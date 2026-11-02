import time, gc, tempfile, shutil, warnings, logging, statistics, sys
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
def run(policy, n, warm, ninst=50):
    d=tempfile.mkdtemp(); e=Engine.open(d,config=EngineConfig(fsync_policy=policy,snapshot_cadence_events=10**9,snapshot_cadence_seconds=1e9)); seqs={}
    def ev(k):
        inst=f"I{k%ninst}"; seqs[inst]=seqs.get(inst,0)+1; t=base+k*1e-3
        return RawEvent(source="A",payload={"instrument":inst,"event_type":"QUOTE","bid":100.0+(k%7)*.01,"ask":100.2+(k%7)*.01,"exchange_ts":t,"sequence":seqs[inst]},receive_timestamp=t)
    for k in range(warm): e.submit(ev(k))
    gc.collect(); gc.disable(); lat=[]
    evs=[ev(k) for k in range(warm,warm+n)]
    for x in evs:
        t0=time.perf_counter_ns(); e.submit(x); lat.append(time.perf_counter_ns()-t0)
    gc.enable(); e.close(); shutil.rmtree(d,ignore_errors=True); lat.sort()
    return lat[len(lat)//2]/1e3, lat[int(len(lat)*.99)]/1e3, lat[int(len(lat)*.999)]/1e3
out=[]
for pol,n,w in (("never",15000,2000),("grouped_by_time",5000,500)):
    out.append((pol,run(pol,n,w)))
print(sys.argv[1], " ".join(f"{p}:p50={a:.1f}/p99={b:.1f}/p99.9={c:.0f}us" for p,(a,b,c) in out))
