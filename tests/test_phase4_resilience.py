"""Phase 4 Resilience, Chaos Engineering & Disaster Recovery Test Suite.

Verifies system survivability and recovery invariants under simulated faults:
  1. WAL partial-write corruption & mid-segment truncation recovery.
  2. SBE wire layout corruption & malformed binary payload rejection.
  3. Active-Passive failover fencing, split-brain rejection, and epoch stepping.
  4. Transport disconnect / reconnect resilience and sequence gap audit.
"""

import os
import struct
import tempfile
import time
import pytest
from mdrap.failover import FailoverNode, HeartbeatMessage, NodeState, StaleEpochError
from mdrap.ingress import FeedAdapterConfig, ReplayFeedAdapter
from mdrap.ingestlog import IngestLog
from mdrap.models import EventType, QualityStatus, RawEvent
from mdrap.quality import QualityEngine


def test_wal_partial_write_truncation_recovery(tmp_path):
    """WAL reader cleanly recovers up to the last valid record when trailing bytes are truncated."""
    wal_dir = str(tmp_path / "wal_trunc")
    os.makedirs(wal_dir, exist_ok=True)

    log = IngestLog(log_dir=wal_dir, max_segment_bytes=1024 * 1024)

    # Append 5 valid records
    for seq in range(1, 6):
        raw = RawEvent(
            raw_id=f"rec_{seq}",
            source="NASDAQ",
            receive_timestamp=time.time(),
            payload={"seq": seq, "sym": "AAPL", "px": 150.0 + seq},
        )
        log.append(raw)

    log.flush()
    log.close()

    # Find the active segment file
    seg_files = [os.path.join(wal_dir, f) for f in os.listdir(wal_dir) if f.startswith("segment_") and f.endswith(".log")]
    assert len(seg_files) == 1
    seg_path = seg_files[0]
    orig_size = os.path.getsize(seg_path)

    # Corrupt by truncating the last 15 bytes of the last record
    with open(seg_path, "r+b") as f:
        f.truncate(orig_size - 15)

    # Reopen WAL
    reopened = IngestLog(log_dir=wal_dir)
    recovered_records = []
    for off, rec in reopened.iter_from(0):
        recovered_records.append(rec)
    reopened.close()

    # IngestLog must successfully read the first 4 valid records without crashing on the truncated 5th record
    assert len(recovered_records) == 4
    assert recovered_records[0].payload["seq"] == 1
    assert recovered_records[3].payload["seq"] == 4


def test_sbe_corrupted_wire_frame_handling():
    """System validates SBE frames, rejecting undersized buffers and malformed payload values."""
    # 1. Undersized buffer (< 64 bytes)
    short_buf = b"\x00" * 48
    with pytest.raises(struct.error):
        struct.unpack("<16sQqddII8s", short_buf)

    # 2. Corrupted frame with NaN price
    nan_price = float("nan")
    inst_b = b"AAPL" + b"\x00" * 12
    corrupt_frame = struct.pack(
        "<16sQqddII8s",
        inst_b,
        1001,
        1728475200000000000,
        nan_price,
        100.0,
        1,  # TRADE
        0,  # VALID
        b"\x00" * 8,
    )
    assert len(corrupt_frame) == 64

    # Unpack frame and verify QualityEngine catches NaN price as invalid/suspicious
    u_inst, u_seq, u_ts, u_px, u_qty, u_type, u_q, _ = struct.unpack("<16sQqddII8s", corrupt_frame)
    quality = QualityEngine()
    from mdrap.models import CanonicalEvent
    ev = CanonicalEvent(
        event_id="test_nan",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=100.0,
        receive_timestamp=100.0,
        processing_timestamp=100.0,
        source="NASDAQ",
        sequence_number=u_seq,
        price=u_px,
        quantity=u_qty,
    )
    evaluated = quality.evaluate(ev)
    # Price NaN must not be marked clean VALID
    assert evaluated.quality_status != QualityStatus.VALID


def test_failover_split_brain_fencing():
    """Primary promotion generates newer epoch; stale messages from demoted node are fenced out."""
    primary_1 = FailoverNode("node-1", initial_state=NodeState.PRIMARY, initial_epoch=1)
    standby_2 = FailoverNode("node-2", initial_state=NodeState.STANDBY, initial_epoch=1)

    # Standby records primary heartbeats
    hb1 = primary_1.send_heartbeat(last_committed_seq=1000)
    assert standby_2.receive_heartbeat(hb1) is True

    # Standby promotes due to silence
    standby_2.promote(reason="failover_test")
    assert standby_2.state == NodeState.PRIMARY
    assert standby_2.epoch == 2
    assert standby_2.fencing_token == 2

    # Now isolated node-1 attempts to send heartbeat with stale epoch 1
    stale_hb = primary_1.send_heartbeat(last_committed_seq=1005)
    assert stale_hb.epoch == 1

    # Standby_2 must reject stale heartbeat from node-1
    accepted = standby_2.receive_heartbeat(stale_hb)
    assert accepted is False

    # Stale node-1 attempting write with fencing token 1 raises StaleEpochError
    with pytest.raises(StaleEpochError):
        standby_2.assert_fencing_token(stale_hb.fencing_token)
    standby_2.assert_fencing_token(2)


def test_ingress_disconnect_reconnect_audit():
    """Ingress adapter safely handles reconnects and audits missing sequences caused by network drops."""
    adapter = ReplayFeedAdapter(
        FeedAdapterConfig(venue="NASDAQ", feed_id="ITCH_CH1", gap_detection_enabled=True),
        frames=[
            {"seq": 100, "sym": "TSLA", "px": 250.0, "sz": 10.0, "type": "TRADE"},
            {"seq": 101, "sym": "TSLA", "px": 250.2, "sz": 10.0, "type": "TRADE"},
            # Simulated network drop: seq 102, 103, 104 lost in transit
            {"seq": 105, "sym": "TSLA", "px": 251.0, "sz": 15.0, "type": "TRADE"},
            {"seq": 106, "sym": "TSLA", "px": 251.1, "sz": 20.0, "type": "TRADE"},
        ],
    )
    adapter.connect()

    # Poll first two
    r1 = adapter.poll()
    r2 = adapter.poll()
    assert r1 is not None and r2 is not None
    assert adapter.stats.gaps_detected == 0

    # Poll post-drop frame (seq 105)
    r3 = adapter.poll()
    assert r3 is not None
    assert adapter.stats.gaps_detected == 1
    assert adapter.stats.missing_events_count == 3  # sequences 102, 103, 104

    # Disconnect and reconnect
    adapter.disconnect()
    assert adapter.state.value == "CLOSED"
    adapter.connect()
    assert adapter.state.value == "CONNECTED"

    # Poll final frame
    r4 = adapter.poll()
    assert r4 is not None
    assert r4.payload["seq"] == 106
    adapter.disconnect()
