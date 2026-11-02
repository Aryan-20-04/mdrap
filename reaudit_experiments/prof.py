import cProfile, pstats, tempfile, warnings, logging, io, shutil
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from mdrap import Engine, EngineConfig, RawEvent
base=1_700_000_000.0
def run(pol):
    d=tempfile.mkdtemp(); e=Engine.open(d,config=EngineConfig(fsync_policy=pol,snapshot_cadence_events=10**9,snapshot_cadence_seconds=1e9))
    evs=[RawEvent(source="A",payload={"instrument":"X","event_type":"QUOTE","bid":1.0,"ask":1.1,"exchange_ts":base+i*1e-3,"sequence":i+1},receive_timestamp=base+i*1e-3) for i in range(3000)]
    pr=cProfile.Profile(); pr.enable()
    for x in evs: e.submit(x)
    pr.disable(); s=io.StringIO(); pstats.Stats(pr,stream=s).sort_stats("tottime").print_stats(5)
    print("=== policy:",pol); print("\n".join(l for l in s.getvalue().splitlines() if l.strip())[:900]); e.close(); shutil.rmtree(d,ignore_errors=True)
run("never"); run("grouped_by_time")
