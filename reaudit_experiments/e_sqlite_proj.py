import sqlite3, threading, time, tempfile, shutil, os, warnings, logging
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
from mdrap.projection import SQLiteProjection
base=1_700_000_000.0
def ev(i): return RawEvent(source="A",payload={"instrument":"X","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i,"sequence":i},receive_timestamp=base+i)

print("=== A. external writer holds the SQLite write lock for 3 s (e.g. a backup job or long reader/writer)")
d=tempfile.mkdtemp(); db=os.path.join(d,"p.db")
proj=SQLiteProjection(db); e=Engine.open(d+"/wal",config=EngineConfig(fsync_policy="never",projections=[proj]))
for i in (1,2,3): e.submit(ev(i))
other=sqlite3.connect(db,isolation_level=None,check_same_thread=False); other.execute("BEGIN IMMEDIATE")
threading.Timer(3.0, lambda: other.execute("COMMIT")).start()
fast=[]
stop=threading.Event()
def other_submit():                      # a second feed thread: any submit() contends for the same engine lock
    t0=time.perf_counter(); e.submit(RawEvent(source="B",payload={"instrument":"Y","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base,"sequence":1},receive_timestamp=base)); fast.append(time.perf_counter()-t0)
t0=time.perf_counter(); th=threading.Thread(target=other_submit); 
e.submit(ev(4)) if False else None
t_main=[]; 
def main_submit():
    t=time.perf_counter(); e.submit(ev(4)); t_main.append(time.perf_counter()-t)
m=threading.Thread(target=main_submit); m.start(); time.sleep(0.2); th.start(); m.join(); th.join()
print(f"  submit() that hit the locked DB took      : {t_main[0]:6.2f} s")
print(f"  UNRELATED feed's submit() (other source) : {fast[0]:6.2f} s   <- head-of-line blocking across feeds")
e.close(); shutil.rmtree(d,ignore_errors=True)

print("\n=== B. projection fails for 3 batches then recovers: what does metrics() say?")
class Flaky(SQLiteProjection):
    fail=set()
    def apply(self,batch,offset,**k):
        if offset in self.fail: raise sqlite3.OperationalError("disk I/O error")
        return super().apply(batch,offset,**k)
d=tempfile.mkdtemp(); p=Flaky(os.path.join(d,"p.db")); e=Engine.open(d+"/wal",config=EngineConfig(fsync_policy="never",projections=[p]))
p.fail={3,4,5}
for i in range(1,9): e.submit(ev(i))
m=e.metrics()
print("  events in WAL / engine state :", e.state.event_count)
print("  rows in projection table     :", p.count_canonical(), "  <- missing", e.state.event_count-p.count_canonical())
print("  projection checkpoint        :", p.checkpoint(), "(= last offset, looks fully caught up)")
print("  metrics() health / lag       :", {k:m.get(k) for k in ("health","health_status","max_projection_lag","projection_lag","projections") if k in m} or {k:v for k,v in m.items() if "health" in k or "lag" in k or "proj" in k})
e.close(); shutil.rmtree(d,ignore_errors=True)
