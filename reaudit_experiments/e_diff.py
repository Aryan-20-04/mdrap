import sys, time, tempfile, shutil, warnings, logging
warnings.simplefilter("ignore"); logging.disable(logging.CRITICAL)
from collections import Counter
from mdrap import Engine, EngineConfig, RawEvent
from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.quality import QualityEngine
from mdrap.fastpath import FastQualityEngine
import mdrap.fastpath as fp
print("native C tier active in FastQualityEngine:", getattr(FastQualityEngine(), "_native_available", getattr(fp, "_NATIVE_AVAILABLE", "?")))

base = 1_700_000_000.0
def scen_poison():
    ev = [(s, "QUOTE", None) for s in range(1, 6)] + [(2**62, "QUOTE", None)] + [(s, "QUOTE", None) for s in range(6, 16)]
    return ev
def scen_retx():
    return [(s, "QUOTE", None) for s in range(1, 11)] + [(10, "QUOTE", None), (5, "QUOTE", None), (5, "QUOTE", None)]
def scen_regime():
    return [(i + 1, "TRADE", 100.0 + (i % 3) * 0.01) for i in range(30)] + [(100 + i, "TRADE", 120.0 + (i % 3) * 0.01) for i in range(60)]
def scen_gap_late():
    return [(1, "QUOTE", None), (2, "QUOTE", None), (6, "QUOTE", None), (3, "QUOTE", None), (4, "QUOTE", None), (7, "QUOTE", None)]

def mk_raw(i, seq, et, price):
    t = base + i * 0.01
    p = {"instrument": "X", "event_type": et, "exchange_ts": t, "sequence": seq}
    if et == "QUOTE": p.update(bid=10.0, ask=10.1)
    else: p["price"] = price; p["quantity"] = 1
    return RawEvent(source="A", payload=p, receive_timestamp=t)

def mk_canon(i, seq, et, price):
    t = base + i * 0.01
    kw = dict(event_id=f"e{i}", instrument_id="X", event_type=EventType.QUOTE if et == "QUOTE" else EventType.TRADE,
              exchange_timestamp=t, receive_timestamp=t, source="A", sequence_number=seq, raw_id=f"r{i}")
    if et == "QUOTE": kw.update(bid_price=10.0, ask_price=10.1)
    else: kw.update(price=price, quantity=1.0)
    return CanonicalEvent(**kw)

def run_engine(ev):
    d = tempfile.mkdtemp()
    e = Engine.open(d, config=EngineConfig(fsync_policy="never"))
    out = [e.submit(mk_raw(i, *x)).quality_status.name[0] for i, x in enumerate(ev)]
    e.close(); shutil.rmtree(d, ignore_errors=True); return "".join(out)
def run_q(cls, ev):
    q = cls(); out = []
    for i, x in enumerate(ev):
        r = q.evaluate(mk_canon(i, *x))
        out.append("X" if r is None else r.quality_status.name[0])
    return "".join(out)

for name, sc in (("A. poisoned sequence", scen_poison), ("B. retransmit old seq", scen_retx),
                 ("C. genuine +20% regime shift", scen_regime), ("D. late fill of a gap", scen_gap_late)):
    ev = sc()
    print(f"\n{name}   (V=VALID S=SUSPICIOUS I=INVALID X=dropped)")
    for lab, fn in (("Engine (inline)   ", lambda: run_engine(ev)),
                    ("QualityEngine(py)", lambda: run_q(QualityEngine, ev)),
                    ("FastQualityEngine", lambda: run_q(FastQualityEngine, ev))):
        s = fn(); print(f"  {lab}: {s if len(s) <= 40 else s[:20] + '...' + s[-20:]}   {dict(Counter(s))}")
