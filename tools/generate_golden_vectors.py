"""Generate tests/golden/quality_vectors.jsonl and tests/golden/quality_vectors_v1.bin with >=350 comprehensive test vectors.

Emits:
1. tests/golden/quality_vectors.jsonl - JSON lines format with input fields and oracle expected status/reasons.
2. tests/golden/quality_vectors_v1.bin - CanonicalRecordV1 64-byte binary layout.
3. tests/golden/dictionary_v1.json - Side dictionary for symbol_id, venue_id, and source_id mapping.

Covers:
- Clean trades and quotes (warmup)
- Structural validations (null, zero, negative, NaN, Inf, missing fields)
- Crossed quotes (bid >= ask, inverted spreads)
- Sequence gaps, duplicates, out-of-order, resync
- Timestamp staleness and future timestamp plausibility
- Price anomalies, warm-up boundaries, and regime shift re-seeding
- Multi-source independent sequence and baseline isolation
- Combined multi-fault vectors
- Duplicate bursts (consecutive identical sequences)
- Massive sequence gaps (>10k)
- 3-sigma price spike bursts
- Symbol table / dictionary scaling edge cases
"""

import json
import math
import os
import struct
import sys

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from config import QualityConfig
from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityEngine

# CanonicalRecordV1 64-byte struct layout matching docs/spec/canonical-record-v1.md
# <Q (uint64 seq), q (int64 ex_ts_ns), q (int64 wire_ts_ns),
# q (int64 price_ticks), q (int64 qty_units),
# I (uint32 sym_id), H (uint16 venue_id), H (uint16 src_id),
# B (uint8 quality_status), B (uint8 flags), H (uint16 reason_mask), 12s (reserved 12B)
RECORD_V1_STRUCT = struct.Struct("<QqqqqIHHBBH12s")
assert RECORD_V1_STRUCT.size == 64, f"CanonicalRecordV1 must be 64 bytes, got {RECORD_V1_STRUCT.size}"

REASON_BITS = {
    Reason.SCHEMA_VIOLATION.value: (1 << 0),
    Reason.DUPLICATE.value: (1 << 1),
    Reason.SEQUENCE_GAP.value: (1 << 2),
    Reason.OUT_OF_ORDER.value: (1 << 3),
    Reason.STALE.value: (1 << 4),
    Reason.PRICE_ANOMALY.value: (1 << 5),
    Reason.CROSSED_QUOTE.value: (1 << 6),
    Reason.CROSS_FEED_DISAGREEMENT.value: (1 << 7),
    Reason.MALFORMED.value: (1 << 8),
    Reason.CIRCUIT_FILTER_BREACH.value: (1 << 9),
    Reason.VOLATILITY_INTERRUPTION.value: (1 << 10),
    Reason.SPECIAL_QUOTE_INDICATION.value: (1 << 11),
    Reason.TS_IMPLAUSIBLE.value: (1 << 12),
    Reason.RATE_LIMITED.value: (1 << 13),
    Reason.SECURITY_REJECT.value: (1 << 14),
}

STATUS_CODES = {
    QualityStatus.VALID.name: 0,
    QualityStatus.SUSPICIOUS.name: 1,
    QualityStatus.INVALID.name: 2,
}


def generate_vectors(
    out_jsonl: str = "tests/golden/quality_vectors.jsonl",
    out_bin: str = "tests/golden/quality_vectors_v1.bin",
    out_dict: str = "tests/golden/dictionary_v1.json",
):
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
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=61, price=None, qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=62, price=0.0, qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=63, price=-10.0, qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=64, price=float("nan"), qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=65, price=float("inf"), qty=10.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=66, price=150.0, qty=None)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=67, price=150.0, qty=0.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=68, price=150.0, qty=-1.0)
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=69, price=150.0, qty=float("nan"))
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=70, price=150.0, qty=float("inf"))

    for i in range(71, 86):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=150.01, qty=10.0)

    # 4. Structural Violations on Quotes (86 to 110)
    t += 0.01
    add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=86, bid=None, ask=None)
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
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=i, bid=150.10 + i * 0.01, ask=150.00, bid_sz=10.0, ask_sz=10.0)

    for i in range(121, 131):
        t += 0.01
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=i, bid=150.05, ask=150.05, bid_sz=10.0, ask_sz=10.0)

    # 6. Timestamp Anomalies: Staleness & Implausible Skew (131 to 160)
    for i in range(131, 146):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t - 2.0, recv_ts=t, seq=i, price=150.02, qty=10.0)

    for i in range(146, 161):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t + 5.0, recv_ts=t, seq=i, price=150.02, qty=10.0)

    # 7. Sequence Anomalies: Gaps, Duplicates, Out-of-Order (161 to 210)
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=161, price=150.03, qty=10.0)

    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=175, price=150.03, qty=10.0)

    for i in range(163, 171):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=175, price=150.03, qty=10.0)

    for i, s in enumerate(range(162, 172)):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=150.03, qty=10.0)

    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=165, price=150.03, qty=10.0)

    for s in range(182, 211):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=150.04, qty=10.0)

    # 8. Price Anomalies & Regime Shift (211 to 250)
    for i in range(211, 216):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=180.0, qty=10.0)

    for i in range(216, 221):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=150.04, qty=10.0)

    for i in range(221, 233):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=170.0, qty=10.0)

    for i in range(233, 251):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=i, price=170.02, qty=10.0)

    # 9. Multi-source Isolation (251 to 300)
    for s in range(1, 26):
        t += 0.01
        add(source="FEEDX", instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=250 + s, price=170.05, qty=5.0)
        add(source="FEEDY", instrument="MSFT", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=300.0 + (s % 3) * 0.1, qty=25.0)

    # 10. Combined Violations (301 to 350)
    for i in range(301, 311):
        t += 0.01
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t - 1.0, recv_ts=t, seq=280 + i, bid=175.0, ask=170.0, bid_sz=10.0, ask_sz=10.0)

    for i in range(311, 321):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t - 1.0, recv_ts=t, seq=600 + i, price=170.0, qty=10.0)

    for i in range(321, 331):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t + 10.0, recv_ts=t, seq=700 + i, price=250.0, qty=10.0)

    for i in range(331, 351):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=None, price=170.0 + (i % 4) * 0.05, qty=15.0)

    # 11. Section 11: Duplicate Bursts (351 to 365)
    for i in range(351, 366):
        t += 0.01
        # Repeatedly send identical sequence 800
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=800, price=170.10, qty=10.0)

    # 12. Section 12: Massive Sequence Gap (366 to 375)
    t += 0.01
    add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=50000, price=170.15, qty=10.0)
    for s in range(50001, 50010):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=s, price=170.15, qty=10.0)

    # 13. Section 13: High-Volatility Outlier Price Spikes (376 to 385)
    for i in range(376, 386):
        t += 0.01
        add(instrument="AAPL", event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=50009 + (i - 375), price=240.0 + i, qty=5.0)

    # 14. Section 14: Inverted Spread Crossed Quotes (386 to 395)
    for i in range(386, 396):
        t += 0.01
        add(instrument="AAPL", event_type="QUOTE", ex_ts=t, recv_ts=t + 0.001, seq=50020 + (i - 385), bid=180.0, ask=160.0, bid_sz=20.0, ask_sz=20.0)

    # 15. Section 15: Extreme Staleness (396 to 405)
    for i in range(396, 406):
        t += 0.01
        # 120 seconds old
        add(instrument="AAPL", event_type="TRADE", ex_ts=t - 120.0, recv_ts=t, seq=50035 + (i - 395), price=170.20, qty=10.0)

    # 16. Section 16: Multi-Symbol Side Dictionary Scaling (406 to 420)
    symbols = ["GOOGL", "AMZN", "NVDA", "TSLA", "META"]
    for i, sym in enumerate(symbols):
        for step in range(3):
            t += 0.01
            add(source="FEEDZ", instrument=sym, event_type="TRADE", ex_ts=t, recv_ts=t + 0.001, seq=100 + step, price=200.0 + i * 50.0, qty=10.0)

    # -------------------------------------------------------------------------
    # Oracle Evaluation: Compute Ground-Truth Verdicts & Binary Encoding
    # -------------------------------------------------------------------------
    oracle_cfg = QualityConfig()
    oracle_engine = QualityEngine(oracle_cfg)

    # Side dictionary mapping
    sym_dict = {}
    venue_dict = {"NASDAQ": 1, "CME": 2, "BINANCE": 3, "COINBASE": 4}
    source_dict = {}

    bin_records = []

    for v in vectors:
        ev_type = EventType.TRADE if v["event_type"] == "TRADE" else EventType.QUOTE
        ev = CanonicalEvent(
            event_id=v["event_id"],
            instrument_id=v["instrument_id"],
            event_type=ev_type,
            exchange_timestamp=v["exchange_timestamp"],
            receive_timestamp=v["receive_timestamp"],
            processing_timestamp=v["processing_timestamp"],
            source=v["source"],
            sequence_number=v["sequence_number"],
            price=v["price"],
            quantity=v["quantity"],
            bid_price=v["bid_price"],
            bid_size=v["bid_size"],
            ask_price=v["ask_price"],
            ask_size=v["ask_size"],
            quality_status=QualityStatus.VALID,
            reasons=[],
            raw_id=f"raw-{v['event_id']}",
        )

        # Run oracle state machine
        res = oracle_engine.evaluate(ev)
        v["expected_status"] = res.quality_status.name
        v["expected_reasons"] = sorted(res.reasons)

        # Compute 16-bit reason mask
        mask = 0
        for r in res.reasons:
            mask |= REASON_BITS.get(r, 0)
        v["reason_mask"] = mask

        # Intern dictionary strings
        sym = v["instrument_id"]
        if sym not in sym_dict:
            sym_dict[sym] = len(sym_dict)
        sym_id = sym_dict[sym]

        src = v["source"]
        if src not in source_dict:
            source_dict[src] = len(source_dict) + 1
        src_id = source_dict[src]

        # Pack into CanonicalRecordV1 64-byte layout
        seq = int(v["sequence_number"] or 0)
        ex_ts_ns = int(round(v["exchange_timestamp"] * 1e9))
        wire_ts_ns = int(round(v["receive_timestamp"] * 1e9))

        def _to_ticks(val):
            if val is None or not math.isfinite(val):
                return 0
            return int(round(val * 10000))

        price_ticks = _to_ticks(v["price"])
        qty_units = _to_ticks(v["quantity"])
        bid_ticks = _to_ticks(v["bid_price"])
        ask_ticks = _to_ticks(v["ask_price"])

        is_crossed = bool(v["bid_price"] is not None and v["ask_price"] is not None and v["bid_price"] >= v["ask_price"])
        flags = (0x01 if is_crossed else 0x00) | (0x04 if ev_type == EventType.QUOTE else 0x00)

        raw_record = RECORD_V1_STRUCT.pack(
            seq,
            ex_ts_ns,
            wire_ts_ns,
            price_ticks,
            qty_units,
            sym_id,
            1,  # Default venue: NASDAQ
            src_id,
            STATUS_CODES[res.quality_status.name],
            flags,
            mask,
            b"\x00" * 12,  # Reserved padding
        )
        bin_records.append(raw_record)

    # 1. Write JSONL file
    os.makedirs(os.path.dirname(out_jsonl), exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for v in vectors:
            f.write(json.dumps(v) + "\n")

    # 2. Write Binary file
    os.makedirs(os.path.dirname(out_bin), exist_ok=True)
    with open(out_bin, "wb") as f:
        for rec in bin_records:
            f.write(rec)

    # 3. Write Dictionary file
    os.makedirs(os.path.dirname(out_dict), exist_ok=True)
    with open(out_dict, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": 1,
                "symbols": {v: k for k, v in sym_dict.items()},
                "venues": {v: k for k, v in venue_dict.items()},
                "sources": {v: k for k, v in source_dict.items()},
            },
            f,
            indent=2,
        )

    print(f"Successfully generated {len(vectors)} golden quality vectors:")
    print(f"  • JSONL:      {out_jsonl}")
    print(f"  • Binary v1:  {out_bin} ({len(bin_records) * 64} bytes)")
    print(f"  • Dictionary: {out_dict}")
    return len(vectors)


if __name__ == "__main__":
    generate_vectors()
