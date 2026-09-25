import copy
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityConfig, QualityEngine
from fastpath import FastQualityEngine, HAS_FASTPATH, _CFastEvent, _CFastResult

pytestmark = pytest.mark.skipif(
    not HAS_FASTPATH, reason="Native fastpath library not available"
)


def generate_parity_stream(seed: int, num_events: int) -> list[CanonicalEvent]:
    """
    Generate synthetic market data stream with all 9 fault types injected:
    1. stale
    2. crossed
    3. negative
    4. NaN
    5. sequence gap
    6. out of order
    7. spike (upward price anomaly)
    8. flash crash (downward price anomaly)
    9. duplicate
    """
    from simulator import FeedSimulator, SimulatorConfig
    from gateway import normalize

    sim_cfg = SimulatorConfig(
        seed=seed,
        num_events=num_events,
        duplicate_rate=0.015,
        missing_rate=0.012,
        out_of_order_rate=0.010,
        malformed_rate=0.0,
        price_anomaly_rate=0.012,
        crossed_quote_rate=0.008,
    )
    sim = FeedSimulator(sim_cfg)
    events: list[CanonicalEvent] = [normalize(r) for r, _ in sim.generate()]

    # Inject negative, NaN, explicit stale, and flash crash / spike boundaries
    for i, ev in enumerate(events):
        mod = i % 120
        if mod == 7:
            ev.price = -25.5
        elif mod == 19:
            ev.price = math.nan
        elif mod == 31:
            ev.quantity = -100.0
        elif mod == 43 and ev.bid_price is not None:
            ev.bid_price = math.nan
        elif mod == 57 and ev.ask_price is not None:
            ev.ask_price = -5.0
        elif mod == 71:
            ev.receive_timestamp = ev.exchange_timestamp + 0.15  # stale (> 0.05)
        elif mod == 83 and ev.price is not None:
            ev.price = round(ev.price * 0.40, 2)  # flash crash (-60%)
        elif mod == 97 and ev.price is not None:
            ev.price = round(ev.price * 2.50, 2)  # extreme spike (+150%)

    return events


def _format_hex_dump(c_struct) -> str:
    import ctypes

    raw_bytes = bytes(ctypes.string_at(ctypes.byref(c_struct), ctypes.sizeof(c_struct)))
    return " ".join(f"{b:02X}" for b in raw_bytes)


@pytest.mark.parametrize("seed", [42, 1337, 777, 2026, 99999])
def test_parity_differential_seed(seed: int):
    # Support fast unit-test mode or full 1M scale via env var
    num_events = int(os.environ.get("MDRAP_PARITY_EVENTS", "50000"))

    cfg = QualityConfig(
        staleness_threshold_s=0.05, price_anomaly_stddev=6.0, price_window=50
    )

    events = generate_parity_stream(seed, num_events)

    q_py = QualityEngine(cfg)
    q_c = FastQualityEngine(cfg)
    q_batch = FastQualityEngine(cfg)

    evs_py = [copy.deepcopy(e) for e in events]
    evs_c = [copy.deepcopy(e) for e in events]
    evs_batch = [copy.deepcopy(e) for e in events]

    # Evaluate pure Python
    for e in evs_py:
        q_py.evaluate(e)

    # Evaluate Native C (single)
    for e in evs_c:
        q_c.evaluate(e)

    # Evaluate Native C (micro-batch)
    chunk_size = 128
    for i in range(0, len(evs_batch), chunk_size):
        q_batch.evaluate_batch(evs_batch[i : i + chunk_size])

    # Assert event-by-event equivalence
    for idx, (epy, ec, eb) in enumerate(zip(evs_py, evs_c, evs_batch)):
        if (
            epy.quality_status != ec.quality_status
            or set(epy.reasons) != set(ec.reasons)
            or epy.quality_status != eb.quality_status
            or set(epy.reasons) != set(eb.reasons)
        ):
            # Format diagnostic info
            c_ev = _CFastEvent()
            c_ev.source_id = ec.source_id
            c_ev.instrument_id = ec.instrument_id_int
            c_ev.event_type = 1 if ec.event_type == EventType.QUOTE else 0
            c_ev.exchange_ts = ec.exchange_timestamp
            c_ev.receive_ts = ec.receive_timestamp
            c_ev.sequence_num = (
                ec.sequence_number if ec.sequence_number is not None else -1
            )
            c_ev.price = ec.price if ec.price is not None else math.nan
            c_ev.quantity = ec.quantity if ec.quantity is not None else math.nan

            c_res = _CFastResult()
            c_res.status = (
                1
                if ec.quality_status == QualityStatus.VALID
                else (2 if ec.quality_status == QualityStatus.SUSPICIOUS else 3)
            )

            diag = (
                f"\n[DIFFERENTIAL PARITY FAILURE] at Event Index: {idx}\n"
                f"Raw Event: {events[idx]}\n"
                f"Python Output: status={epy.quality_status.value}, reasons={epy.reasons}\n"
                f"Native C Output: status={ec.quality_status.value}, reasons={ec.reasons}\n"
                f"Native C Batch Output: status={eb.quality_status.value}, reasons={eb.reasons}\n"
                f"C Event Hex Dump: {_format_hex_dump(c_ev)}\n"
                f"C Result Hex Dump: {_format_hex_dump(c_res)}\n"
            )
            pytest.fail(diag)

    # Bit-identical counts
    assert q_py.counts == q_c.counts == q_batch.counts, (
        f"Counts mismatch: PY={q_py.counts}, C={q_c.counts}, BATCH={q_batch.counts}"
    )

    # Bit-identical reason histograms
    assert q_py.reason_counts == q_c.reason_counts == q_batch.reason_counts, (
        f"Reason histogram mismatch: PY={q_py.reason_counts}, C={q_c.reason_counts}, BATCH={q_batch.reason_counts}"
    )
