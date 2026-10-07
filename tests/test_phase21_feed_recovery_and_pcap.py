"""Institutional Verification Suite for Phase 21: Feed Recovery State Machine & PCAP Replay Harness.

Validates:
- Full Feed State Machine lifecycle: DISCONNECTED -> CONNECTING -> SNAPSHOT -> LIVE.
- Sequence gap detection: transitions to GAP_DETECTED and RECOVERING.
- Reconnect and incremental replay stitching: seamless sequence healing back to LIVE.
- Stale snapshot detection and rejection: protects against backwards time jumps.
- Recovery failure handling: buffer overflow locks into FAILED_RECOVERY.
- PCAP 2.4 generation and parsing: validates microsecond timestamp alignment and header dissection.
- MoldUDP64 dissection: parses multi-message frames with NASDAQ ITCH payloads.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from recovery import (
    FeedRecoveryEngine,
    FeedState,
    FeedPacket,
)
from pcap import (
    PcapWriter,
    PcapReader,
    CapturedPacket,
    MoldUDP64Dissector,
    MOLDUDP64_HEADER_STRUCT,
)
from itch import (
    ITCHParser,
    ITCHOrderBookTracker,
    STRUCT_A,
    MSG_ADD_ORDER,
    PRICE_FACTOR_ITCH,
)


def test_feed_recovery_normal_lifecycle():
    """Verify normal connection: DISCONNECTED -> CONNECTING -> SNAPSHOT -> LIVE."""
    engine = FeedRecoveryEngine(channel_id="NASDAQ_CH1")
    assert engine.state == FeedState.DISCONNECTED

    engine.connect(require_snapshot=True)
    assert engine.state == FeedState.SNAPSHOT

    # Accept valid initial snapshot at seq 100
    ok = engine.on_snapshot(snapshot_seq=100, snapshot_ts=1000.0)
    assert ok is True
    assert engine.state == FeedState.LIVE
    assert engine.expected_seq == 101

    # In-order packet arrives at seq 101
    pkt101 = FeedPacket("NASDAQ_CH1", 101, b"tick_101", 1000.1)
    dispatched = engine.on_packet(pkt101)
    assert len(dispatched) == 1
    assert dispatched[0].sequence_num == 101
    assert engine.expected_seq == 102
    assert engine.state == FeedState.LIVE


def test_feed_recovery_sequence_gap_and_replay_stitching():
    """Verify sequence gap detection, RECOVERING transition, and backfill stitching."""
    replayed_storage = {
        102: FeedPacket("NASDAQ_CH1", 102, b"tick_102", 1000.2),
        103: FeedPacket("NASDAQ_CH1", 103, b"tick_103", 1000.3),
    }

    def mock_replay_client(ch: str, start: int, end: int) -> list[FeedPacket]:
        return [
            replayed_storage[s] for s in range(start, end + 1) if s in replayed_storage
        ]

    engine = FeedRecoveryEngine(
        channel_id="NASDAQ_CH1",
        initial_seq=101,
        replay_handler=mock_replay_client,
    )
    engine.connect(require_snapshot=False)
    assert engine.state == FeedState.LIVE

    # Packet 101 arrives in-order
    p101 = FeedPacket("NASDAQ_CH1", 101, b"tick_101", 1000.1)
    out1 = engine.on_packet(p101)
    assert len(out1) == 1
    assert engine.expected_seq == 102

    # Packet 104 arrives! Sequence gap [102..103] detected!
    p104 = FeedPacket("NASDAQ_CH1", 104, b"tick_104", 1000.4)
    out_gap = engine.on_packet(p104)

    # Gap resolution: replay handler retrieved [102, 103] and drained [102, 103, 104]
    assert len(out_gap) == 0  # Replay invoked synchronously, packets queued or drained
    # State transitions: GAP_DETECTED -> RECOVERING -> LIVE
    assert engine.state == FeedState.LIVE
    assert engine.expected_seq == 105
    assert engine.metrics.gaps_detected == 1
    assert engine.metrics.replays_completed == 1
    assert engine.metrics.in_order_dispatched == 4


def test_feed_recovery_stale_snapshot_rejection():
    """Verify outdated snapshots are rejected with STALE_SNAPSHOT state."""
    engine = FeedRecoveryEngine(channel_id="CME_CH1")
    engine.connect(require_snapshot=True)

    # Accept initial snapshot at t=1000.0
    assert engine.on_snapshot(snapshot_seq=50, snapshot_ts=1000.0) is True
    assert engine.state == FeedState.LIVE

    # Ingest packet at t=1005.0
    engine.on_packet(FeedPacket("CME_CH1", 51, b"p51", 1005.0))

    # Stale snapshot arrives with t=999.0 (< 1005.0)
    rejected = engine.on_snapshot(snapshot_seq=60, snapshot_ts=999.0)
    assert rejected is False
    assert engine.state == FeedState.STALE_SNAPSHOT
    assert engine.metrics.stale_snapshots_rejected == 1


def test_feed_recovery_buffer_saturation_failure():
    """Verify gap buffer overflow transitions to FAILED_RECOVERY without silent sequence skip."""
    engine = FeedRecoveryEngine(channel_id="CH1", initial_seq=1, max_buffer_size=3)
    engine.connect(require_snapshot=False)

    # Send packets skipping 1: seq 2, 3, 4
    engine.on_packet(FeedPacket("CH1", 2, b"p2", 100.0))
    engine.on_packet(FeedPacket("CH1", 3, b"p3", 101.0))
    engine.on_packet(FeedPacket("CH1", 4, b"p4", 102.0))

    # 4th out-of-order packet exceeds max_buffer_size=3
    engine.on_packet(FeedPacket("CH1", 5, b"p5", 103.0))
    assert engine.state == FeedState.FAILED_RECOVERY
    assert engine.metrics.recovery_failures >= 1


def test_pcap_writer_and_reader_roundtrip():
    """Verify Libpcap 2.4 Ethernet/IPv4/UDP packet encapsulation and parsing roundtrip."""
    with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as tmp:
        pcap_path = tmp.name

    try:
        t_base = 1700000000.123456
        test_payloads = [
            b"FIRST_UDP_PAYLOAD_123456",
            b"SECOND_UDP_PAYLOAD_ABCDEF",
            b"THIRD_UDP_PAYLOAD_XYZZY",
        ]

        with PcapWriter(pcap_path) as writer:
            for idx, payload in enumerate(test_payloads):
                writer.write_udp_packet(
                    payload=payload,
                    timestamp=t_base + idx * 0.001,
                    src_ip="10.0.0.1",
                    dst_ip="239.255.0.1",
                    src_port=40000 + idx,
                    dst_port=12345,
                )

        # Read back using PcapReader
        with PcapReader(pcap_path) as reader:
            captured = list(reader.packets())

        assert len(captured) == 3
        for idx, (cap, orig_payload) in enumerate(zip(captured, test_payloads)):
            assert cap.payload == orig_payload
            assert cap.src_ip == "10.0.0.1"
            assert cap.dst_ip == "239.255.0.1"
            assert cap.dst_port == 12345
            assert cap.src_port == 40000 + idx
            assert cap.timestamp == pytest.approx(t_base + idx * 0.001, abs=1e-5)
    finally:
        if os.path.exists(pcap_path):
            os.remove(pcap_path)


def test_pcap_moldudp64_dissection_with_itch_integration():
    """Verify PCAP replay parses MoldUDP64 multicast packets into ITCH 5.0 book updates."""
    # Synthesize ITCH 5.0 Add Order (Type A)
    ts_b = (34_200_000_000_000).to_bytes(6, "big")
    itch_body = STRUCT_A.pack(
        1, 0, ts_b, 9001, b"B", 1000, b"NVDA    ", int(130.50 * PRICE_FACTOR_ITCH)
    )
    itch_msg = MSG_ADD_ORDER + itch_body

    # Encapsulate in MoldUDP64: 20B Header + 2B Length + ITCH payload
    mold_hdr = MOLDUDP64_HEADER_STRUCT.pack(b"SESSION001", 1001, 1)
    mold_payload = mold_hdr + (len(itch_msg)).to_bytes(2, "big") + itch_msg

    with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as tmp:
        pcap_path = tmp.name

    try:
        with PcapWriter(pcap_path) as writer:
            writer.write_udp_packet(mold_payload, timestamp=1700000000.0)

        # Replay and dissect
        book_tracker = ITCHOrderBookTracker()
        with PcapReader(pcap_path) as reader:
            for cap in reader.packets():
                sess, seq, msgs = MoldUDP64Dissector.dissect(cap.payload)
                assert sess == "SESSION001"
                assert seq == 1001
                assert len(msgs) == 1

                # Feed dissected message to ITCH parser
                msg_raw = msgs[0]
                parsed_msg = ITCHParser.parse_payload(msg_raw[:1], msg_raw[1:])
                assert parsed_msg is not None
                book_tracker.process_message(parsed_msg)

        assert 9001 in book_tracker.orders
        assert book_tracker.depth["NVDA"]["B"][130.50] == 1000
    finally:
        if os.path.exists(pcap_path):
            os.remove(pcap_path)
