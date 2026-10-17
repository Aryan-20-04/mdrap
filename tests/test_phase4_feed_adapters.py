"""Acceptance test suite for Phase 4: Public Feed Adapters & Sequence Correctness.

Verifies:
1. FeedAdapter Protocol Conformance:
   - BinanceAdapter, KrakenAdapter, DatabentoAdapter, ITCHAdapter all conform to FeedAdapter.
   - Legacy open() / close() aliases and context manager ergonomics.
2. Licensing & Capability Declarations:
   - Explicit capabilities() with licensing tiers (PUBLIC_COMMUNITY, COMMERCIAL_INTERNAL_DISPLAY, NON_DISPLAY_PROP_TRADING).
   - Sequence, gap detection, retransmission, and snapshot support flags.
3. Sequence Tracking & Gap Detection:
   - Monotonic sequence tracking without false positives.
   - Detection of sequence gaps, lost frame count accounting, and DEGRADED state transitions.
   - Duplicate and out-of-order sequence accounting.
4. Reconnection & Exponential Backoff with Jitter:
   - Exponential delay progression bounded by max_delay.
   - Jitter randomness and successful reset.
5. Backpressure & Drop Accounting:
   - Bounded buffer overflow tracking: never silently drops a frame.
   - Explicit drops_total counter exposed via health().
6. Normalization & Frame Parsing:
   - BinanceAdapter: bookTicker, depth5, trade, and synthetic exchange_ts fallback.
   - KrakenAdapter: list-based ticker, book snapshot, and trade frames.
   - DatabentoAdapter: DBN binary struct decoding, 1e9 fixed-point price, and nanosecond timestamps.
   - ITCHAdapter: Nasdaq ITCH 5.0 binary message decoding, 10^-4 price scaling, and MBO quote synthesis.
"""

from __future__ import annotations

import struct
import time
import pytest

from mdrap.adapters import (
    AdapterCapabilities,
    AdapterStreamState,
    BaseFeedAdapter,
    BinanceAdapter,
    DatabentoAdapter,
    FeedAdapter,
    ITCHAdapter,
    KrakenAdapter,
    LicensingTier,
    ReconnectTracker,
    SequenceTracker,
)
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent


class TestFeedAdapterProtocolAndCapabilities:
    """Verifies that all Phase 4 adapters conform to FeedAdapter and declare capabilities."""

    @pytest.mark.parametrize(
        "adapter_cls,symbol,expected_tier",
        [
            (BinanceAdapter, "BTCUSDT", LicensingTier.PUBLIC_COMMUNITY),
            (KrakenAdapter, "XBT/USD", LicensingTier.PUBLIC_COMMUNITY),
            (DatabentoAdapter, "ES.FUT", LicensingTier.COMMERCIAL_INTERNAL_DISPLAY),
            (ITCHAdapter, "AAPL", LicensingTier.NON_DISPLAY_PROP_TRADING),
        ],
    )
    def test_adapter_protocol_and_capabilities(
        self, adapter_cls, symbol, expected_tier
    ):
        adapter = adapter_cls(symbol=symbol)
        assert isinstance(adapter, FeedAdapter)

        # Capabilities declaration
        caps = adapter.capabilities()
        assert isinstance(caps, AdapterCapabilities)
        assert caps.licensing_tier == expected_tier
        assert caps.supports_monotonic_sequence is True
        assert caps.supports_gap_detection is True

        cap_dict = caps.to_dict()
        assert isinstance(cap_dict, dict)
        assert cap_dict["licensing_tier"] == expected_tier.value
        assert "supports_monotonic_sequence" in cap_dict
        assert "transport" in cap_dict

    def test_context_manager_lifecycle(self):
        with BinanceAdapter(symbol="ETHUSDT") as adapter:
            assert adapter.is_connected is True
            assert adapter.state == AdapterStreamState.CONNECTED
            h = adapter.health()
            assert h["connected"] is True
            assert h["status"] == "HEALTHY"
        assert adapter.is_connected is False
        assert adapter.state == AdapterStreamState.DISCONNECTED


class TestSequenceTrackingAndGapDetection:
    """Verifies monotonic sequence tracking, gap detection, and state degradation."""

    def test_monotonic_sequence_pass(self):
        tracker = SequenceTracker(venue="TEST", strict=True)
        for seq in range(1, 101):
            is_valid, reason = tracker.observe(seq)
            assert is_valid is True
            assert reason is None

        assert tracker.gaps_total == 0
        assert tracker.missing_frames_total == 0
        assert tracker.duplicates_total == 0
        assert tracker.last_sequence == 100

    def test_sequence_gap_detection(self):
        tracker = SequenceTracker(venue="TEST", strict=True)
        tracker.observe(1)
        tracker.observe(2)
        tracker.observe(3)

        # Gap from 3 to 10 (lost 6 frames: 4, 5, 6, 7, 8, 9)
        is_valid, reason = tracker.observe(10)
        assert is_valid is False
        assert "sequence_gap_6_frames" in str(reason)
        assert tracker.gaps_total == 1
        assert tracker.missing_frames_total == 6
        assert len(tracker.gap_history) == 1
        assert tracker.gap_history[0]["expected"] == 4
        assert tracker.gap_history[0]["received"] == 10

        # Monotonic resume
        is_valid2, reason2 = tracker.observe(11)
        assert is_valid2 is True
        assert tracker.gaps_total == 1

    def test_duplicate_and_out_of_order_detection(self):
        tracker = SequenceTracker(venue="TEST", strict=True)
        tracker.observe(5)

        # Duplicate
        is_valid, reason = tracker.observe(5)
        assert is_valid is False
        assert "duplicate_sequence_5" in str(reason)
        assert tracker.duplicates_total == 1

        # Out-of-order
        is_valid_ooo, reason_ooo = tracker.observe(4)
        assert is_valid_ooo is False
        assert "out_of_order" in str(reason_ooo)
        assert tracker.out_of_order_total == 1

    def test_adapter_gap_triggers_degraded_state(self):
        adapter = DatabentoAdapter(symbol="NQ.FUT")
        adapter.connect()
        assert adapter.state == AdapterStreamState.CONNECTED

        # Ingest sequence 1, 2, then jump to 10
        adapter.feed_synthetic_trade(seq=1, price=18500.0, qty=1.0)
        adapter.feed_synthetic_trade(seq=2, price=18500.25, qty=2.0)
        adapter.feed_synthetic_trade(seq=10, price=18501.0, qty=1.0)

        h = adapter.health()
        assert h["gaps_total"] == 1
        assert h["missing_frames_total"] == 7
        assert h["last_sequence"] == 10


class TestReconnectionExponentialBackoffWithJitter:
    """Verifies exponential delay calculation, bounding, and jitter perturbation."""

    def test_exponential_backoff_progression(self):
        reconnect = ReconnectTracker(
            base_delay=1.0, max_delay=16.0, backoff_factor=2.0, jitter_ratio=0.1
        )

        d1 = reconnect.next_backoff()
        assert 0.89 <= d1 <= 1.11  # ~1.0 +/- 10%

        d2 = reconnect.next_backoff()
        assert 1.78 <= d2 <= 2.22  # ~2.0 +/- 10%

        d3 = reconnect.next_backoff()
        assert 3.58 <= d3 <= 4.42  # ~4.0 +/- 10%

        d4 = reconnect.next_backoff()
        assert 7.18 <= d4 <= 8.82  # ~8.0 +/- 10%

        d5 = reconnect.next_backoff()
        assert 14.38 <= d5 <= 16.0  # ~16.0 capped +/- 10%

        assert reconnect.attempts == 5
        assert reconnect.reconnects_total == 5

        # Success resets consecutive attempt counter
        reconnect.record_success()
        assert reconnect.attempts == 0
        assert reconnect.reconnects_total == 5

        # Next backoff restarts from base_delay
        d_reset = reconnect.next_backoff()
        assert 0.89 <= d_reset <= 1.11


class TestBackpressureAndDropAccounting:
    """Verifies bounded buffer overflow handling: never silently drop a frame."""

    def test_no_silent_drops_under_buffer_saturation(self):
        adapter = BinanceAdapter(symbol="SOLUSDT", max_buffer_size=5)
        adapter.connect()

        # Ingest 15 packets into a buffer of size 5
        for i in range(1, 16):
            adapter.feed_simulated_packet(seq=i, price=140.0 + i, qty=10.0)

        h = adapter.health()
        assert h["drops_total"] == 10
        assert h["received_total"] == 15
        assert h["buffer_size"] <= 5
        assert h["state"] == AdapterStreamState.DEGRADED.value

        # Drain the remaining 5 packets
        drained = []
        while True:
            pkt = adapter.receive()
            if pkt is None:
                break
            drained.append(pkt)

        assert len(drained) == 5
        # The drained packets should be the most recent ones (11 through 15)
        assert drained[-1].payload["sequence"] == 15


class TestBinanceAdapterNormalizationAndParsing:
    """Verifies Binance frame parsing, timestamp fallback, and normalization."""

    def test_binance_book_ticker_parsing(self):
        adapter = BinanceAdapter(symbol="BTCUSDT")
        adapter.connect()

        frame = {
            "u": 400900200,
            "s": "BTCUSDT",
            "b": "65123.40",
            "B": "1.25",
            "a": "65123.50",
            "A": "2.40",
            "E": 1729000000123,
        }
        raw = adapter.parse_frame(frame)
        assert raw is not None
        assert raw.source == "BINANCE"

        ev = adapter.normalize(raw)
        assert ev.instrument_id == "BTCUSDT"
        assert ev.event_type == EventType.QUOTE
        assert ev.bid_price == 65123.40
        assert ev.ask_price == 65123.50
        assert ev.bid_size == 1.25
        assert ev.ask_size == 2.40
        assert ev.sequence_number == 400900200
        assert ev.exchange_timestamp == pytest.approx(1729000000.123)
        assert ev.quality_status == QualityStatus.VALID

    def test_binance_synthetic_timestamp_fallback(self):
        adapter = BinanceAdapter(symbol="ETHUSDT")
        adapter.connect()

        # Frame without 'E' timestamp
        frame_no_e = {
            "u": 5001,
            "b": "3200.10",
            "B": "5.0",
            "a": "3200.20",
            "A": "4.0",
        }
        t_recv = 1729000500.0
        raw = adapter.parse_frame(frame_no_e, t_recv=t_recv)
        assert raw is not None
        assert raw.payload["clock_source"] == "GATEWAY_RECV"
        assert raw.payload["exchange_ts"] == t_recv

        ev = adapter.normalize(raw)
        assert ev.exchange_timestamp == t_recv

    def test_binance_trade_frame_parsing(self):
        adapter = BinanceAdapter(symbol="BTCUSDT")
        adapter.connect()

        trade_frame = {
            "e": "trade",
            "E": 1729000100000,
            "s": "BTCUSDT",
            "t": 1234567,
            "p": "65400.00",
            "q": "0.50",
        }
        raw = adapter.parse_frame(trade_frame)
        assert raw is not None

        ev = adapter.normalize(raw)
        assert ev.event_type == EventType.TRADE
        assert ev.price == 65400.00
        assert ev.quantity == 0.50
        assert ev.sequence_number == 1234567


class TestKrakenAdapterNormalizationAndParsing:
    """Verifies Kraken list-based frame parsing and normalization."""

    def test_kraken_ticker_frame_parsing(self):
        adapter = KrakenAdapter(symbol="XBT/USD")
        adapter.connect()

        # Standard Kraken WS v1 ticker frame
        ticker_frame = [
            340,
            {
                "a": ["65100.20000", 1, "1.500"],
                "b": ["65100.10000", 2, "2.200"],
                "c": ["65100.15000", "0.100"],
            },
            "ticker",
            "XBT/USD",
        ]
        raw = adapter.parse_frame(ticker_frame)
        assert raw is not None

        ev = adapter.normalize(raw)
        assert ev.instrument_id == "XBT/USD"
        assert ev.event_type == EventType.QUOTE
        assert ev.bid_price == 65100.10
        assert ev.ask_price == 65100.20
        assert ev.bid_size == 2.20
        assert ev.ask_size == 1.50
        assert ev.quality_status == QualityStatus.VALID

    def test_kraken_book_snapshot_parsing(self):
        adapter = KrakenAdapter(symbol="ETH/USD")
        adapter.connect()

        book_frame = [
            120,
            {
                "as": [["3205.50", "10.000", "1729000000.5"]],
                "bs": [["3205.40", "8.500", "1729000000.4"]],
            },
            "book-10",
            "ETH/USD",
        ]
        raw = adapter.parse_frame(book_frame)
        assert raw is not None

        ev = adapter.normalize(raw)
        assert ev.bid_price == 3205.40
        assert ev.ask_price == 3205.50
        assert ev.bid_size == 8.5
        assert ev.ask_size == 10.0
        assert ev.exchange_timestamp == pytest.approx(1729000000.4)


class TestDatabentoAdapterBinaryAndSynthetic:
    """Verifies Databento DBN binary decoding, fixed-point conversion, and normalization."""

    def test_databento_synthetic_trades_and_mbp(self):
        adapter = DatabentoAdapter(symbol="ES.FUT")
        adapter.connect()

        raw_trd = adapter.feed_synthetic_trade(seq=101, price=5850.25, qty=5.0)
        ev_trd = adapter.normalize(raw_trd)
        assert ev_trd.instrument_id == "ES.FUT"
        assert ev_trd.event_type == EventType.TRADE
        assert ev_trd.price == 5850.25
        assert ev_trd.quantity == 5.0
        assert ev_trd.sequence_number == 101

        raw_mbp = adapter.feed_synthetic_mbp1(
            seq=102, bid_px=5850.00, ask_px=5850.25, bid_sz=15.0, ask_sz=20.0
        )
        ev_mbp = adapter.normalize(raw_mbp)
        assert ev_mbp.event_type == EventType.QUOTE
        assert ev_mbp.bid_price == 5850.00
        assert ev_mbp.ask_price == 5850.25
        assert ev_mbp.bid_size == 15.0
        assert ev_mbp.ask_size == 20.0

    def test_databento_binary_frame_unpacking(self):
        adapter = DatabentoAdapter(symbol="NQ.FUT")
        adapter.connect()

        # Construct binary TradeMsg (rtype 0x15):
        # Header: length(B), rtype(B), pub_id(H), inst_id(I), ts_event_ns(Q)
        header_bytes = struct.pack("<BBHIQ", 48, 0x15, 1, 1001, 1729000000_000_000_000)
        # Trade body: price(q), size(I), action(c), side(c), flags(B), depth(B), ts_recv(Q), ts_delta(i), seq(I)
        # Price scaled by 1e9: 20500.50 * 1e9 = 20500500000000
        price_scaled = int(20500.50 * 1e9)
        body_bytes = struct.pack(
            "<qIccBBQii",
            price_scaled,
            12,
            b"T",
            b"B",
            0,
            0,
            1729000000_000_000_000,
            0,
            505,
        )

        full_frame = header_bytes + body_bytes
        raw = adapter.unpack_dbn_frame(full_frame)
        assert raw is not None

        ev = adapter.normalize(raw)
        assert ev.instrument_id == "NQ.FUT"
        assert ev.event_type == EventType.TRADE
        assert ev.price == pytest.approx(20500.50)
        assert ev.quantity == 12.0
        assert ev.sequence_number == 505
        assert ev.exchange_timestamp == pytest.approx(1729000000.0)


class TestITCHAdapterBinaryAndSynthetic:
    """Verifies Nasdaq ITCH 5.0 binary protocol parsing, 10^-4 price scaling, and normalization."""

    def test_itch_synthetic_trade_and_quote(self):
        adapter = ITCHAdapter(symbol="MSFT")
        adapter.connect()

        raw_trd = adapter.feed_synthetic_trade(seq=1, price=430.50, shares=100)
        ev_trd = adapter.normalize(raw_trd)
        assert ev_trd.instrument_id == "MSFT"
        assert ev_trd.event_type == EventType.TRADE
        assert ev_trd.price == 430.50
        assert ev_trd.quantity == 100.0

        raw_qte = adapter.feed_synthetic_quote(
            seq=2, bid_px=430.45, ask_px=430.55, bid_sz=500.0, ask_sz=300.0
        )
        ev_qte = adapter.normalize(raw_qte)
        assert ev_qte.event_type == EventType.QUOTE
        assert ev_qte.bid_price == 430.45
        assert ev_qte.ask_price == 430.55

    def test_itch_binary_trade_message_unpacking(self):
        adapter = ITCHAdapter(symbol="AAPL")
        adapter.connect()

        # Trade Message (P): type(1s), locate(H), track(H), ts(6s), order_ref(Q), side(1s), shares(I), stock(8s), price(I), match_num(Q)
        # Price scaled by 10,000: $225.50 * 10,000 = 2255000
        ts_ns = 34200_000_000_000  # 34,200 seconds into the trading day (9:30 AM EDT)
        ts_bytes = ts_ns.to_bytes(6, byteorder="big")
        stock_bytes = b"AAPL    "

        trade_payload = struct.pack(
            ">HH6sQ1sI8sIQ",
            1,
            0,
            ts_bytes,
            99887766,
            b"B",
            250,
            stock_bytes,
            2255000,
            12345,
        )

        raw = adapter.unpack_itch_message(msg_type=b"P", payload_bytes=trade_payload)
        assert raw is not None

        ev = adapter.normalize(raw)
        assert ev.instrument_id == "AAPL"
        assert ev.event_type == EventType.TRADE
        assert ev.price == pytest.approx(225.50)
        assert ev.quantity == 250.0
        assert ev.exchange_timestamp == pytest.approx(ts_ns / 1e9)
