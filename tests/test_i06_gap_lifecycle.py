"""Unit and integration tests for Sequence Gap Lifecycle and Retransmission Management (Program I-6)."""

from __future__ import annotations

import tempfile
import time
import pytest

from mdrap.gap import GapManager, GapRecord, GapState
from mdrap.models import RawEvent, QualityStatus, Reason
from mdrap.engine import Engine, EngineConfig
from mdrap.clock import FixedClock


def test_gap_state_transitions() -> None:
    """Test standard OPEN -> FILLED and OPEN -> EXPIRED lifecycle transitions."""
    mgr = GapManager(gap_timeout_s=1.0)
    now = 1000.0

    # Record gap between seq 1 and seq 4 (missing 2 and 3)
    records = mgr.record_gap(
        source="BINANCE",
        instrument="BTC-USDT",
        expected_start=2,
        received_seq=4,
        epoch="0",
        now_ts=now,
    )
    assert len(records) == 2
    assert records[0].missing_seq == 2
    assert records[0].state == GapState.OPEN
    assert records[1].missing_seq == 3
    assert records[1].state == GapState.OPEN

    # Fill sequence 2
    filled = mgr.try_fill(
        source="BINANCE",
        instrument="BTC-USDT",
        seq=2,
        epoch="0",
        now_ts=now + 0.1,
    )
    assert filled is not None
    assert filled.missing_seq == 2
    assert filled.state == GapState.FILLED
    assert filled.filled_ts == now + 0.1

    # Remaining open gap is 3
    open_gaps = mgr.get_open_gaps()
    assert len(open_gaps) == 1
    assert open_gaps[0].missing_seq == 3

    # Drain expired gaps after timeout
    expired = mgr.drain_expired(now_ts=now + 1.5)
    assert len(expired) == 1
    assert expired[0].missing_seq == 3
    assert expired[0].state == GapState.EXPIRED
    assert expired[0].expired_ts == now + 1.5

    assert len(mgr.get_open_gaps()) == 0
    stats = mgr.stats()
    assert stats["open"] == 0
    assert stats["filled"] == 1
    assert stats["expired"] == 1
    assert stats["unrecoverable"] == 0


def test_gap_buffer_capacity_unrecoverable() -> None:
    """Test bounded capacity transitions oldest gaps to UNRECOVERABLE."""
    mgr = GapManager(max_open_gaps=5)
    records = mgr.record_gap(
        source="COINBASE",
        instrument="ETH-USD",
        expected_start=1,
        received_seq=10,  # 9 missing seqs: 1..9
        epoch="0",
    )
    assert len(records) == 9
    stats = mgr.stats()
    # 5 retained open, 4 oldest marked UNRECOVERABLE
    assert stats["open"] == 5
    assert stats["unrecoverable"] == 4
    open_seqs = [r.missing_seq for r in mgr.get_open_gaps()]
    assert open_seqs == [5, 6, 7, 8, 9]


def test_on_gap_hook_invocation_and_safety() -> None:
    """Test user extension hook is triggered on gap creation and handles errors safely."""
    hook_calls = []

    def on_gap_callback(src: str, inst: str, exp: int, rcv: int, ep: str) -> None:
        hook_calls.append((src, inst, exp, rcv, ep))

    mgr = GapManager(on_gap_hook=on_gap_callback)
    mgr.record_gap(
        source="KRAKEN",
        instrument="SOL-USD",
        expected_start=10,
        received_seq=15,
        epoch="1",
    )
    assert len(hook_calls) == 1
    assert hook_calls[0] == ("KRAKEN", "SOL-USD", 10, 15, "1")

    # Faulty hook does not raise or interrupt operation
    def faulty_hook(*args) -> None:
        raise RuntimeError("Network failure in retransmission hook")

    faulty_mgr = GapManager(on_gap_hook=faulty_hook)
    recs = faulty_mgr.record_gap("KRAKEN", "SOL-USD", 20, 25)
    assert len(recs) == 5


def test_engine_gap_integration() -> None:
    """Test full integration with Engine: gap tagging, retransmission fill, and stats."""
    clock = FixedClock(1700000000.0)
    gap_mgr = GapManager(gap_timeout_s=5.0)

    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = EngineConfig(
            wal_dir=tmp_dir,
            clock=clock,
            fsync_policy="always",
            gap_manager=gap_mgr,
        )
        engine = Engine.open(tmp_dir, config=cfg)

        # 1. Normal packet seq 100
        ev1 = RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "sequence": 100,
                "price": 180.0,
                "quantity": 10.0,
                "exchange_ts": clock.now(),
            },
        )
        d1 = engine.submit(ev1)
        assert d1.quality_status == QualityStatus.VALID

        # 2. Sequence jump to 104 (missing 101, 102, 103)
        ev2 = RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "sequence": 104,
                "price": 180.5,
                "quantity": 20.0,
                "exchange_ts": clock.now(),
            },
        )
        d2 = engine.submit(ev2)
        assert d2.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.SEQUENCE_GAP.value in d2.reasons

        # Verify gaps 101, 102, 103 are open
        open_gaps = engine.gap_manager.get_open_gaps()
        assert [g.missing_seq for g in open_gaps] == [101, 102, 103]

        # 3. Late arriving packet seq 102 arrives (fills gap)
        ev3 = RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "sequence": 102,
                "price": 180.2,
                "quantity": 15.0,
                "exchange_ts": clock.now(),
            },
        )
        d3 = engine.submit(ev3)
        # Gap is filled: marked VALID with Reason.GAP_FILLED
        assert d3.quality_status == QualityStatus.VALID
        assert Reason.GAP_FILLED.value in d3.reasons
        assert Reason.OUT_OF_ORDER.value not in d3.reasons

        # 4. Duplicate late packet seq 102 arrives
        ev4 = RawEvent(
            source="NASDAQ",
            payload={
                "instrument": "AAPL",
                "event_type": "TRADE",
                "sequence": 102,
                "price": 180.2,
                "quantity": 15.0,
                "exchange_ts": clock.now(),
            },
        )
        d4 = engine.submit(ev4)
        # No longer in open gaps, so treated as OUT_OF_ORDER
        assert d4.quality_status == QualityStatus.SUSPICIOUS
        assert Reason.OUT_OF_ORDER.value in d4.reasons

        # Stats check
        st = engine.gap_manager.stats()
        assert st["filled"] == 1
        assert st["open"] == 2  # 101 and 103 remain open
        assert [g.missing_seq for g in engine.gap_manager.get_open_gaps()] == [101, 103]

        engine.close()
