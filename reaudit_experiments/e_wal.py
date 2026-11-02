"""WAL / recovery experiments. Uses real subprocess SIGKILL, not simulation."""
import os, sys, time, json, shutil, signal, subprocess, tempfile, warnings, struct, textwrap
warnings.simplefilter("ignore")
import logging; logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
from mdrap.ingestlog import IngestLog

def hdr(t): print("\n" + "=" * 78 + "\n" + t + "\n" + "=" * 78)
base = 1_700_000_000.0

# ------------------------------------------------------------ E7
hdr("E7  RawEvent.session_id is not persisted: does replay diverge under session_marker policy?")
def run(session_attr):
    d = tempfile.mkdtemp()
    e = Engine.open(d, config=EngineConfig(fsync_policy="never", session_policy="session_marker"))
    for i, sid in enumerate(["S1", "S1", "S2", "S2"]):      # S1 -> S2 is a session change => epoch reset
        p = {"instrument": "X", "event_type": "QUOTE", "bid": 1.0, "ask": 1.1,
             "exchange_ts": base + i, "sequence": i + 1 if i < 2 else i - 1}   # seq restarts after S2 starts
        ev = RawEvent(source="A", payload=p, receive_timestamp=base + i,
                      session_id=sid if session_attr else None)
        if not session_attr: ev.payload["session_id"] = sid
        e.submit(ev)
    live = (dict(e.state.counts), dict(e.state.epoch_state))
    e.close()
    e2 = Engine.open(d, config=EngineConfig(fsync_policy="never", session_policy="session_marker"))
    rec = (dict(e2.state.counts), dict(e2.state.epoch_state))
    e2.close(); shutil.rmtree(d, ignore_errors=True)
    return live, rec
for mode, flag in (("session_id inside payload", False), ("session_id as RawEvent attribute", True)):
    live, rec = run(flag)
    print(f"{mode:36s} live={live}\n{'':36s} recovered={rec}  ->", "SAME" if live == rec else "DIVERGED")

# ------------------------------------------------------------ E8
hdr("E8  grouped_by_time: ACKed-but-not-fsynced window when traffic goes quiet")
d = tempfile.mkdtemp()
log = IngestLog(d, fsync_policy="grouped_by_time")
log.append(RawEvent(source="A", payload={"n": 0}, receive_timestamp=base))
time.sleep(0.06)                     # exceed the 50ms group window
off = log.append(RawEvent(source="A", payload={"n": 1}, receive_timestamp=base + 1))   # this one triggers fsync
off2 = log.append(RawEvent(source="A", payload={"n": 2}, receive_timestamp=base + 2))  # ACKed, within window: no fsync
print(f"last ACKed offset={off2}  last fsynced offset={log._synced_offset}")
time.sleep(2.0)                      # market goes quiet for 2 s; no further appends
print(f"after 2.0 s of silence      last fsynced offset={log._synced_offset}  -> unsynced ACKed events remain:", off2 > log._synced_offset)
log.close(); shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------ E9
hdr("E9  SIGKILL mid-stream: acknowledged events recovered? (process crash, page cache intact)")
writer = textwrap.dedent("""
    import sys, time, warnings, logging; warnings.simplefilter('ignore'); logging.disable(logging.CRITICAL)
    from mdrap import Engine, EngineConfig, RawEvent
    d, pol, ackf = sys.argv[1], sys.argv[2], sys.argv[3]
    e = Engine.open(d, config=EngineConfig(fsync_policy=pol))
    base = 1_700_000_000.0
    acked = open(ackf, 'w', buffering=1)
    i = 0
    while True:
        i += 1
        dec = e.submit(RawEvent(source='A', payload={'instrument':'X','event_type':'QUOTE','bid':1.0,'ask':1.1,
                                'exchange_ts': base+i, 'sequence': i}, receive_timestamp=base+i))
        acked.write(f"{dec.offset}\\n")     # printed only AFTER submit() returned (= ACK)
""")
writer_script = os.path.join(tempfile.gettempdir(), "_writer.py")
open(writer_script, "w").write(writer)
for pol in ("always", "grouped_by_time", "never"):
    d = tempfile.mkdtemp(); ackf = d + "/acked.txt"; os.makedirs(d, exist_ok=True)
    p = subprocess.Popen([sys.executable, writer_script, d + "/wal", pol, ackf])
    time.sleep(1.5); p.kill(); p.wait()
    acked = [int(x) for x in open(ackf).read().split() if x.strip()]
    e2 = Engine.open(d + "/wal", config=EngineConfig(fsync_policy="always"))
    rec = e2.state.event_count
    print(f"policy={pol:16s} ACKed={len(acked):6d}  recovered={rec:6d}  lost_acked={max(0, len(acked) - rec)}  recovery_ms={e2.recovery_metrics.recovery_duration_ms:.0f}")
    e2.close(); shutil.rmtree(d, ignore_errors=True)

# ------------------------------------------------------------ E10
hdr("E10 Corruption: bit-flip in the MIDDLE of a segment (not the tail). What survives?")
d = tempfile.mkdtemp()
e = Engine.open(d, config=EngineConfig(fsync_policy="never", max_segment_bytes=10 * 1024 * 1024))
N = 2000
for i in range(1, N + 1):
    e.submit(RawEvent(source="A", payload={"instrument": "X", "event_type": "QUOTE", "bid": 1.0, "ask": 1.1,
                      "exchange_ts": base + i, "sequence": i}, receive_timestamp=base + i))
e.close()
seg = sorted(f for f in os.listdir(d) if f.endswith(".log") or f.endswith(".seg") or "segment" in f or f.startswith("0"))
segpath = None
for root, _, files in os.walk(d):
    for f in files:
        if f.endswith((".log", ".seg", ".wal")) and "manifest" not in f:
            segpath = os.path.join(root, f)
print("segment file:", os.path.basename(segpath), os.path.getsize(segpath), "bytes")
data = bytearray(open(segpath, "rb").read())
mid = len(data) // 2
data[mid] ^= 0xFF
open(segpath, "wb").write(data)
try:
    e3 = Engine.open(d, config=EngineConfig(fsync_policy="never"))
    print(f"opened OK: recovered event_count={e3.state.event_count} of {N} written; corrupted_frames={getattr(e3.log,'corrupted_frames_count',None)}; recovery_metrics={e3.recovery_metrics}")
    e3.close()
except Exception as ex:
    print("open FAILED:", type(ex).__name__, str(ex)[:200])
print("size after open (truncated?):", os.path.getsize(segpath), "bytes (was", len(data), ")")
shutil.rmtree(d, ignore_errors=True)
