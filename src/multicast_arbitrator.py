"""Multicast UDP A/B Feed Arbitrator & Gap Recovery Engine (Spec §18, §26).

Implements institutional market data network feed arbitration (CME MDP 3.0 / NASDAQ MoldUDP64):
  - Dual physical feed listeners (Feed A & Feed B) running concurrently over UDP multicast.
  - Ultra-low latency O(1) sequence watermark deduplication.
  - Gap detection on missing packet sequence numbers.
  - Automatic TCP Historical Replay backfill requests for seamless sequence healing.
  - Re-sequencing gap buffer with strictly monotonic in-order dispatch downstream.

Network Transport Architecture:
  1. A/B Redundancy:
     Financial exchanges broadcast market events across two geometrically and physically
     disjoint networks ("Line A" and "Line B"). The arbitrator consumes packets from both lines,
     immediately processing whichever packet arrives first and discarding the secondary copy.
  2. Sequence Watermark Invariant:
     Each channel maintains `expected_seq`. If an arriving packet has `seq < expected`, it is
     a duplicate and dropped in O(1) time. If `seq == expected`, it is dispatched immediately.
  3. Gap Resolution:
     If `seq > expected`, packet loss has occurred on both lines. The packet is buffered in
     the out-of-order gap queue, and a TCP replay request is triggered for the range
     `[expected, seq - 1]`. Contiguous packets are drained and dispatched in strictly monotonic order.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
import logging
import random
import time
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UDPPacket:
    """A market data packet received from UDP Multicast Feed A, Feed B, or TCP Replay."""

    channel_id: str
    sequence_num: int
    feed_id: str  # 'A', 'B', or 'TCP_REPLAY'
    payload: bytes  # Raw wire bytes (SBE or JSON)
    send_timestamp: float = 0.0
    receive_timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "channel": self.channel_id,
            "seq": self.sequence_num,
            "feed": self.feed_id,
            "size_bytes": len(self.payload),
            "send_ts": self.send_timestamp,
            "recv_ts": self.receive_timestamp,
        }


@dataclass
class ArbitratorMetrics:
    """Real-time observability metrics for feed arbitration health."""

    feed_a_packets: int = 0
    feed_b_packets: int = 0
    tcp_packets: int = 0
    dedup_dropped: int = 0
    in_order_dispatched: int = 0
    gaps_detected: int = 0
    tcp_replays_requested: int = 0
    tcp_packets_recovered: int = 0
    gap_buffer_overflows: int = 0

    def to_dict(self) -> dict:
        total_rx = self.feed_a_packets + self.feed_b_packets + self.tcp_packets
        dedup_rate = (self.dedup_dropped / max(1, total_rx)) * 100.0
        return {
            "feed_a_packets": self.feed_a_packets,
            "feed_b_packets": self.feed_b_packets,
            "tcp_packets": self.tcp_packets,
            "total_received": total_rx,
            "dedup_dropped": self.dedup_dropped,
            "dedup_rate_pct": round(dedup_rate, 2),
            "in_order_dispatched": self.in_order_dispatched,
            "gaps_detected": self.gaps_detected,
            "tcp_replays_requested": self.tcp_replays_requested,
            "tcp_packets_recovered": self.tcp_packets_recovered,
            "gap_buffer_overflows": self.gap_buffer_overflows,
        }


class ABFeedArbitrator:
    """High-Performance A/B Feed Arbitrator with Monotonic In-Order Dispatch.

    Channels: Arbitrates multiple instrument channels independently.
    Deduplication: Fast O(1) comparison against expected sequence watermark.
    Gap Recovery: Queries TCP Replay client whenever sequence jump exceeds expected.
    """

    def __init__(
        self,
        tcp_replay_client: Callable[[str, int, int], list[UDPPacket]] | None = None,
        max_gap_buffer_size: int = 10000,
        initial_seq: int = 1,
    ):
        self.tcp_replay_client = tcp_replay_client
        self.max_gap_buffer_size = max_gap_buffer_size
        self.initial_seq = initial_seq

        # Channel State: channel_id -> expected monotonic sequence number
        self._expected_seq: dict[str, int] = {}

        # Out-of-order gap buffers: channel_id -> {sequence_num: UDPPacket}
        self._gap_buffers: dict[str, dict[int, UDPPacket]] = collections.defaultdict(
            dict
        )

        self.metrics = ArbitratorMetrics()

    def get_expected_seq(self, channel_id: str) -> int:
        return self._expected_seq.get(channel_id, self.initial_seq)

    def set_expected_seq(self, channel_id: str, seq: int) -> None:
        self._expected_seq[channel_id] = seq

    def on_packet(self, packet: UDPPacket) -> list[UDPPacket]:
        """
        Process an incoming packet from Feed A, Feed B, or TCP Replay.

        Returns a list of deduplicated packets in strictly monotonic sequence order
        ready for downstream processing. Returns empty list if packet was duplicate
        or buffered pending gap resolution.
        """
        ch = packet.channel_id
        seq = packet.sequence_num

        # Update ingress metrics
        if packet.feed_id == "A":
            self.metrics.feed_a_packets += 1
        elif packet.feed_id == "B":
            self.metrics.feed_b_packets += 1
        else:
            self.metrics.tcp_packets += 1

        if ch not in self._expected_seq:
            self._expected_seq[ch] = self.initial_seq

        expected = self._expected_seq[ch]

        # -------------------------------------------------------------
        # 1. DUPLICATE CHECK: O(1) Fast Rejection
        # -------------------------------------------------------------
        if seq < expected:
            # Already processed and dispatched downstream
            self.metrics.dedup_dropped += 1
            return []

        # -------------------------------------------------------------
        # 2. SEQUENCE GAP DETECTED (seq > expected)
        # -------------------------------------------------------------
        if seq > expected:
            gap_buf = self._gap_buffers[ch]

            # Store in gap buffer if not already present
            if seq not in gap_buf:
                if len(gap_buf) >= self.max_gap_buffer_size:
                    self.metrics.gap_buffer_overflows += 1
                    logger.warning(
                        "Gap buffer overflow on channel %s (%d packets). Dropping seq %d",
                        ch,
                        len(gap_buf),
                        seq,
                    )
                    return []
                gap_buf[seq] = packet
            else:
                self.metrics.dedup_dropped += 1

            self.metrics.gaps_detected += 1

            # Trigger TCP Replay if handler configured
            if self.tcp_replay_client:
                missing_start = expected
                missing_end = seq - 1
                self.metrics.tcp_replays_requested += 1
                try:
                    replayed = self.tcp_replay_client(ch, missing_start, missing_end)
                    for r_pkt in replayed:
                        if (
                            r_pkt.sequence_num not in gap_buf
                            and r_pkt.sequence_num >= expected
                        ):
                            gap_buf[r_pkt.sequence_num] = r_pkt
                            self.metrics.tcp_packets_recovered += 1
                except Exception as ex:
                    logger.error(
                        "TCP replay failed for %s [%d-%d]: %s",
                        ch,
                        missing_start,
                        missing_end,
                        ex,
                    )

            # Check if gap was resolved by TCP replay
            if expected in gap_buf:
                dispatched = self._drain_gap_buffer(ch)
                self.metrics.in_order_dispatched += len(dispatched)
                return dispatched

            return []

        # -------------------------------------------------------------
        # 3. IN-ORDER PACKET ARRIVAL (seq == expected)
        # -------------------------------------------------------------
        dispatched = [packet]
        self._expected_seq[ch] = expected + 1

        # Check if following contiguous packets are waiting in the gap buffer
        gap_buf = self._gap_buffers[ch]
        if gap_buf and self._expected_seq[ch] in gap_buf:
            dispatched.extend(self._drain_gap_buffer(ch))

        self.metrics.in_order_dispatched += len(dispatched)
        return dispatched

    def _drain_gap_buffer(self, channel_id: str) -> list[UDPPacket]:
        """Drain contiguous packets from the gap buffer starting at expected_seq."""
        gap_buf = self._gap_buffers[channel_id]
        dispatched: list[UDPPacket] = []

        curr = self._expected_seq[channel_id]
        while curr in gap_buf:
            pkt = gap_buf.pop(curr)
            dispatched.append(pkt)
            curr += 1

        self._expected_seq[channel_id] = curr
        return dispatched

    def reset_channel(self, channel_id: str, new_expected_seq: int = 1) -> None:
        """Reset sequence tracker and clear gap buffer for a channel."""
        self._expected_seq[channel_id] = new_expected_seq
        self._gap_buffers[channel_id].clear()

    def stats(self) -> dict:
        """Return arbitrator metrics and channel statuses."""
        return {
            "metrics": self.metrics.to_dict(),
            "channels": {
                ch: {
                    "expected_seq": self._expected_seq[ch],
                    "gap_buffer_size": len(self._gap_buffers[ch]),
                }
                for ch in self._expected_seq
            },
        }


class MulticastFeedSimulator:
    """Simulates dual-path multicast network transport with configurable loss and jitter.

    Maintains a TCP Historical Replay archive for gap recovery testing.
    """

    def __init__(
        self,
        channel_id: str = "CH1",
        drop_rate_a: float = 0.05,
        drop_rate_b: float = 0.05,
        seed: int | None = 42,
    ):
        self.channel_id = channel_id
        self.drop_rate_a = drop_rate_a
        self.drop_rate_b = drop_rate_b
        self.rng = random.Random(seed)

        self._seq = 0
        self._history: dict[int, UDPPacket] = {}

    def publish_event(
        self, payload: bytes
    ) -> tuple[UDPPacket | None, UDPPacket | None]:
        """Simulate transmitting an event over both Feed A and Feed B multicast paths.

        Returns:
            (packet_a, packet_b): Either packet may be None if dropped on that physical line.
        """
        self._seq += 1
        seq = self._seq
        now = time.time()

        pkt_canonical = UDPPacket(
            channel_id=self.channel_id,
            sequence_num=seq,
            feed_id="CANONICAL",
            payload=payload,
            send_timestamp=now,
            receive_timestamp=now,
        )
        self._history[seq] = pkt_canonical

        pkt_a: UDPPacket | None = None
        if self.rng.random() >= self.drop_rate_a:
            pkt_a = UDPPacket(
                channel_id=self.channel_id,
                sequence_num=seq,
                feed_id="A",
                payload=payload,
                send_timestamp=now,
                receive_timestamp=now,
            )

        pkt_b: UDPPacket | None = None
        if self.rng.random() >= self.drop_rate_b:
            pkt_b = UDPPacket(
                channel_id=self.channel_id,
                sequence_num=seq,
                feed_id="B",
                payload=payload,
                send_timestamp=now,
                receive_timestamp=now,
            )

        return pkt_a, pkt_b

    def tcp_replay_request(
        self, channel_id: str, start_seq: int, end_seq: int
    ) -> list[UDPPacket]:
        """Simulate TCP Replay server responding with missing range."""
        if channel_id != self.channel_id:
            return []

        replayed: list[UDPPacket] = []
        for s in range(start_seq, end_seq + 1):
            if s in self._history:
                orig = self._history[s]
                replayed.append(
                    UDPPacket(
                        channel_id=self.channel_id,
                        sequence_num=s,
                        feed_id="TCP_REPLAY",
                        payload=orig.payload,
                        send_timestamp=orig.send_timestamp,
                        receive_timestamp=time.time(),
                    )
                )
        return replayed
