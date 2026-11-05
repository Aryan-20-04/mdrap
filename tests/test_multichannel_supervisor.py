"""
MDRAP v5.1 — Multi-Channel Supervisor & Multicore Concurrency Test Suite.

Verifies ADR-006:
- Multi-worker concurrent execution.
- Dedicated SPSC queues per channel.
- CPU affinity setting across platforms.
- Sequence gap and duplicate tracking per channel.
- Health watchdog and heartbeat detection.
- Clean start/stop lifecycle.
"""

import time
import pytest

from mdrap.models import RawEvent
from mdrap.supervisor import (
    ChannelWorker,
    ChannelWorkerConfig,
    MultiChannelSupervisor,
    set_core_affinity,
)


def _make_event(venue: str, channel: str, seq: int, price: float = 100.0) -> RawEvent:
    return RawEvent(
        source=venue,
        payload={
            "venue": venue,
            "channel_id": channel,
            "instrument": "AAPL",
            "event_type": "TRADE",
            "price": price,
            "size": 10.0,
            "sequence": seq,
            "exchange_ts": 1700000000.0 + seq * 0.001,
        },
        receive_timestamp=1700000000.0 + seq * 0.001,
        raw_id=f"{venue}_{channel}_{seq}",
    )


class TestMultiChannelSupervisor:
    """Verifies multicore channel ingress and supervision."""

    def test_core_affinity_execution(self):
        """Verify set_core_affinity executes safely without throwing exceptions."""
        # On Windows or Linux, core 0 should succeed or return boolean
        res = set_core_affinity(0)
        assert isinstance(res, bool)
        # Invalid core should return False
        assert not set_core_affinity(-1)

    def test_multi_channel_concurrent_processing(self):
        """Verify multiple channels process events concurrently and preserve ordering."""
        supervisor = MultiChannelSupervisor()

        # Register 4 channels simulating distinct exchange feeds
        for i in range(4):
            cfg = ChannelWorkerConfig(
                channel_id=f"FEED_CH_{i}",
                venue="TEST_VENUE",
                core_id=i,
                queue_capacity=1024,
            )
            supervisor.register_channel(cfg)

        supervisor.start_all()

        try:
            total_per_channel = 1_000
            # Submit events across all channels
            for seq in range(1, total_per_channel + 1):
                for ch_idx in range(4):
                    ch_id = f"FEED_CH_{ch_idx}"
                    ev = _make_event("TEST_VENUE", ch_id, seq, price=100.0 + ch_idx)
                    # Submit with bounded spin on backpressure
                    while not supervisor.submit(ch_id, ev):
                        pass

            # Wait for workers to drain
            deadline = time.time() + 5.0
            while time.time() < deadline:
                metrics = supervisor.aggregate_metrics()
                if metrics["total_processed"] >= total_per_channel * 4:
                    break
                time.sleep(0.01)

            metrics = supervisor.aggregate_metrics()
            assert metrics["total_channels"] == 4
            assert metrics["total_received"] == total_per_channel * 4
            assert metrics["total_processed"] == total_per_channel * 4
            assert metrics["total_dropped"] == 0
            assert metrics["total_gaps"] == 0

        finally:
            supervisor.stop_all()

    def test_channel_isolation_of_sequence_gaps(self):
        """Verify sequence gaps on Channel A do not corrupt sequence on Channel B."""
        supervisor = MultiChannelSupervisor()
        cfg_a = ChannelWorkerConfig(channel_id="CH_A", queue_capacity=1024)
        cfg_b = ChannelWorkerConfig(channel_id="CH_B", queue_capacity=1024)

        supervisor.register_channel(cfg_a)
        supervisor.register_channel(cfg_b)
        supervisor.start_all()

        try:
            # Channel A: normal 1, 2, 3
            supervisor.submit("CH_A", _make_event("VENUE_A", "CH_A", 1))
            supervisor.submit("CH_A", _make_event("VENUE_A", "CH_A", 2))
            supervisor.submit("CH_A", _make_event("VENUE_A", "CH_A", 3))

            # Channel B: sequence gap! 1 then 5
            supervisor.submit("CH_B", _make_event("VENUE_B", "CH_B", 1))
            supervisor.submit("CH_B", _make_event("VENUE_B", "CH_B", 5))

            time.sleep(0.2)
            metrics = supervisor.aggregate_metrics()

            assert metrics["channels"]["CH_A"]["sequence_gaps"] == 0
            assert metrics["channels"]["CH_B"]["sequence_gaps"] == 1

        finally:
            supervisor.stop_all()

    def test_backpressure_drop_counter(self):
        """Verify queue saturation drops events without losing state and records drop counter."""
        supervisor = MultiChannelSupervisor()
        # Tiny queue capacity to force immediate backpressure
        cfg = ChannelWorkerConfig(channel_id="SATURATED_CH", queue_capacity=4)
        worker = supervisor.register_channel(cfg)

        # Do not start worker so queue stays full
        # First 4 offers succeed
        for i in range(1, 5):
            assert supervisor.submit("SATURATED_CH", _make_event("V", "SATURATED_CH", i))

        # 5th offer rejected
        assert not supervisor.submit("SATURATED_CH", _make_event("V", "SATURATED_CH", 5))

        metrics = supervisor.aggregate_metrics()
        assert metrics["channels"]["SATURATED_CH"]["events_dropped"] == 1
        assert metrics["channels"]["SATURATED_CH"]["events_received"] == 5

    def test_health_watchdog_heartbeat(self):
        """Verify supervisor detects worker heartbeat health."""
        supervisor = MultiChannelSupervisor(heartbeat_timeout_s=0.5)
        cfg = ChannelWorkerConfig(channel_id="HEALTHY_CH", queue_capacity=128)
        supervisor.register_channel(cfg)
        supervisor.start_all()

        try:
            time.sleep(0.1)
            health = supervisor.check_health()
            assert health["HEALTHY_CH"] is True
        finally:
            supervisor.stop_all()
