import tempfile, shutil, warnings, logging
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
class Good:
    def __init__(s): s.rows=0
    def apply(s,batch,offset): s.rows+=len(batch)
class Flaky:
    def __init__(s): s.fail=False
    def apply(s,batch,offset):
        if s.fail: raise RuntimeError("projection down")
g,f=Good(),Flaky()
d=tempfile.mkdtemp()
e=Engine.open(d,config=EngineConfig(fsync_policy="never",projections=[g,f]))
def ev(i): return RawEvent(source="A",payload={"instrument":"X","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i,"sequence":i},receive_timestamp=base+i)
for i in (1,2,3): e.submit(ev(i))
f.fail=True
try: e.submit(ev(4)); print("submit(4): no exception raised")
except Exception as x: print("submit(4) raised to caller:", type(x).__name__, x)
f.fail=False
print("after failure: live event_count =", e.state.event_count, "| WAL records =", sum(1 for _ in e.log.iter_from(0)), "| 'good' projection rows =", g.rows)
try:
    r=e.submit(ev(4)); print("caller RETRY of seq=4 ->", r.quality_status.name, r.reasons)
except Exception as x: print("retry raised", x)
e.close()
e2=Engine.open(d,config=EngineConfig(fsync_policy="never"))
print("RECOVERED event_count =", e2.state.event_count, "counts =", dict(e2.state.counts), "(WAL holds the event twice)")
e2.close(); shutil.rmtree(d,ignore_errors=True)
