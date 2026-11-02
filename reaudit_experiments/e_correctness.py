"""Correctness experiments for the MDRAP v4.0.0 audit.
Each experiment states a hypothesis from code reading and prints evidence.
Runs against an UNMODIFIED copy of the repo (installed editable from /home/claude/run).
"""
import shutil, tempfile, time, json, os, sys, warnings
warnings.simplefilter("ignore")
from mdrap import Engine, EngineConfig, RawEvent, QualityStatus

def mk(src, inst, seq=None, etype="QUOTE", bid=None, ask=None, price=None,
       ts=None, recv=None, **extra):
    now = time.time() if ts is None else ts
    p = {"instrument": inst, "event_type": etype, "exchange_ts": now}
    if seq is not None: p["sequence"] = seq
    if bid is not None: p["bid"] = bid
    if ask is not None: p["ask"] = ask
    if price is not None: p["price"] = price
    p.update(extra)
    return RawEvent(source=src, payload=p, receive_timestamp=(recv or now))

def fresh(**cfg):
    d = tempfile.mkdtemp(prefix="mdrap_exp_")
    c = EngineConfig(fsync_policy="never", **cfg)
    return d, Engine.open(d, config=c)

def hdr(t): print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)

# ---------------------------------------------------------------- E1
hdr("E1  Oversize payload: is live state == recovered state?")
d, e = fresh(max_payload_bytes=200)
base = 1_700_000_000.0
e.submit(mk("A", "X", seq=1, bid=10.0, ask=10.1, ts=base))
big = mk("A", "X", seq=2, bid=10.0, ask=10.1, ts=base+1, blob="z" * 1000)
dec_big = e.submit(big)
dec_next = e.submit(mk("A", "X", seq=3, bid=10.0, ask=10.1, ts=base+2))
print("oversize decision : offset=%s status=%s event_id=%s" % (dec_big.offset, dec_big.quality_status.name, dec_big.event_id))
print("next-event decision: offset=%s status=%s event_id=%s" % (dec_next.offset, dec_next.quality_status.name, dec_next.event_id))
live = (e.state.event_count, dict(e.state.counts))
wal_records = sum(1 for _ in e.log.iter_from(0))
e.close()
e2 = Engine.open(d, config=EngineConfig(fsync_policy="never", max_payload_bytes=200))
rec = (e2.state.event_count, dict(e2.state.counts))
print("WAL records on disk :", wal_records)
print("LIVE      state     :", live)
print("RECOVERED state     :", rec)
print("=> DIVERGED" if live != rec else "=> identical")
e2.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E2
hdr("E2  Sequence poisoning: one bogus huge seq, then legit traffic")
d, e = fresh()
out = []
for s in range(1, 6):
    out.append(e.submit(mk("A", "X", seq=s, bid=10, ask=10.1, ts=base + s)).quality_status.name)
poison = e.submit(mk("A", "X", seq=2**62, bid=10, ask=10.1, ts=base + 6))
print("poison seq=2**62  ->", poison.quality_status.name, poison.reasons)
after = [e.submit(mk("A", "X", seq=s, bid=10, ask=10.1, ts=base + 6 + s)) for s in range(6, 16)]
print("legit seq 6..15   ->", [x.quality_status.name for x in after])
print("reasons (first 3) ->", [x.reasons for x in after[:3]])
print("canonical emitted for poisoned-stream events:", sum(1 for x in after if x.canonical_event))
e.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E3
hdr("E3  Retransmitted OLD sequence (e.g. A/B line arbitration, MoldUDP64 replay)")
d, e = fresh()
for s in range(1, 11):
    e.submit(mk("A", "X", seq=s, bid=10, ask=10.1, ts=base + s))
r_last = e.submit(mk("A", "X", seq=10, bid=10, ask=10.1, ts=base + 11))
r_old = e.submit(mk("A", "X", seq=5, bid=10, ask=10.1, ts=base + 12))
r_old2 = e.submit(mk("A", "X", seq=5, bid=10, ask=10.1, ts=base + 13))
print("re-send seq=10 (== last) ->", r_last.quality_status.name, r_last.reasons, "emitted:", bool(r_last.canonical_event))
print("re-send seq=5  (old)     ->", r_old.quality_status.name, r_old.reasons, "emitted:", bool(r_old.canonical_event))
print("re-send seq=5 AGAIN      ->", r_old2.quality_status.name, r_old2.reasons, "emitted:", bool(r_old2.canonical_event))
e.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E4
hdr("E4  Price corridor after a REAL regime shift (gap open / news / re-open)")
d, e = fresh()
t = base
for i in range(30):
    t += 0.01
    e.submit(mk("A", "Y", etype="TRADE", price=100.0 + (i % 3) * 0.01, seq=i + 1, ts=t))
statuses = []
for i in range(60):
    t += 0.01
    dec = e.submit(mk("A", "Y", etype="TRADE", price=120.0 + (i % 3) * 0.01, seq=100 + i, ts=t))
    statuses.append(dec.quality_status.name)
from collections import Counter
print("30 trades @~100, then 60 trades @~120 (genuine +20% re-rate)")
print("verdicts on the 60 post-shift trades:", dict(Counter(statuses)))
print("last 10:", statuses[-10:])
e.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E5
hdr("E5  Sequence-less dedup fingerprint ignores sizes")
d, e = fresh()
a = e.submit(mk("A", "Z", bid=50.0, ask=50.1, ts=base, bid_size=100, ask_size=100))
b = e.submit(mk("A", "Z", bid=50.0, ask=50.1, ts=base, bid_size=900, ask_size=100))
print("quote #1 sizes 100/100 ->", a.quality_status.name, a.reasons)
print("quote #2 sizes 900/100 (different book state, same ts & prices) ->", b.quality_status.name, b.reasons, "emitted:", bool(b.canonical_event))
e.close(); shutil.rmtree(d, ignore_errors=True)

# ---------------------------------------------------------------- E6
hdr("E6  on_gap hook re-fires during crash-recovery replay?")
calls = []
def hook(*a): calls.append(a)
d = tempfile.mkdtemp(prefix="mdrap_exp_")
e = Engine.open(d, config=EngineConfig(fsync_policy="never", on_gap=hook))
for s in (1, 2, 3, 10):   # gap 4..9
    e.submit(mk("A", "G", seq=s, bid=10, ask=10.1, ts=base + s))
print("hook calls during live run   :", len(calls))
e.close()
calls.clear()
e2 = Engine.open(d, config=EngineConfig(fsync_policy="never", on_gap=hook))
print("hook calls during RECOVERY   :", len(calls), "(no new traffic was submitted)")
print("gap_manager.stats() after recovery:", e2.gap_manager.stats())
e2.close(); shutil.rmtree(d, ignore_errors=True)
