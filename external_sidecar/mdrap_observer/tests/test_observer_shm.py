"""Tests for Observer Shared Memory (SHM) transport, multi-consumer, and ring wraparound."""

import os
import sys
import time
import pytest

from mdrap.shm import SHMWriter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.client import ObserverClient
from observer.state import ObserverState


def test_observer_shm_end_to_end():
    """Verify ObserverClient in SHM mode reads live tick data from lock-free ring buffer."""
    ring_name = f"test_obs_shm_{int(time.time() * 1000) % 1000000}"
    writer = SHMWriter(name=ring_name, slot_count=256)

    received_events = []

    def on_event(evt):
        received_events.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_event,
    )
    client.start()

    try:
        # Give reader worker time to attach
        time.sleep(0.1)

        # Write 20 ticks into SHM
        for i in range(1, 21):
            writer.write_tick(
                seq=i,
                symbol="BTCUSDT",
                source="BINANCE",
                price=65000.0 + i,
                size=1.0,
                bid=64999.0 + i,
                ask=65001.0 + i,
            )

        # Allow consumer loop to read batch
        time.sleep(0.3)

        assert len(received_events) >= 20
        assert received_events[0]["sym"] == "BTCUSDT"
        assert received_events[0]["seq"] == 1
        assert received_events[-1]["seq"] == 20
        assert client.connected
    finally:
        client.stop()
        writer.close()


def test_observer_shm_ring_wraparound():
    """Verify slow consumer surviving ring buffer wraparound when writer exceeds slot capacity."""
    ring_name = f"test_obs_wrap_{int(time.time() * 1000) % 1000000}"
    # Small slot count of 64 slots
    writer = SHMWriter(name=ring_name, slot_count=64)

    # Pre-fill and wrap around by writing 150 ticks before reader connects
    for i in range(1, 151):
        writer.write_tick(
            seq=i,
            symbol="BTC-USD",
            source="COINBASE",
            price=60000.0 + i,
            size=0.5,
            bid=59999.0 + i,
            ask=60001.0 + i,
        )

    received_events = []

    def on_event(evt):
        received_events.append(evt)

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=on_event,
    )
    client.start()

    try:
        time.sleep(0.3)
        # Write another 10 ticks while client is active
        for i in range(151, 161):
            writer.write_tick(
                seq=i,
                symbol="BTC-USD",
                source="COINBASE",
                price=60000.0 + i,
                size=0.5,
                bid=59999.0 + i,
                ask=60001.0 + i,
            )
        time.sleep(0.3)

        # Client should have received recent slots without crashing
        assert len(received_events) > 0
        assert client.connected
    finally:
        client.stop()
        writer.close()


def test_observer_multiple_concurrent_consumers():
    """Verify multiple independent Observer consumers read concurrently without interference."""
    ring_name = f"test_obs_multi_{int(time.time() * 1000) % 1000000}"
    writer = SHMWriter(name=ring_name, slot_count=256)

    c1_events = []
    c2_events = []

    c1 = ObserverClient(mode="shm", ring_name=ring_name, on_event_callback=lambda e: c1_events.append(e))
    c2 = ObserverClient(mode="shm", ring_name=ring_name, on_event_callback=lambda e: c2_events.append(e))

    c1.start()
    c2.start()

    try:
        time.sleep(0.1)
        for i in range(1, 31):
            writer.write_tick(
                seq=i,
                symbol="ETH-USD",
                source="KRAKEN",
                price=3400.0 + i,
                size=2.0,
                bid=3399.0 + i,
                ask=3401.0 + i,
            )

        time.sleep(0.4)

        # Both independent consumers must receive the full stream
        assert len(c1_events) >= 30
        assert len(c2_events) >= 30
        assert c1_events[0]["seq"] == 1
        assert c2_events[0]["seq"] == 1
    finally:
        c1.stop()
        c2.stop()
        writer.close()
