import tempfile, shutil, warnings, logging
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
import mdrap
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
def ev(i,**x):
    p={"instrument":"X","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i,"sequence":i}; p.update(x)
    return RawEvent(source="A",payload=p,receive_timestamp=base+i)
print("build:", mdrap.__file__)
d=tempfile.mkdtemp(); e=Engine.open(d,config=EngineConfig(fsync_policy="never"))
for i in (1,2): e.submit(ev(i))
before=(e.state.event_count, e.log.next_offset)
try: e.submit(ev(3, wire=b"\x01\x02")); print("bytes payload: accepted")
except Exception as x: print("bytes payload: submit() RAISED", type(x).__name__)
after=(e.state.event_count, e.log.next_offset, e.log._is_poisoned if hasattr(e.log,"_is_poisoned") else None)
print("state (event_count, next_offset) before:", before, " after:", after[:2], " log poisoned:", after[2])
r=e.submit(ev(3)); print("next normal event (seq 3):", r.quality_status.name, r.reasons)
# batch containing one poison event: do the good events in the same batch get lost?
try: e.submit([ev(4), ev(5, wire=b"\x00"), ev(6)]); print("batch w/ one bytes event: accepted")
except Exception as x: print("batch w/ one bytes event: RAISED", type(x).__name__)
print("after batch: event_count =", e.state.event_count, " next_offset =", e.log.next_offset, " (events 4 and 6 were submitted, valid)")
e.close()
e2=Engine.open(d,config=EngineConfig(fsync_policy="never")); print("recovered event_count =", e2.state.event_count); e2.close(); shutil.rmtree(d,ignore_errors=True)
