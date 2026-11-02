import threading, time, tempfile, shutil, warnings, logging, sys
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
import mdrap
from mdrap import Engine, EngineConfig, RawEvent
print("build:", mdrap.__file__)
base = 1_700_000_000.0
NINST = 10000
d = tempfile.mkdtemp()
e = Engine.open(d, config=EngineConfig(fsync_policy="never", snapshot_cadence_events=10**9, snapshot_cadence_seconds=1e9))
seqs = {}
def ev(k):
    inst = f"I{k % NINST}"; seqs[inst] = seqs.get(inst, 0) + 1; t = base + k * 1e-3
    return RawEvent(source="A", payload={"instrument": inst, "event_type": "TRADE", "price": 100 + (k % 5) * .01, "quantity": 1,
                    "exchange_ts": t, "sequence": seqs[inst]}, receive_timestamp=t)
for k in range(60000): e.submit(ev(k))
stop = threading.Event(); lat = []; marks = {}
def submitter():
    k = 60000
    while not stop.is_set():
        x = ev(k); k += 1
        t0 = time.perf_counter_ns(); e.submit(x); t1 = time.perf_counter_ns()
        lat.append((t0, t1 - t0))
th = threading.Thread(target=submitter); th.start()
time.sleep(1.0)
s0 = time.perf_counter_ns(); e.save_snapshot(); s1 = time.perf_counter_ns()
time.sleep(1.0); stop.set(); th.join()
in_win = [l for t0, l in lat if s0 <= t0 <= s1]
out_win = [l for t0, l in lat if t0 < s0 or t0 > s1]
mx_in = max(in_win) / 1e6 if in_win else float("nan")
# worst gap between consecutive completed submits that overlaps the snapshot window (what a feed would feel)
ends = [t0 + l for t0, l in lat]
gaps = [(ends[i+1] - ends[i]) / 1e6 for i in range(len(ends) - 1) if ends[i+1] >= s0 and ends[i] <= s1]
print(f"snapshot call duration            : {(s1 - s0)/1e6:8.1f} ms  (10,000 instruments)")
print(f"submits completed during snapshot : {len(in_win)}")
print(f"max submit latency during snapshot: {mx_in:8.1f} ms")
print(f"worst gap between completed submits overlapping snapshot: {max(gaps) if gaps else float('nan'):8.1f} ms")
print(f"max submit latency outside window : {max(out_win)/1e6:8.2f} ms   p99.9 outside: {sorted(out_win)[int(len(out_win)*0.999)]/1e3:8.1f} us")
e.close(); shutil.rmtree(d, ignore_errors=True)
