import tempfile, shutil, warnings, logging
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
class Rec:
    def __init__(s): s.offsets=[]; s.fail=False
    def apply(s,batch,offset):
        if s.fail: raise RuntimeError("transient outage")
        s.offsets.append(offset)
good, flaky = Rec(), Rec()
d=tempfile.mkdtemp(); e=Engine.open(d,config=EngineConfig(fsync_policy="never",projections=[good,flaky]))
def ev(i): return RawEvent(source="A",payload={"instrument":"X","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i,"sequence":i},receive_timestamp=base+i)
for i in (1,2,3): e.submit(ev(i))
flaky.fail=True
for i in (4,5,6):
    try: r=e.submit(ev(i)); print(f"submit({i}) under outage -> returned {r.quality_status.name}, no exception")
    except Exception as x: print(f"submit({i}) raised {type(x).__name__}")
flaky.fail=False
for i in (7,8): e.submit(ev(i))
print("WAL offsets 0..7 exist. 'good' projection saw :", good.offsets)
print("'flaky' projection (recovered) saw          :", flaky.offsets, "<- missing", sorted(set(good.offsets)-set(flaky.offsets)))
print("any engine-visible signal of the lost batches? ->",
      [a for a in dir(e) if any(w in a.lower() for w in ("projection_fail","dead","lag","dropped_proj","proj_err"))] or "none found by attribute scan")
e.close(); shutil.rmtree(d,ignore_errors=True)
