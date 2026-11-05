"""
MDRAP v5.1 — Direct Wire-Byte Ingestion & Normalization Test Suite (ADR-007).

Tests:
1. Zero-dict normalization of CME SBE binary wire frames.
2. Direct normalization of NASDAQ TotalView-ITCH 5.0 binary frames.
3. WAL append and replay round-trip fidelity with raw wire payloads.
4. Direct wire performance vs dictionary-based normalization.
"""

import time
import pytest

from mdrap.gateway import Gateway
from mdrap.ingestlog import IngestLog
from mdrap.models import CanonicalEvent, EventType, RawEvent
from mdrap.sbe import (
    pack_sbe_tick,
    SBETickMessage,
)
from mdrap.itch import (
    ITCHParser,
    STRUCT_A,
    MSG_ADD_ORDER,
    PRICE_FACTOR_ITCH,
)


class TestDirectWireIngestion:
    """Verifies ADR-007 Direct Wire-Byte Ingestion and Fastpath Normalization."""

    def test_sbe_direct_wire_normalization(self):
        """Verify CME SBE wire bytes are decoded directly into CanonicalEvent."""
        gw = Gateway()

        # Pack a valid SBE tick binary frame
        sbe_wire = pack_sbe_tick(
            seq=101,
            symbol="AAPL",
            source="CME",
            price=150.25,
            size=50.0,
            bid=150.20,
            ask=150.30,
            bid_size=100.0,
            ask_size=200.0,
            status="VALID",
            is_crossed=False,
            exchange_ts=1700000000.123,
            ingest_ts=1700000000.124,
            broadcast_ts=1700000000.125,
            engine_us=1.5,
        )
        assert isinstance(sbe_wire, bytes)

        # Ingest as RawEvent with wire bytes and empty payload dict
        raw = RawEvent(source="CME", payload={}, wire=sbe_wire, session_id="SESS_1")
        canonical = gw.normalize(raw)

        assert isinstance(canonical, CanonicalEvent)
        assert canonical.source == "CME"
        assert canonical.instrument_id == "AAPL"
        assert canonical.price == 150.25
        assert canonical.quantity == 50.0
        assert canonical.bid_price == 150.20
        assert canonical.ask_price == 150.30
        assert canonical.sequence_number == 101
        assert canonical.exchange_timestamp == 1700000000.123
        assert canonical.session_id == "SESS_1"

    def test_itch_direct_wire_normalization(self):
        """Verify NASDAQ ITCH wire bytes are decoded directly into CanonicalEvent."""
        gw = Gateway()

        # Construct a NASDAQ ITCH Add Order ('A') message body:
        # locate(H), tracking(H), ts(6s), order_ref(Q), buy_sell(c), shares(I), stock(8s), price(I)
        # Nanoseconds since midnight (e.g. 10:00:00 AM)
        ts_bytes = (36_000_000_000_000).to_bytes(6, "big")
        body = STRUCT_A.pack(
            1,                      # locate
            42,                     # tracking / seq
            ts_bytes,               # 6-byte ts
            999999,                 # order_ref
            b"B",                   # buy
            500,                    # shares
            b"MSFT    ",            # stock
            int(300.50 * PRICE_FACTOR_ITCH),  # price fixed point
        )
        # Prepend 2-byte frame length (len(body) + 1 for type byte) and type byte b'A'
        frame_len = len(body) + 1
        itch_wire = frame_len.to_bytes(2, "big") + MSG_ADD_ORDER + body

        raw = RawEvent(source="NASDAQ", payload={}, wire=itch_wire, session_id="ITCH_SESS")
        canonical = gw.normalize(raw)

        assert isinstance(canonical, CanonicalEvent)
        assert canonical.source == "NASDAQ"
        assert canonical.instrument_id == "MSFT"
        assert canonical.price == 300.50
        assert canonical.quantity == 500.0
        assert canonical.sequence_number == 42
        assert canonical.event_type == EventType.QUOTE

    def test_wal_wire_roundtrip_fidelity(self, tmp_path):
        """Verify raw wire events can be appended and replayed via WAL."""
        log_dir = str(tmp_path / "wal_wire_test")
        log = IngestLog(log_dir=log_dir, fsync_policy="never")

        try:
            # Create SBE wire event
            wire_bytes = pack_sbe_tick(
                seq=555,
                symbol="TSLA",
                source="CME",
                price=99.99,
                size=10.0,
                bid=99.95,
                ask=100.05,
                bid_size=10.0,
                ask_size=10.0,
                status="VALID",
                is_crossed=False,
                exchange_ts=1700000000.5,
                ingest_ts=1700000000.51,
                broadcast_ts=1700000000.52,
                engine_us=1.0,
            )
            raw = RawEvent(source="CME", payload={}, wire=wire_bytes, raw_id="RAW_555", session_id="S55")

            offset = log.append(raw)
            log.flush()

            # Replay and verify wire reconstruction
            records = list(log.iter_from(offset))
            assert len(records) == 1
            off, replayed_raw = records[0]

            assert off == offset
            assert replayed_raw.raw_id == "RAW_555"
            assert replayed_raw.source == "CME"
            assert replayed_raw.session_id == "S55"
            assert replayed_raw.wire == wire_bytes

            # Normalize replayed event
            gw = Gateway()
            canonical = gw.normalize(replayed_raw)
            assert canonical.instrument_id == "TSLA"
            assert canonical.price == 99.99
            assert canonical.sequence_number == 555

        finally:
            log.close()
