import os, json, time, tempfile, shutil, warnings, logging, io, glob
warnings.simplefilter("ignore")
import mdrap
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
def ev(i,inst="X"): return RawEvent(source="A",payload={"instrument":inst,"event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i,"sequence":i},receive_timestamp=base+i)
print("build:", mdrap.__file__)
logging.disable(logging.CRITICAL)

print("\n--- R1. grouped_by_time on the ENGINE path: ACKed vs fsynced after quiet period")
d=tempfile.mkdtemp(); e=Engine.open(d,config=EngineConfig(fsync_policy="grouped_by_time"))
for i in range(1,4): e.submit(ev(i)); time.sleep(0.07)       # steady trickle, each > 50 ms apart
dec=e.submit(ev(4)); time.sleep(0.01); dec2=e.submit(ev(5))  # last two inside one 50 ms window
time.sleep(2.0)
print(f"  last ACKed offset={dec2.offset}  log.synced_offset after 2 s of silence={e.log.synced_offset}  -> unsynced ACKed events: {dec2.offset > e.log.synced_offset}")
e.close(); shutil.rmtree(d,ignore_errors=True)

print("\n--- R2. snapshot prunes WAL; then snapshot is corrupted. What does recovery do?")
logging.disable(logging.NOTSET); buf=io.StringIO(); h=logging.StreamHandler(buf); logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.WARNING)
d=tempfile.mkdtemp(); cfg=lambda: EngineConfig(fsync_policy="never",max_segment_bytes=60_000,snapshot_cadence_events=10**9,snapshot_cadence_seconds=1e9)
e=Engine.open(d,config=cfg())
for i in range(1,3001): e.submit(ev(i))
segs_before=len(glob.glob(d+"/segment_*.log")); info=e.save_snapshot()
segs_after=len(glob.glob(d+"/segment_*.log"))
for i in range(3001,3101): e.submit(ev(i))
live_n=e.state.event_count; e.close()
print(f"  segments before/after snapshot prune: {segs_before} -> {segs_after}   (live event_count={live_n})")
sp=d+"/snapshot.json"; j=json.load(open(sp)); j["state"]["counts"]["VALID"]+=1          # tamper: SHA no longer matches
json.dump(j,open(sp,"w"))
try:
    e2=Engine.open(d,config=cfg()); n=e2.state.event_count
    print(f"  reopened WITHOUT error: recovered event_count={n} of {live_n}  ({live_n-n} events silently missing)  counts={dict(e2.state.counts)}")
    e2.close()
except Exception as x:
    print("  open raised (fail-closed):", type(x).__name__, str(x)[:150])
print("  warnings logged:", [l for l in buf.getvalue().splitlines() if l.strip()][:3] or "none")
shutil.rmtree(d,ignore_errors=True)
