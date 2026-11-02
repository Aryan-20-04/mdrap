import os, time, gc, json, tempfile, shutil, warnings, logging, statistics, sys
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
base = 1_700_000_000.0

def hdr(t): print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)
def raw(i, inst="X", extra=None, src="A"):
    p = {"instrument": inst, "event_type": "QUOTE", "bid": 100.0, "ask": 100.01,
         "exchange_ts": base + i * 1e-3, "sequence": i}
    if extra: p.update(extra)
    return RawEvent(source=src, payload=p, receive_timestamp=base + i * 1e-3)

# ---------------------------------------------------------------- E11
hdr("E11 Snapshot offset = event_count-1, but oversize rejects inflate event_count -> recovery skips WAL records?")
d = tempfile.mkdtemp()
cfg = lambda: EngineConfig(fsync_policy="never", max_payload_bytes=300, snapshot_cadence_events=10**9, snapshot_cadence_seconds=1e9)
e = Engine.open(d, config=cfg())
i = 0
def sub(extra=None):
    global i; i += 1; return e.submit(raw(i, extra=extra))
for _ in range(5): sub()
for _ in range(3): sub({"blob": "z" * 1000})       # 3 oversize rejects (NOT in WAL)
for _ in range(2): sub()
snap = e.save_snapshot()
for _ in range(3): sub()                            # 3 more valid events AFTER the snapshot
live_n = e.state.event_count; live_counts = dict(e.state.counts)
wal_n = sum(1 for _ in e.log.iter_from(0))
e.close()
e2 = Engine.open(d, config=cfg())
print(f"WAL records={wal_n}  snapshot offset={snap.get('offset')}")
print(f"LIVE      event_count={live_n} counts={live_counts}")
print(f"RECOVERED event_count={e2.state.event_count} counts={dict(e2.state.counts)} replayed={getattr(e2.recovery_metrics,'events_replayed',None)}")
print("seq state live vs recovered last seq:", "see counts above")
e2.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- latency helpers
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(len(xs) * p / 100))]
def summarize(name, ns):
    us = [x / 1000 for x in ns]
    print(f"{name:44s} n={len(us):6d} p50={pct(us,50):9.1f} p90={pct(us,90):9.1f} p99={pct(us,99):9.1f} p99.9={pct(us,99.9):10.1f} max={max(us):11.1f} us")

def bench(label, config, n=20000, warm=2000, instruments=50, **kw):
    d = tempfile.mkdtemp(); e = Engine.open(d, config=config)
    seqs = {}
    def ev(k):
        inst = f"I{k % instruments}"; seqs[inst] = seqs.get(inst, 0) + 1
        t = base + k * 1e-3
        return RawEvent(source="A", payload={"instrument": inst, "event_type": "QUOTE", "bid": 100.0 + (k % 7) * .01,
                        "ask": 100.2 + (k % 7) * .01, "exchange_ts": t, "sequence": seqs[inst]}, receive_timestamp=t)
    for k in range(warm): e.submit(ev(k))
    gc.collect(); gc.disable()
    lat = []
    for k in range(warm, warm + n):
        x = ev(k); t0 = time.perf_counter_ns(); e.submit(x); lat.append(time.perf_counter_ns() - t0)
    gc.enable(); summarize(label, lat); e.close(); shutil.rmtree(d, ignore_errors=True); return lat

hdr("E12 Engine.submit() per-event latency (closed loop, single thread, gc disabled, 2k warm-up) -- RELATIVE numbers only")
big = dict(snapshot_cadence_events=10**9, snapshot_cadence_seconds=1e9)
bench("WAL fsync=never, no projection", EngineConfig(fsync_policy="never", **big))
bench("WAL fsync=grouped_by_time(50ms)", EngineConfig(fsync_policy="grouped_by_time", **big), n=5000)
bench("WAL fsync=always (default)", EngineConfig(fsync_policy="always", **big), n=3000, warm=300)
d_db = tempfile.mkdtemp()
bench("WAL never + SQLite projection (db_path)", EngineConfig(fsync_policy="never", db_path=d_db + "/m.db", **big), n=5000, warm=500)

hdr("E13 Periodic snapshot stall (default cadence 50,000 events) as state grows")
for ninst in (100, 2000, 10000):
    d = tempfile.mkdtemp(); e = Engine.open(d, config=EngineConfig(fsync_policy="never", **big))
    seqs = {}
    for k in range(max(30000, ninst * 6)):
        inst = f"I{k % ninst}"; seqs[inst] = seqs.get(inst, 0) + 1
        t = base + k * 1e-3
        e.submit(RawEvent(source="A", payload={"instrument": inst, "event_type": "TRADE", "price": 100 + (k % 5) * .01, "quantity": 1,
                    "exchange_ts": t, "sequence": seqs[inst]}, receive_timestamp=t))
    t0 = time.perf_counter(); e.save_snapshot(); dt = (time.perf_counter() - t0) * 1000
    sz = os.path.getsize(os.path.join(d, "snapshot.json"))
    print(f"instruments={ninst:6d}  state events={e.state.event_count:6d}  save_snapshot()={dt:8.1f} ms  (blocks submit(): lock held)  file={sz/1e6:.2f} MB")
    e.close(); shutil.rmtree(d, ignore_errors=True)

hdr("E14 Hot-path overhead of the max_payload_bytes check (json.dumps of every dict payload)")
p = {"instrument": "X", "event_type": "QUOTE", "bid": 100.0, "ask": 100.2, "exchange_ts": base, "sequence": 1}
N = 200000; t0 = time.perf_counter_ns()
for _ in range(N): len(json.dumps(p, default=str))
per = (time.perf_counter_ns() - t0) / N
print(f"json.dumps(small quote payload) = {per/1000:.2f} us/event  (pure overhead; WAL then serializes the same payload again)")
