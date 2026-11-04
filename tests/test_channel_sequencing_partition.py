"""Tests for Phase 2: Channel Ingress Sequencing and Lock-Free SPSC Partitioning.

Verifies:
1. Invariant ADR-003: Sequence gap detection and duplicate checking occurs at the
   channel boundary BEFORE symbol partitioning.
2. Interleaved symbols on a shared channel sequence domain do not cause false gaps.
3. True sequence gaps in the feed stream are detected, recorded in GapManager, and flagged.
4. SPSC lock-free ring buffer operates with zero lock contention and strict FIFO order.
5. End-to-end multi-shard fleet preserves per-instrument ordering and feed gap detection.
"""

from __future__ import annotations

import threading
import time
import pytest

from mdrap.gap import GapManager, GapState
from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from mdrap.partition import (
    ChannelIngressSequencer,
    FleetCoordinator,
    ShardConfig,
    SpscRingQueue,
    SymbolPartitioner,
)


def _make_raw(
    seq: int,
    symbol: str,
    venue: str = "NASDAQ",
    channel: str = "ITCH_CH1",
    price: float = 100.0,
) -> RawEvent:
    return RawEvent(
        source=venue,
        payload={
            "venue": venue,
            "channel_id": channel,
            "instrument": symbol,
            "event_type": "TRADE",
            "price": price,
            "size": 10.0,
            "sequence": seq,
            "exchange_ts": 1700000000.0 + seq * 0.001,
        },
        receive_timestamp=1700000000.0 + seq * 0.001,
        raw_id=f"{venue}_{channel}_{seq}",
    )


class TestSpscRingQueue:
    def test_spsc_basic_operations(self):
        q = SpscRingQueue(capacity=4)
        assert q.capacity == 4
        assert q.empty()
        assert q.qsize() == 0

        # Offer items
        assert q.offer("item1")
        assert q.offer("item2")
        assert q.offer("item3")
        assert q.offer("item4")
        assert q.full()
        assert q.qsize() == 4

        # Cannot offer when full
        assert not q.offer("item5")

        # Poll items (FIFO)
        assert q.poll() == "item1"
        assert q.poll() == "item2"
        assert q.qsize() == 2
        assert not q.full()

        # Offer wraps around
        assert q.offer("item5")
        assert q.offer("item6")
        assert q.full()

        assert q.poll() == "item3"
        assert q.poll() == "item4"
        assert q.poll() == "item5"
        assert q.poll() == "item6"
        assert q.empty()
        assert q.poll() is None

    def test_spsc_concurrent_producer_consumer(self):
        q = SpscRingQueue(capacity=1024)
        count = 20_000
        received = []

        def producer():
            for i in range(count):
                while not q.offer(i):
                    # Bounded spin / yield on backpressure
                    pass

        def consumer():
            for _ in range(count):
                while True:
                    val = q.poll()
                    if val is not None:
                        received.append(val)
                        break

        t_prod = threading.Thread(target=producer)
        t_cons = threading.Thread(target=consumer)

        t_cons.start()
        t_prod.start()

        t_prod.join(timeout=5.0)
        t_cons.join(timeout=5.0)

        assert not t_prod.is_alive(), "Producer stalled"
        assert not t_cons.is_alive(), "Consumer stalled"
        assert len(received) == count
        assert received == list(range(count)), (
            "FIFO order violated in concurrent SPSC ring"
        )


class TestChannelIngressSequencer:
    def test_interleaved_symbols_no_false_gaps(self):
        """Verifies ADR-003: Interleaved symbols on sequence 1, 2, 3, 4 do NOT trigger false gaps."""
        gap_mgr = GapManager()
        sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1", gap_manager=gap_mgr)

        events = [
            _make_raw(seq=1, symbol="AAPL"),
            _make_raw(seq=2, symbol="MSFT"),
            _make_raw(seq=3, symbol="TSLA"),
            _make_raw(seq=4, symbol="AAPL"),
            _make_raw(seq=5, symbol="MSFT"),
        ]

        results = []
        for ev in events:
            status, reason = sequencer.verify_sequence(ev)
            results.append((status, reason))

        # All events must be VALID with NO sequence gap
        assert all(st == QualityStatus.VALID for st, _ in results)
        assert all(r is None for _, r in results)
        assert sequencer.gaps_detected == 0
        assert len(gap_mgr._open_gaps) == 0

    def test_true_channel_gap_detected(self):
        """A missing sequence on the feed channel (e.g. 1, 2, 4 - missing 3) must be detected."""
        gap_mgr = GapManager()
        sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1", gap_manager=gap_mgr)

        # Seq 1 (AAPL), Seq 2 (MSFT)
        s1, r1 = sequencer.verify_sequence(_make_raw(seq=1, symbol="AAPL"))
        s2, r2 = sequencer.verify_sequence(_make_raw(seq=2, symbol="MSFT"))
        assert s1 == QualityStatus.VALID
        assert s2 == QualityStatus.VALID

        # Seq 4 (AAPL) -> Gap of seq 3!
        s4, r4 = sequencer.verify_sequence(_make_raw(seq=4, symbol="AAPL"))
        assert s4 == QualityStatus.SUSPICIOUS
        assert r4 == Reason.SEQUENCE_GAP.value
        assert sequencer.gaps_detected == 1
        assert sequencer.missing_events_count == 1

        # GapManager must have recorded the missing sequence 3
        assert len(gap_mgr._open_gaps) == 1
        gap_record = next(iter(gap_mgr._open_gaps.values()))
        assert gap_record.missing_seq == 3
        assert gap_record.received_seq == 4

    def test_duplicate_and_out_of_order_detection(self):
        sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1")

        # In-order seq 1, 2, 3
        sequencer.verify_sequence(_make_raw(seq=1, symbol="AAPL"))
        sequencer.verify_sequence(_make_raw(seq=2, symbol="AAPL"))
        sequencer.verify_sequence(_make_raw(seq=3, symbol="AAPL"))

        # Duplicate seq 2
        st_dup, r_dup = sequencer.verify_sequence(_make_raw(seq=2, symbol="AAPL"))
        assert st_dup == QualityStatus.SUSPICIOUS
        assert r_dup == Reason.DUPLICATE.value
        assert sequencer.duplicates_detected == 1

        # Out of order older sequence 1
        st_ooo, r_ooo = sequencer.verify_sequence(_make_raw(seq=1, symbol="AAPL"))
        assert st_ooo == QualityStatus.SUSPICIOUS
        assert r_ooo in (Reason.DUPLICATE.value, Reason.OUT_OF_ORDER.value)

    def test_session_reset_handling(self):
        sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1")
        sequencer.verify_sequence(_make_raw(seq=100, symbol="AAPL"))
        sequencer.verify_sequence(_make_raw(seq=101, symbol="AAPL"))

        # Upstream exchange reset to seq 1 with explicit session_id="SESSION_2"
        reset_event = _make_raw(seq=1, symbol="AAPL")
        reset_event.payload["session_id"] = "SESSION_2"

        st, r = sequencer.verify_sequence(reset_event)
        assert st == QualityStatus.VALID
        assert r is None
        assert sequencer.expected_sequence == 2


class TestFleetWithChannelSequencing:
    def test_fleet_coordinator_with_spsc_shards(self):
        """End-to-end integration: Channel Sequencer -> Partitioner -> SPSC Shards."""
        partitioner = SymbolPartitioner(num_shards=2, mode="range")
        shard_configs = [
            ShardConfig(
                shard_id=0,
                name="Shard_A_L",
                symbol_prefix_start="A",
                symbol_prefix_end="L",
            ),
            ShardConfig(
                shard_id=1,
                name="Shard_M_Z",
                symbol_prefix_start="M",
                symbol_prefix_end="Z",
            ),
        ]

        fleet = FleetCoordinator(
            partitioner=partitioner, shard_configs=shard_configs, use_spsc=True
        )
        gap_mgr = GapManager()
        sequencer = ChannelIngressSequencer(channel_id="ITCH_CH1", gap_manager=gap_mgr)

        fleet.start_fleet()
        try:
            # 100 interleaved events
            events = []
            for i in range(1, 101):
                sym = "AAPL" if i % 2 == 1 else "MSFT"
                events.append(_make_raw(seq=i, symbol=sym))

            for ev in events:
                status, reason = sequencer.verify_sequence(ev)
                assert status == QualityStatus.VALID
                sym = ev.payload["instrument"]
                shard_id, ok = fleet.dispatch_event(sym, b"dummy_payload")
                assert ok

            # Allow shards to process
            time.sleep(0.1)

            health = fleet.fleet_health()
            assert health["fleet_status"] == "HEALTHY"
            assert health["aggregate_events_processed"] == 100
            assert health["shards"]["shard_0"]["total_processed"] == 50  # AAPL
            assert health["shards"]["shard_1"]["total_processed"] == 50  # MSFT
            assert sequencer.gaps_detected == 0
        finally:
            fleet.stop_fleet()
