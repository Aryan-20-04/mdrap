"""Randomised stateful test: after any sequence of operations, state recovered from disk must equal live state."""
import random, tempfile, shutil, sys, warnings, logging, collections
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
import mdrap
from mdrap import Engine, EngineConfig, RawEvent
base = 1_700_000_000.0
def cfg(): return EngineConfig(fsync_policy="never", max_payload_bytes=400, snapshot_cadence_events=10**9, snapshot_cadence_seconds=1e9)
def norm(s):
    import json; d = json.loads(json.dumps(s.to_dict(), sort_keys=True)); d.pop("staleness_threshold_s", None); return d   # normalise tuple/list
def trial(seed, allow_oversize=True, allow_snapshot=True):
    r = random.Random(seed); d = tempfile.mkdtemp()
    e = Engine.open(d, config=cfg()); seq = collections.defaultdict(int); px = {}; t = base; ops = []
    for _ in range(r.randint(5, 60)):
        c = r.random()
        if c < 0.08 and allow_snapshot and e.log is not None and e.state.event_count > 0:
            e.save_snapshot(); ops.append("snap"); continue
        batch = []
        for _ in range(r.randint(1, 4)):
            src = r.choice("AB"); inst = r.choice(["X", "Y", "Z"]); k = (src, inst); t += r.random() * 0.01
            m = r.random()
            if m < 0.08: s = max(1, seq[k] - r.randint(0, 5))            # duplicate / old
            elif m < 0.14: s = seq[k] + r.randint(2, 6)                   # gap
            elif m < 0.16: s = 2**40                                       # implausible jump
            else: s = seq[k] + 1
            seq[k] = max(seq[k], s) if m >= 0.14 or m < 0.08 else s
            p0 = px.get(k, 100.0); p0 *= (1.2 if r.random() < 0.03 else 1 + r.uniform(-.0005, .0005)); px[k] = p0
            pay = {"instrument": inst, "exchange_ts": t, "sequence": s}
            if r.random() < 0.5: pay.update(event_type="TRADE", price=p0, quantity=1.0)
            else: pay.update(event_type="QUOTE", bid=p0 - .01, ask=p0 + .01, bid_size=r.randint(1, 5), ask_size=r.randint(1, 5))
            if allow_oversize and r.random() < 0.06: pay["blob"] = "z" * 600
            batch.append(RawEvent(source=src, payload=pay, receive_timestamp=t))
        e.submit(batch if len(batch) > 1 else batch[0]); ops.append(f"sub{len(batch)}")
        if r.random() < 0.05:
            live = norm(e.state); e.close(); e = Engine.open(d, config=cfg()); ops.append("restart")
            diff = [k for k in live if live[k] != norm(e.state)[k]]
            if diff: shutil.rmtree(d, ignore_errors=True); return diff, ops
    live = norm(e.state); e.close(); e2 = Engine.open(d, config=cfg()); rec = norm(e2.state); e2.close(); shutil.rmtree(d, ignore_errors=True)
    return [k for k in live if live[k] != rec[k]], ops
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
print("build:", mdrap.__file__)
for label, kw in (("ALL operations (oversize + snapshot + restart)", {}),
                  ("no oversize payloads", {"allow_oversize": False}),
                  ("no oversize, no snapshots (pure WAL replay)", {"allow_oversize": False, "allow_snapshot": False})):
    bad = collections.Counter(); fails = 0
    for s in range(N):
        diff, ops = trial(s, **kw)
        if diff: fails += 1; bad.update(diff)
    print(f"{label:50s} diverged {fails:3d}/{N}   fields: {dict(bad)}")
