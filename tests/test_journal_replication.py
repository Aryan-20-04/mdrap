"""Unit tests for MDRAP Streaming Journal Replication Daemon & Epoch Fencing."""

import time
import pytest
from mdrap.failover import FailoverNode, NodeState
from mdrap.journal_replicator import (
    JournalReplicationClient,
    JournalReplicationServer,
    MSG_ACK,
    MSG_FRAME_DATA,
    REPL_HEADER_SIZE,
    pack_replication_frame,
    unpack_replication_header,
)


def test_replication_frame_pack_unpack_roundtrip():
    """Verify replication frame serialization, 32-byte header unpacking, and CRC verification."""
    payload = b'{"event_id": "e1", "price": 100.5, "symbol": "AAPL"}'
    seq = 42
    epoch = 3

    frame = pack_replication_frame(MSG_FRAME_DATA, seq, epoch, payload)
    assert len(frame) == REPL_HEADER_SIZE + len(payload)

    hdr = frame[:REPL_HEADER_SIZE]
    ver, msg_type, out_seq, out_epoch, length, expected_crc = unpack_replication_header(
        hdr
    )

    assert ver == 1
    assert msg_type == MSG_FRAME_DATA
    assert out_seq == seq
    assert out_epoch == epoch
    assert length == len(payload)


def test_replication_client_server_streaming():
    """Verify streaming replication of WAL frames over TCP with synchronous ACK."""
    # Find free port or use high ephemeral port
    port = 19988
    received_payloads = []

    class MockTargetLog:
        def append_raw(self, data: bytes):
            received_payloads.append(data)

    server = JournalReplicationServer(port=port, target_log=MockTargetLog())
    server.start()
    time.sleep(0.05)  # Wait for socket bind

    try:
        client = JournalReplicationClient(target_port=port, epoch=1)
        assert client.connect() is True

        payload1 = b"RECORD_1_BINANCE_BTCUSDT"
        payload2 = b"RECORD_2_KRAKEN_ETHUSD"

        assert client.send_frame(sequence=1, payload=payload1, wait_ack=True) is True
        assert client.send_frame(sequence=2, payload=payload2, wait_ack=True) is True

        time.sleep(0.05)
        assert len(received_payloads) == 2
        assert received_payloads[0] == payload1
        assert received_payloads[1] == payload2

        assert client.stats.frames_sent == 2
        assert client.stats.acks_received == 2
        assert server.stats.frames_received == 2
        assert server.stats.last_seq == 2
    finally:
        client.close()
        server.stop()


def test_replication_stale_epoch_rejection():
    """Verify server rejects frames with an epoch older than the failover node's current epoch."""
    port = 19989
    failover_node = FailoverNode(node_id="standby-1", initial_epoch=5)
    server = JournalReplicationServer(port=port, failover_node=failover_node)
    server.start()
    time.sleep(0.05)

    try:
        # Client attempts replication with stale epoch 4 (< 5)
        client = JournalReplicationClient(target_port=port, epoch=4)
        assert client.connect() is True

        res = client.send_frame(sequence=100, payload=b"STALE_PAYLOAD", wait_ack=True)
        assert res is False

        time.sleep(0.05)
        assert server.stats.stale_epoch_rejections == 1
    finally:
        client.close()
        server.stop()


def test_replication_client_offline_spooling_and_catchup():
    """Verify frames are safely spooled while standby is offline and drained when standby connects."""
    port = 19990
    received_payloads = []

    class MockTargetLog:
        def append_raw(self, data: bytes):
            received_payloads.append(data)

    client = JournalReplicationClient(
        target_port=port,
        epoch=1,
        reconnect_backoff_s=0.01,
        max_backoff_s=0.1,
    )

    # 1. Standby is completely offline. Send 3 frames: all must be spooled!
    assert client.send_frame(sequence=10, payload=b"SPOOLED_1") is False
    assert client.send_frame(sequence=11, payload=b"SPOOLED_2") is False
    assert client.send_frame(sequence=12, payload=b"SPOOLED_3") is False

    assert client.stats.spool_queued == 3
    assert client.stats.spool_dropped == 0

    # 2. Standby server now comes online
    server = JournalReplicationServer(port=port, target_log=MockTargetLog())
    server.start()
    time.sleep(0.05)

    try:
        # Reset reconnect backoff so it can connect immediately
        client._next_reconnect_ts = 0.0
        drained = client.flush_spool()
        assert drained == 3
        assert client.stats.spool_queued == 0

        time.sleep(0.05)
        assert len(received_payloads) == 3
        assert received_payloads[0] == b"SPOOLED_1"
        assert received_payloads[1] == b"SPOOLED_2"
        assert received_payloads[2] == b"SPOOLED_3"
    finally:
        client.close()
        server.stop()


def test_replication_spool_capacity_drop():
    """Verify spool queue drops oldest frames when max_spool_size is exceeded."""
    client = JournalReplicationClient(
        target_port=19991,
        epoch=1,
        max_spool_size=2,
        reconnect_backoff_s=10.0,  # Ensure no reconnect attempt
    )

    client.send_frame(sequence=1, payload=b"P1")
    client.send_frame(sequence=2, payload=b"P2")
    assert client.stats.spool_queued == 2
    assert client.stats.spool_dropped == 0

    # 3rd frame forces eviction of oldest frame (P1)
    client.send_frame(sequence=3, payload=b"P3")
    assert client.stats.spool_queued == 2
    assert client.stats.spool_dropped == 1
    assert client._spool_queue[0][1] == b"P2"
    assert client._spool_queue[1][1] == b"P3"
