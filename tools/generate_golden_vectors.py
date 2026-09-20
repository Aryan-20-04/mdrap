"""Generate tests/golden/quality_vectors.jsonl with >=300 comprehensive test vectors.

Covers:
- Clean trades and quotes
- Structural validations (null, zero, negative, NaN, Inf, missing fields)
- Crossed quotes (bid >= ask)
- Sequence gaps, duplicates, out-of-order, resync
- Timestamp staleness and future timestamp plausibility
- Price anomalies, warm-up boundaries, and regime shift re-seeding
- Multi-source independent sequence and baseline isolation
- Combined multi-fault vectors
"""

import json
import math
import os


def generate_vectors(out_path: str = "tests/golden/quality_vectors.jsonl"):
    vectors = []
    vid = 0

    def add(
        instrument="AAPL",
        event_type="TRADE",
        ex_ts=1000.0,
        recv_ts=1000.001,
        source="FEEDX",
        seq=None,
        price=None,
        qty=None,
        bid=None,
        bid_sz=None,
        ask=None,
        ask_sz=None,
    ):
        nonlocal vid
        vid += 1
        vectors.append(
            {
                "event_id": f"vec-{vid:04d}",
                "instrument_id": instrument,
                "event_type": event_type,
                "exchange_timestamp": ex_ts,
                "receive_timestamp": recv_ts,
                "processing_timestamp": recv_ts + 0.0001,
                "source": source,
                "sequence_number": seq,
                "price": price,
                "quantity": qty,
                "bid_price": bid,
                "bid_size": bid_sz,
                "ask_price": ask,
                "ask_size": ask_sz,
            }
        )

    # 1. Warm-up & Clean Trades (1 to 30)
    t = 1000.0
    for s in range(1, 31):
        t += 0.01
        add(
            instrument="AAPL",
            event_type="TRADE",
            ex_ts=t,
            recv_ts=t + 0.001,
            seq=s,
            price=150.0 + (s % 5) * 0.02,
            qty=100.0,
        )

    # 2. Clean Quotes (31 to 60)
    for s in range(31, 61):
        t += 0.01
        add(
            instrument="AAPL",
            event_type="QUOTE",
            ex_ts=t,
            recv_ts=t + 0.001,
            seq=s,
            bid=150.00,
            bid_sz=50.0,
            ask=150.05,
            ask_sz=60.0,
        )

    # 3. Structural Violations on Trades (61 to 85)
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=61, price=None, qty=10.0)  # Missing price
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=62, price=0.0, qty=10.0)   # Zero price
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=63, price=-10.0, qty=10.0) # Negative price
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=64, price=float("nan"), qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=65, price=float("inf"), qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=66, price=150.0, qty=None) # Missing qty
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=67, price=150.0, qty=0.0)  # Zero qty
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=68, price=150.0, qty=-1.0) # Negative qty
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=69, price=150.0, qty=float("nan"))
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=70, price=150.0, qty=float("inf"))

    for i in range(71, 86):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=150.01, qty=10.0)

    # 4. Structural Violations on Quotes (86 to 110)
    t += 0.01
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=86, bid=None, ask=None) # Missing both
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=87, bid=150.0, ask=150.1, bid_sz=0.0)
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=88, bid=150.0, ask=150.1, ask_sz=-1.0)
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=89, bid=float("nan"), ask=150.1)
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=90, bid=150.0, ask=float("inf"))

    for i in range(91, 111):
        t += 0.01
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=i, bid=150.01, ask=150.06, bid_sz=10.0, ask_sz=10.0)

    # 5. Crossed Quotes (111 to 130)
    for i in range(111, 121):
        t += 0.01
        # bid > ask
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=i, bid=150.10 + i * 0.01, ask=150.00, bid_sz=10.0, ask_sz=10.0)

    for i in range(121, 131):
        t += 0.01
        # bid == ask
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=i, bid=150.05, ask=150.05, bid_sz=10.0, ask_sz=10.0)

    # 6. Timestamp Anomalies: Staleness & Implausible Skew (131 to 160)
    for i in range(131, 146):
        t += 0.01
        # Stale: exchange_ts is 2 seconds older than receive_ts (threshold is 0.050s)
        add(instrument="AAPL", event_type="TRADE", ex_ts=t - 2.0, recv_ts=t, seq=i, price=150.02, qty=10.0)

    for i in range(146, 161):
        t += 0.01
        # Future timestamp: exchange_ts is 5 seconds in future (skew limit 1.0s)
        add(instrument="AAPL", event_type="TRADE", ex_ts=t + 5.0, recv_ts=t, seq=i, price=150.02, qty=10.0)

    # 7. Sequence Anomalies: Gaps, Duplicates, Out-of-Order (161 to 210)
    # 161: Normal resume
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=161, price=150.03, qty=10.0)

    # 162: Sequence Gap (161 -> 175)
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=175, price=150.03, qty=10.0)

    # 163 to 170: Duplicate sequences
    for i in range(163, 171):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=175, price=150.03, qty=10.0)

    # 171 to 180: Out of order (within 64-bit window)
    for i, s in enumerate(range(162, 172)):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=150.03, qty=10.0)

    # 181: Re-send 165 (already seen late) -> duplicate
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=165, price=150.03, qty=10.0)

    # 182 to 210: Advance cleanly
    for s in range(182, 211):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=150.04, qty=10.0)

    # 8. Price Anomalies & Regime Shift (211 to 250)
    # Warm baseline is ~150.04.
    # 211 to 215: Price spike anomaly (150 -> 180.0, +20%)
    for i in range(211, 216):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=180.0, qty=10.0)

    # 216 to 220: Return to normal (150.04) -> baseline was NOT poisoned!
    for i in range(216, 221):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=150.04, qty=10.0)

    # 221 to 232: Regime shift test: 9 consecutive events at 170.0 (+13%)
    # First 8 should trigger anomalies, 9th re-seeds and becomes VALID!
    for i in range(221, 233):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=170.0, qty=10.0)

    for i in range(233, 251):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=170.02, qty=10.0)

    # 9. Multi-source Isolation (251 to 300)
    # FEEDY has its own sequence space (starting at 1) and its own baseline for MSFT (~300.0)
    for s in range(1, 26):
        t += 0.01
        # Alternating FEEDX (AAPL ~170) and FEEDY (MSFT ~300)
        add(source="FEEDX", instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=250 + s, price=170.05, qty=5.0)
        add(source="FEEDY", instrument="MSFT", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=300.0 + (s % 3) * 0.1, qty=25.0)

    # 10. Combined Violations (301 to 350)
    # Stale + crossed quote
    for i in range(301, 311):
        t += 0.01
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t - 1.0, recv_ts=t, seq=280 + i, bid=175.0, ask=170.0, bid_sz=10.0, ask_sz=10.0)

    # Gap + Stale
    for i in range(311, 321):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t - 1.0, recv_ts=t, seq=600 + i, price=170.0, qty=10.0)

    # Implausible Future Ts + Price Spike
    for i in range(321, 331):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t + 10.0, recv_ts=t, seq=700 + i, price=250.0, qty=10.0)

    # Un-sequenced events (seq=None)
    for i in range(331, 351):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=None, price=170.0 + (i % 4) * 0.05, qty=15.0)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for v in vectors:
            f.write(json.dumps(v) + "\n")

    print(f"Successfully generated {len(vectors)} golden quality vectors in {out_path}")
    return len(vectors)


if __name__ == "__main__":
    generate_vectors()
