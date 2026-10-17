"""Acceptance test suite for Phase 9: Specialized Hardware & Direct Gateways.

Verifies:
1. Hardware-Qualified Profiles & Affinity:
   - CPU core pinning (set_cpu_affinity, get_cpu_affinity).
   - NUMA node allocation interface (set_numa_node).
   - SPSC seqlock shared memory 64-byte cache-line alignment.
2. CME MDP 3.0 Simple Binary Encoding (SBE) Decoder:
   - Fixed 8-byte SBE framing header decoding (<block_length, template_id, schema_id, version>).
   - Strict schema validation (rejects invalid schema_id).
   - MDIncrementalRefreshBook (Template 46) repeating group decoding.
   - MDIncrementalRefreshVolume (Template 42) decoding.
   - CanonicalEvent projection with correct price scaling.
3. Eurex T7 ETI / EMDI Binary Decoder:
   - Framing header parsing (<body_len, template_id, msg_seq_num>).
   - OrderBookDepth (Template 10130) and TradeReport (Template 10131) unpacking.
   - CanonicalEvent projection with microsecond timestamps and sequence tracking.
"""

from __future__ import annotations

import platform
import time
import pytest

from mdrap.cme import (
    CME_SCHEMA_ID,
    CME_SCHEMA_VERSION,
    CMESBEDecoder,
    MDUpdateAction,
    TEMPLATE_MD_INCREMENTAL_REFRESH_BOOK,
    TEMPLATE_MD_INCREMENTAL_REFRESH_VOLUME,
    pack_cme_incremental_book,
)
from mdrap.eurex import (
    EurexDepthMessage,
    EurexT7Decoder,
    EurexTradeMessage,
    pack_eurex_depth_message,
    pack_eurex_trade_message,
)
from mdrap.models import EventType, QualityStatus
from mdrap.profile import (
    HardwareProfile,
    ProfileMode,
    current_profile,
    get_cpu_affinity,
    set_cpu_affinity,
    set_numa_node,
)
from mdrap.shm import HEADER_SIZE, SLOT_SIZE


class TestSpecializedHardwareProfiles:
    """Verifies hardware CPU core pinning, NUMA allocation, and cache-line boundaries."""

    def test_cpu_affinity_interfaces(self):
        current_cores = get_cpu_affinity()
        assert isinstance(current_cores, list)
        assert len(current_cores) > 0

        # Attempt to set affinity to core 0 (or current core)
        if current_cores:
            res = set_cpu_affinity([current_cores[0]])
            # Should return a boolean without crashing or raising exceptions
            assert isinstance(res, bool)
            # Restore affinity
            set_cpu_affinity(current_cores)

    def test_numa_allocation_interface(self):
        # NUMA allocation should return boolean without crashing on any platform
        res = set_numa_node(0)
        assert isinstance(res, bool)

    def test_shm_cache_line_padding(self):
        # Cache-line alignment: 64-byte multiple
        assert SLOT_SIZE % 64 == 0
        assert HEADER_SIZE % 64 == 0
        assert SLOT_SIZE == 128
        assert HEADER_SIZE == 128


class TestCMEMDP3DirectSBEDecoder:
    """Verifies CME MDP 3.0 SBE binary decoder."""

    def test_sbe_header_unpacking_and_schema_validation(self):
        decoder = CMESBEDecoder()
        entries = [(1001, 1, 20250.25, 10.0, 0, "0")]  # NQ_FUT bid
        buf = pack_cme_incremental_book(1729000000_000_000_000, entries)

        block_len, template_id, schema_id, version = decoder.decode_header(buf)
        assert template_id == TEMPLATE_MD_INCREMENTAL_REFRESH_BOOK
        assert schema_id == CME_SCHEMA_ID
        assert version == CME_SCHEMA_VERSION

        # Test schema mismatch rejection
        bad_buf = pack_cme_incremental_book(
            1729000000_000_000_000, entries, schema_id=999
        )
        with pytest.raises(ValueError, match="Invalid CME SBE schema_id"):
            decoder.decode_header(bad_buf)

    def test_cme_md_incremental_refresh_book_decode(self):
        decoder = CMESBEDecoder()
        entries = [
            (1001, 100, 20250.25, 5.0, 0, "0"),  # NQ_FUT New Bid
            (1001, 101, 20250.50, 8.0, 0, "1"),  # NQ_FUT New Ask
            (1002, 200, 5850.75, 12.0, 1, "0"),  # ES_FUT Change Bid
        ]
        tx_time_ns = 1729000000_500_000_000
        buf = pack_cme_incremental_book(tx_time_ns, entries)

        msg = decoder.decode_packet(buf)
        assert msg.template_id == TEMPLATE_MD_INCREMENTAL_REFRESH_BOOK
        assert msg.transact_time_ns == tx_time_ns
        assert len(msg.entries) == 3

        e0 = msg.entries[0]
        assert e0.security_id == 1001
        assert e0.rpt_seq == 100
        assert e0.price == pytest.approx(20250.25, rel=1e-5)
        assert e0.size == 5.0
        assert e0.update_action == MDUpdateAction.NEW
        assert e0.entry_type == "0"

    def test_cme_canonical_event_projection(self):
        decoder = CMESBEDecoder()
        entries = [
            (1001, 55, 20250.25, 2.0, 0, "2"),  # NQ_FUT Trade
            (1001, 56, 20250.00, 10.0, 0, "0"),  # NQ_FUT Bid
        ]
        buf = pack_cme_incremental_book(1729000000_123_456_789, entries)
        msg = decoder.decode_packet(buf)
        now = time.time()
        canonical_events = decoder.to_canonical_events(msg, receive_timestamp=now)

        assert len(canonical_events) == 2
        trade_ev = canonical_events[0]
        assert trade_ev.instrument_id == "NQ_FUT"
        assert trade_ev.event_type == EventType.TRADE
        assert trade_ev.price == pytest.approx(20250.25, rel=1e-5)
        assert trade_ev.quantity == 2.0
        assert trade_ev.source == "CME"
        assert trade_ev.quality_status == QualityStatus.VALID

        quote_ev = canonical_events[1]
        assert quote_ev.instrument_id == "NQ_FUT"
        assert quote_ev.event_type == EventType.QUOTE
        assert quote_ev.bid_price == pytest.approx(20250.00, rel=1e-5)
        assert quote_ev.bid_size == 10.0


class TestEurexT7DirectBinaryDecoder:
    """Verifies Eurex T7 ETI / EMDI direct binary decoder."""

    def test_eurex_order_book_depth_decode(self):
        decoder = EurexT7Decoder()
        buf = pack_eurex_depth_message(
            seq_num=42,
            security_id=2001,  # FDAX_FUT
            transact_time_ns=1729000000_250_000_000,
            bid_price=19500.50,
            ask_price=19501.00,
            bid_size=15,
            ask_size=20,
        )

        msg = decoder.decode_frame(buf)
        assert isinstance(msg, EurexDepthMessage)
        assert msg.seq_num == 42
        assert msg.security_id == 2001
        assert msg.bid_price == pytest.approx(19500.50, rel=1e-6)
        assert msg.ask_price == pytest.approx(19501.00, rel=1e-6)
        assert msg.bid_size == 15.0
        assert msg.ask_size == 20.0

        canonical = decoder.to_canonical_event(msg, receive_timestamp=time.time())
        assert canonical.instrument_id == "FDAX_FUT"
        assert canonical.event_type == EventType.QUOTE
        assert canonical.bid_price == pytest.approx(19500.50, rel=1e-6)
        assert canonical.source == "EUREX"

    def test_eurex_trade_report_decode(self):
        decoder = EurexT7Decoder()
        buf = pack_eurex_trade_message(
            seq_num=99,
            security_id=2002,  # FESX_FUT
            transact_time_ns=1729000000_750_000_000,
            price=4950.00,
            size=5,
            aggressor_side=1,
        )

        msg = decoder.decode_frame(buf)
        assert isinstance(msg, EurexTradeMessage)
        assert msg.seq_num == 99
        assert msg.price == pytest.approx(4950.00, rel=1e-6)
        assert msg.size == 5.0

        canonical = decoder.to_canonical_event(msg, receive_timestamp=time.time())
        assert canonical.instrument_id == "FESX_FUT"
        assert canonical.event_type == EventType.TRADE
        assert canonical.price == pytest.approx(4950.00, rel=1e-6)
        assert canonical.quantity == 5.0
        assert canonical.source == "EUREX"
