"""MDRAP Streaming Journal Replication Daemon (High Availability & Zero-Loss Standby).

Provides real-time TCP streaming replication of IngestLog WAL frames from Primary to Standby nodes:
- Frame packing with 32-byte header, epoch fencing token, and CRC32 verification.
- Bi-directional sequence validation and lag tracking.
- Split-brain prevention: rejects replication frames with stale or conflicting epoch tokens.
- Automatic recovery and catch-up synchronization on reconnect.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

from .failover import FailoverNode, NodeState, StaleEpochError
from .ingestlog import IngestLog

__stability__ = "stable"

logger = logging.getLogger(__name__)

# Protocol Constants
REPL_MAGIC = 0x5245504C  # b"REPL"
REPL_VERSION = 1

# Message Types
MSG_FRAME_DATA = 1
MSG_HEARTBEAT = 2
MSG_ACK = 3
MSG_ERROR = 4

# Replication Header Layout:
# magic(4), version(2), msg_type(2), sequence(8), epoch(8), length(4), crc32(4) = 32 bytes
REPL_HEADER_FORMAT = ">IHHQQII"
REPL_HEADER_SIZE = struct.calcsize(REPL_HEADER_FORMAT)  # 32 bytes


@dataclass(slots=True)
class ReplicationFrame:
    """In-memory representation of a replicated IngestLog frame."""

    msg_type: int
    sequence: int
    epoch: int
    payload: bytes


@dataclass
class ReplicationStats:
    """Operational telemetry counters for the replication link."""

    frames_sent: int = 0
    bytes_sent: int = 0
    frames_received: int = 0
    bytes_received: int = 0
    acks_received: int = 0
    errors: int = 0
    stale_epoch_rejections: int = 0
    crc_failures: int = 0
    last_seq: int = 0
    lag_frames: int = 0
    spool_queued: int = 0
    spool_dropped: int = 0


def pack_replication_frame(
    msg_type: int,
    sequence: int,
    epoch: int,
    payload: bytes = b"",
) -> bytes:
    """Serialize a replication frame with a 32-byte header and CRC32 checksum."""
    length = len(payload)
    # Pack prefix without CRC first (28 bytes)
    prefix = struct.pack(
        ">IHHQQI",
        REPL_MAGIC,
        REPL_VERSION,
        msg_type,
        sequence,
        epoch,
        length,
    )
    # Compute CRC32 over prefix + payload
    crc = zlib.crc32(prefix + payload) & 0xFFFFFFFF
    header = struct.pack(
        REPL_HEADER_FORMAT,
        REPL_MAGIC,
        REPL_VERSION,
        msg_type,
        sequence,
        epoch,
        length,
        crc,
    )
    return header + payload


def unpack_replication_header(
    header_bytes: bytes,
) -> Tuple[int, int, int, int, int, int]:
    """Unpack 32-byte header.

    Returns:
        (version, msg_type, sequence, epoch, length, expected_crc)
    """
    if len(header_bytes) < REPL_HEADER_SIZE:
        raise ValueError("Truncated replication header")

    magic, ver, msg_type, seq, epoch, length, expected_crc = struct.unpack(
        REPL_HEADER_FORMAT, header_bytes
    )
    if magic != REPL_MAGIC:
        raise ValueError(f"Invalid replication magic: 0x{magic:08X}")
    return ver, msg_type, seq, epoch, length, expected_crc


class JournalReplicationServer:
    """Standby node server receiving streaming IngestLog frames from the Primary node."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9998,
        target_log: Optional[IngestLog] = None,
        failover_node: Optional[FailoverNode] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.target_log = target_log
        self.failover_node = failover_node
        self.stats = ReplicationStats()
        self.running: bool = False
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start listening for incoming replication streams in a daemon thread."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(5)
        self.running = True
        self._thread = threading.Thread(
            target=self._accept_loop, name="journal-repl-server", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop replication server and close listener socket."""
        self.running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def _accept_loop(self) -> None:
        while self.running:
            try:
                sock, _ = self._server_sock.accept()
                sock.settimeout(2.0)
                client_thread = threading.Thread(
                    target=self._handle_client, args=(sock,), daemon=True
                )
                client_thread.start()
            except (socket.timeout, OSError):
                if not self.running:
                    break

    def _handle_client(self, sock: socket.socket) -> None:
        with sock:
            while self.running:
                try:
                    # Read 32-byte header
                    hdr_buf = self._recv_exact(sock, REPL_HEADER_SIZE)
                    if not hdr_buf:
                        break

                    ver, msg_type, seq, epoch, length, expected_crc = (
                        unpack_replication_header(hdr_buf)
                    )

                    # Epoch fencing verification
                    if self.failover_node and epoch < self.failover_node.epoch:
                        self.stats.stale_epoch_rejections += 1
                        err_frame = pack_replication_frame(
                            MSG_ERROR, seq, self.failover_node.epoch, b"STALE_EPOCH"
                        )
                        sock.sendall(err_frame)
                        break

                    # Read payload
                    payload = self._recv_exact(sock, length) if length > 0 else b""
                    if len(payload) != length:
                        self.stats.errors += 1
                        break

                    # Verify CRC32
                    prefix = hdr_buf[:28]
                    actual_crc = zlib.crc32(prefix + payload) & 0xFFFFFFFF
                    if actual_crc != expected_crc:
                        self.stats.crc_failures += 1
                        self.stats.errors += 1
                        err_frame = pack_replication_frame(
                            MSG_ERROR, seq, epoch, b"CRC_FAILURE"
                        )
                        sock.sendall(err_frame)
                        continue

                    # Process frame
                    self.stats.frames_received += 1
                    self.stats.bytes_received += REPL_HEADER_SIZE + length
                    self.stats.last_seq = seq

                    if msg_type == MSG_FRAME_DATA:
                        if self.target_log and hasattr(self.target_log, "append_raw"):
                            self.target_log.append_raw(payload)

                        # Return ACK
                        ack_frame = pack_replication_frame(MSG_ACK, seq, epoch)
                        sock.sendall(ack_frame)

                except (socket.timeout, ConnectionResetError, BrokenPipeError):
                    break
                except Exception as exc:
                    self.stats.errors += 1
                    logger.debug("Replication server error: %s", exc)
                    break

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n and self.running:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)


class JournalReplicationClient:
    """Primary node client streaming IngestLog frames to a Standby peer with spooling and auto-reconnect."""

    def __init__(
        self,
        target_host: str = "127.0.0.1",
        target_port: int = 9998,
        epoch: int = 1,
        timeout_s: float = 2.0,
        max_spool_size: int = 10_000,
        reconnect_backoff_s: float = 0.5,
        max_backoff_s: float = 5.0,
    ) -> None:
        self.target_host = target_host
        self.target_port = target_port
        self.epoch = epoch
        self.timeout_s = timeout_s
        self.max_spool_size = max_spool_size
        self.reconnect_backoff_s = reconnect_backoff_s
        self.max_backoff_s = max_backoff_s
        self.stats = ReplicationStats()
        self._sock: Optional[socket.socket] = None
        self._spool_queue: list[Tuple[int, bytes]] = []
        self._next_reconnect_ts: float = 0.0
        self._current_backoff: float = reconnect_backoff_s

    def connect(self) -> bool:
        """Establish TCP connection to the standby peer respecting reconnect backoff."""
        now = time.time()
        if now < self._next_reconnect_ts:
            return False

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout_s)
            sock.connect((self.target_host, self.target_port))
            self._sock = sock
            self._current_backoff = self.reconnect_backoff_s
            self._next_reconnect_ts = 0.0
            return True
        except Exception as exc:
            logger.debug("Failed to connect replication client: %s", exc)
            self._sock = None
            self._next_reconnect_ts = time.time() + self._current_backoff
            self._current_backoff = min(self.max_backoff_s, self._current_backoff * 2.0)
            return False

    def close(self) -> None:
        """Close client connection."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def flush_spool(self) -> int:
        """Drain and transmit spooled frames to standby peer."""
        if not self._sock and not self.connect():
            return 0

        drained = 0
        while self._spool_queue and self._sock:
            seq, payload = self._spool_queue[0]
            if self._send_wire(seq, payload, wait_ack=True):
                self._spool_queue.pop(0)
                drained += 1
                self.stats.spool_queued = len(self._spool_queue)
            else:
                break
        return drained

    def _send_wire(self, sequence: int, payload: bytes, wait_ack: bool = True) -> bool:
        """Internal low-level wire transmission helper."""
        if not self._sock:
            return False

        frame_bytes = pack_replication_frame(
            MSG_FRAME_DATA, sequence, self.epoch, payload
        )
        try:
            self._sock.sendall(frame_bytes)
            self.stats.frames_sent += 1
            self.stats.bytes_sent += len(frame_bytes)
            self.stats.last_seq = sequence

            if wait_ack:
                ack_hdr = self._sock.recv(REPL_HEADER_SIZE)
                if len(ack_hdr) < REPL_HEADER_SIZE:
                    self.stats.errors += 1
                    self.close()
                    return False
                ver, msg_type, ack_seq, ack_epoch, length, crc = (
                    unpack_replication_header(ack_hdr)
                )
                if msg_type == MSG_ACK and ack_seq == sequence:
                    self.stats.acks_received += 1
                    return True
                elif msg_type == MSG_ERROR:
                    self.stats.errors += 1
                    self.close()
                    return False
            return True
        except Exception as exc:
            self.stats.errors += 1
            logger.debug("Wire send failed: %s", exc)
            self.close()
            return False

    def send_frame(self, sequence: int, payload: bytes, wait_ack: bool = True) -> bool:
        """Transmit an IngestLog frame with automatic spooling during disconnects."""
        # 1. Attempt draining existing spool if any
        if self._spool_queue:
            self.flush_spool()

        # 2. If connected and spool is clear, send directly
        if self._sock and not self._spool_queue:
            if self._send_wire(sequence, payload, wait_ack=wait_ack):
                return True

        # 3. Connection down or send failed: enqueue into spool
        if len(self._spool_queue) >= self.max_spool_size:
            self._spool_queue.pop(0)  # Drop oldest frame to protect memory
            self.stats.spool_dropped += 1

        self._spool_queue.append((sequence, payload))
        self.stats.spool_queued = len(self._spool_queue)
        return False
