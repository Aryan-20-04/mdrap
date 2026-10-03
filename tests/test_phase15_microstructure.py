"""Institutional Verification Suite for Phase 15: Market Microstructure Correctness.

Tests all remediations under Phase 15:
- MIC-01: Midnight-crossing trading session schedule evaluation in venues.py.
- MIC-02: Consolidated L2 depth truncation after multi-venue price aggregation in depth.py.
- MIC-04: NASDAQ ITCH book reset on halts ('H') and session boundary ('S') in itch.py.
- MIC-05: Strict participant attribution without pseudorandom broker MPID fabrication in flow_tracker.py.
- MIC-06: Asynchronous, non-blocking TCP replay backfill in multicast_arbitrator.py.
"""

from __future__ import annotations

import datetime
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from venues import (
    MarketVenue,
    MarketPhase,
    TickSizeModel,
    get_session_phase,
)
from depth import (
    ConsolidatedDepthEngine,
)
from models import (
    RawEvent,
)
from itch import (
    ITCHMessage,
    ITCHOrderBookTracker,
)
from flow_tracker import (
    OrderFlowTracker,
    AggressorSide,
)
from multicast_arbitrator import (
    ABFeedArbitrator,
    UDPPacket,
)


def test_mic_01_midnight_crossing_session():
    """Verify MIC-01: Sessions crossing UTC midnight evaluate open/closed correctly."""
    # Venue that opens at 23:00 UTC and closes at 06:00 UTC
    venue = MarketVenue(
        mic="TEST",
        name="Midnight Crossing Test Venue",
        country="JP",
        flag="🇯🇵",
        currency="JPY",
        currency_symbol="¥",
        timezone_name="Asia/Tokyo",
        utc_offset_hours=9.0,
        open_time_utc_hour=23.0,
        close_time_utc_hour=6.0,
        pre_open_utc_hour=22.5,
        closing_auction_utc_hour=5.8,
        benchmark_index="NIKKEI",
        index_name="Nikkei 225",
        tick_size_model=TickSizeModel.TSE_TIERED,
    )

    # Wednesday 2026-09-16
    # 1. During open session before midnight: 23:30 UTC
    dt_open_1 = datetime.datetime(2026, 9, 16, 23, 30, tzinfo=datetime.timezone.utc)
    is_open, phase, _ = get_session_phase(venue, dt_open_1.timestamp())
    assert is_open is True
    assert phase == MarketPhase.CONTINUOUS

    # 2. During open session past midnight: 02:00 UTC (Thursday 2026-09-17)
    dt_open_2 = datetime.datetime(2026, 9, 17, 2, 0, tzinfo=datetime.timezone.utc)
    is_open, phase, _ = get_session_phase(venue, dt_open_2.timestamp())
    assert is_open is True
    assert phase == MarketPhase.CONTINUOUS

    # 3. Before session open (pre-open): 22:45 UTC
    dt_pre = datetime.datetime(2026, 9, 16, 22, 45, tzinfo=datetime.timezone.utc)
    is_open, phase, _ = get_session_phase(venue, dt_pre.timestamp())
    assert is_open is False
    assert phase == MarketPhase.PRE_OPEN

    # 4. Outside session: 12:00 UTC
    dt_closed = datetime.datetime(2026, 9, 16, 12, 0, tzinfo=datetime.timezone.utc)
    is_open, phase, _ = get_session_phase(venue, dt_closed.timestamp())
    assert is_open is False
    assert phase == MarketPhase.CLOSED


def test_mic_02_l2_depth_aggregation_before_truncation():
    """Verify MIC-02: Multi-venue levels at the same price are consolidated before truncation."""
    engine = ConsolidatedDepthEngine(max_levels_per_side=2)

    # Venue 1 has bids: 100.0 (size 10), 99.0 (size 5)
    raw_v1 = RawEvent(
        source="V1",
        payload={
            "instrument": "AAPL",
            "exchange_ts": 1000.0,
            "bids": [[100.0, 10.0], [99.0, 5.0]],
            "asks": [[101.0, 10.0], [102.0, 10.0]],
        },
        receive_timestamp=1000.001,
        raw_id="v1",
    )
    engine.observe(raw_v1)

    # Venue 2 has bids: 100.0 (size 20), 98.0 (size 15)
    raw_v2 = RawEvent(
        source="V2",
        payload={
            "instrument": "AAPL",
            "exchange_ts": 1000.01,
            "bids": [[100.0, 20.0], [98.0, 15.0]],
            "asks": [[101.0, 20.0], [103.0, 10.0]],
        },
        receive_timestamp=1000.011,
        raw_id="v2",
    )
    engine.observe(raw_v2)

    # Venue 3 has bids: 99.0 (size 15), 97.0 (size 30)
    raw_v3 = RawEvent(
        source="V3",
        payload={
            "instrument": "AAPL",
            "exchange_ts": 1000.02,
            "bids": [[99.0, 15.0], [97.0, 30.0]],
            "asks": [[102.0, 15.0], [104.0, 10.0]],
        },
        receive_timestamp=1000.021,
        raw_id="v3",
    )
    ladder = engine.observe(raw_v3)
    assert ladder is not None

    # Top 2 aggregated bid prices must be 100.0 and 99.0
    agg_bids = ladder.aggregated_bids
    assert len(agg_bids) == 2
    assert agg_bids[0].price == 100.0
    assert agg_bids[0].total_size == 30.0  # 10 + 20 from V1 and V2
    assert len(agg_bids[0].venue_sizes) == 2

    assert agg_bids[1].price == 99.0
    assert agg_bids[1].total_size == 20.0  # 5 + 15 from V1 and V3
    assert len(agg_bids[1].venue_sizes) == 2

    # Top 2 aggregated ask prices must be 101.0 and 102.0
    agg_asks = ladder.aggregated_asks
    assert len(agg_asks) == 2
    assert agg_asks[0].price == 101.0
    assert agg_asks[0].total_size == 30.0  # 10 + 20
    assert agg_asks[1].price == 102.0
    assert agg_asks[1].total_size == 25.0  # 10 + 15

    # Micro-price should be balanced since top bid (100.0, size 30) and top ask (101.0, size 30) are equal
    assert ladder.micro_price == pytest.approx(100.5)


def test_mic_04_itch_halt_and_session_clearing():
    """Verify MIC-04: Trading halts ('H') and session boundary ('S') clear order books and ghost orders."""
    tracker = ITCHOrderBookTracker()

    # Add orders for AAPL
    msg_aapl_bid = ITCHMessage(
        msg_type="A",
        locate=1,
        tracking=0,
        timestamp_ns=1_000_000,
        stock="AAPL",
        order_ref=1001,
        side="B",
        shares=500,
        price=150.0,
    )
    msg_aapl_ask = ITCHMessage(
        msg_type="A",
        locate=1,
        tracking=0,
        timestamp_ns=1_000_001,
        stock="AAPL",
        order_ref=1002,
        side="S",
        shares=300,
        price=150.5,
    )
    msg_msft_bid = ITCHMessage(
        msg_type="A",
        locate=2,
        tracking=0,
        timestamp_ns=1_000_002,
        stock="MSFT",
        order_ref=2001,
        side="B",
        shares=100,
        price=300.0,
    )

    tracker.process_message(msg_aapl_bid)
    tracker.process_message(msg_aapl_ask)
    tracker.process_message(msg_msft_bid)

    assert len(tracker.orders) == 3
    assert len(tracker.depth["AAPL"]["B"]) > 0
    assert len(tracker.depth["AAPL"]["S"]) > 0
    assert len(tracker.depth["MSFT"]["B"]) > 0

    # Stock trading action: AAPL halted
    halt_msg = ITCHMessage(
        msg_type="H",
        locate=1,
        tracking=0,
        timestamp_ns=1_050_000,
        stock="AAPL",
        details={"state": "H", "reason": "T1"},
    )
    tracker.process_message(halt_msg)

    # AAPL book and orders must be completely purged
    assert len(tracker.depth["AAPL"]["B"]) == 0
    assert len(tracker.depth["AAPL"]["S"]) == 0
    assert 1001 not in tracker.orders
    assert 1002 not in tracker.orders

    # MSFT must remain untouched
    assert 2001 in tracker.orders
    assert len(tracker.depth["MSFT"]["B"]) > 0

    # End of Day System event: 'C'
    session_close_msg = ITCHMessage(
        msg_type="S",
        locate=0,
        tracking=0,
        timestamp_ns=2_000_000,
        event_code="C",
    )
    tracker.process_message(session_close_msg)

    # Entire book and order tracker must be cleared
    assert len(tracker.orders) == 0
    assert len(tracker.depth) == 0


def test_mic_05_flow_tracker_unknown_mpid():
    """Verify MIC-05: Missing broker attribution defaults to 'UNKNOWN' without pseudo-random hallucination."""
    tracker = OrderFlowTracker(symbol="AAPL")

    # Record public trade without broker MPID (empty string / None)
    tracker.observe_trade(
        price=150.0,
        size=100.0,
        timestamp=1000.0,
        bid=149.95,
        ask=150.05,
        broker="",
    )

    # Record trade with whitespace broker
    tracker.observe_trade(
        price=150.1,
        size=200.0,
        timestamp=1001.0,
        bid=150.0,
        ask=150.1,
        broker="   ",
    )

    # Verify participant is UNKNOWN and no hallucinated Wall Street MPID (e.g. MSCO, GSCO) exists
    assert "UNKNOWN" in tracker.participants
    assert tracker.participants["UNKNOWN"].trade_count == 2
    assert tracker.participants["UNKNOWN"].buy_volume == 300.0
    for p_id in tracker.participants:
        assert p_id == "UNKNOWN"

    # Now record explicit broker
    tracker.observe_trade(
        price=150.0,
        size=50.0,
        timestamp=1002.0,
        bid=149.95,
        ask=150.05,
        broker="JPM",
    )
    assert "JPM" in tracker.participants
    assert tracker.participants["JPM"].trade_count == 1


def test_mic_06_multicast_arbitrator_async_tcp_replay():
    """Verify MIC-06: Asynchronous TCP replay does not block the caller and recovers gaps."""
    # Replay client that sleeps 50ms to simulate network latency
    def slow_replay_client(ch: str, start: int, end: int) -> list[UDPPacket]:
        time.sleep(0.05)
        return [
            UDPPacket(
                channel_id=ch,
                sequence_num=s,
                feed_id="TCP_REPLAY",
                payload=f"replayed_{s}".encode(),
            )
            for s in range(start, end + 1)
        ]

    arb = ABFeedArbitrator(
        tcp_replay_client=slow_replay_client,
        initial_seq=1,
        async_tcp_replay=True,
    )

    try:
        # In-order packet 1
        pkt1 = UDPPacket(channel_id="CH1", sequence_num=1, feed_id="A", payload=b"p1")
        dispatched1 = arb.on_packet(pkt1)
        assert len(dispatched1) == 1
        assert dispatched1[0].sequence_num == 1

        # Packet 3 arrives (missing packet 2)
        pkt3 = UDPPacket(channel_id="CH1", sequence_num=3, feed_id="A", payload=b"p3")

        t0 = time.perf_counter()
        dispatched3 = arb.on_packet(pkt3)
        elapsed = time.perf_counter() - t0

        # Must return immediately without waiting for slow_replay_client (50ms)
        assert elapsed < 0.04, f"on_packet blocked for {elapsed:.4f}s"
        assert len(dispatched3) == 0  # Sequence gap buffered

        # Wait for async replay thread to finish backfill
        max_wait = 1.0
        start_wait = time.time()
        drained: list[UDPPacket] = []
        while time.time() - start_wait < max_wait:
            drained = arb.drain("CH1")
            if len(drained) >= 2:
                break
            time.sleep(0.01)

        assert len(drained) == 2
        assert [p.sequence_num for p in drained] == [2, 3]
        assert arb.metrics.tcp_packets_recovered == 1
        assert arb.metrics.in_order_dispatched == 3
    finally:
        arb.close()
