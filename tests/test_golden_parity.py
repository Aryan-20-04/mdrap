import json
import os
import pytest
from config import QualityConfig
from fastpath import FastQualityEngine
from models import CanonicalEvent, EventType, QualityStatus
from quality import QualityEngine


def _to_canonical(v: dict) -> CanonicalEvent:
    ev_type = EventType.TRADE if v["event_type"] == "TRADE" else EventType.QUOTE
    return CanonicalEvent(
        event_id=v["event_id"],
        instrument_id=v["instrument_id"],
        event_type=ev_type,
        exchange_timestamp=v["exchange_timestamp"],
        receive_timestamp=v["receive_timestamp"],
        processing_timestamp=v.get("processing_timestamp", v["receive_timestamp"]),
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


def test_golden_parity_python_vs_c():
    """Validates 100% verdict and reason parity across 350+ golden vectors."""
    vectors_file = os.path.join(
        os.path.dirname(__file__), "golden", "quality_vectors.jsonl"
    )
    assert os.path.exists(vectors_file), f"Missing golden vectors file: {vectors_file}"

    cfg = QualityConfig()
    py_eng = QualityEngine(cfg)
    c_eng = FastQualityEngine(cfg)

    mismatches = []
    vector_count = 0

    with open(vectors_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            vector_count += 1
            v = json.loads(line)
            ev_py = _to_canonical(v)
            ev_c = _to_canonical(v)

            res_py = py_eng.evaluate(ev_py)
            res_c = c_eng.evaluate(ev_c)

            st_py = res_py.quality_status.value
            st_c = res_c.quality_status.value
            r_py = sorted(res_py.reasons)
            r_c = sorted(res_c.reasons)

            if st_py != st_c or r_py != r_c:
                mismatches.append(
                    f"Line {line_num} (ID: {v['event_id']}, Type: {v['event_type']}, Seq: {v['sequence_number']}): "
                    f"Python=({st_py}, {r_py}) vs C=({st_c}, {r_c})"
                )

    assert vector_count >= 350, f"Expected >=350 golden vectors, got {vector_count}"
    if mismatches:
        pytest.fail(
            f"{len(mismatches)} of {vector_count} vectors diverged between Python and C engines:\n"
            + "\n".join(mismatches[:20])
        )


def test_golden_binary_vectors_spec_v1_roundtrip():
    """Validates that tests/golden/quality_vectors_v1.bin follows the 64-byte CanonicalRecordV1 spec."""
    import struct

    RECORD_STRUCT = struct.Struct("<QqqqqIHHBBH12s")
    assert RECORD_STRUCT.size == 64

    bin_path = os.path.join(
        os.path.dirname(__file__), "golden", "quality_vectors_v1.bin"
    )
    json_path = os.path.join(
        os.path.dirname(__file__), "golden", "quality_vectors.jsonl"
    )
    dict_path = os.path.join(os.path.dirname(__file__), "golden", "dictionary_v1.json")

    assert os.path.exists(bin_path), f"Missing {bin_path}"
    assert os.path.exists(json_path), f"Missing {json_path}"
    assert os.path.exists(dict_path), f"Missing {dict_path}"

    with open(bin_path, "rb") as fb, open(json_path, "r", encoding="utf-8") as fj:
        data = fb.read()
        assert len(data) % 64 == 0
        total_records = len(data) // 64
        assert total_records >= 350

        lines = [line.strip() for line in fj if line.strip()]
        assert total_records == len(lines)

        for i in range(total_records):
            chunk = data[i * 64 : (i + 1) * 64]
            (
                seq,
                ex_ts_ns,
                wire_ts_ns,
                price_ticks,
                qty_units,
                sym_id,
                venue_id,
                src_id,
                q_status,
                flags,
                mask,
                _,
            ) = RECORD_STRUCT.unpack(chunk)
            v = json.loads(lines[i])

            expected_seq = int(v["sequence_number"] or 0)
            assert seq == expected_seq
            assert venue_id == 1
            assert q_status in (0, 1, 2)
            if "reason_mask" in v:
                assert mask == v["reason_mask"]
