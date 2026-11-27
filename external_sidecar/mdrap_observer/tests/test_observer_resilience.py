"""Comprehensive tests for Observer client connection resilience, backoff, and malformed data handling."""

import asyncio
import json
import os
import struct
import sys
import threading
import time
import pytest

from mdrap.shm import SHMWriter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.client import ObserverClient
from observer.state import ObserverState


class DummyStreamingServer:
    """Mock TCP streaming server implementing MDRAP daemon SUB protocol."""

    def __init__(self, host="127.0.0.1", port=19876, inject_malformed=False):
        self.host = host
        self.port = port
        self.inject_malformed = inject_malformed
        self.server = None
        self._running = False

    async def start(self):
        self._running = True
        self.server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

    async def _handle_client(self, reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            cmd = line.decode().strip()
            # Respond to SUB command
            writer.write(b'{"status": "SUBSCRIBED", "symbol": "ALL"}\n')
            await writer.drain()

            seq = 1
            while self._running:
                if self.inject_malformed and seq % 3 == 0:
                    # Write invalid non-JSON / truncated garbage
                    writer.write(b"CORRUPTED_RAW_BYTES_NOT_JSON\n")
                else:
                    frame = {
                        "type": "TICK",
                        "venue": "BINANCE",
                        "symbol": "BTCUSDT",
                        "price": 65000.0,
                        "sequence": seq,
                        "timestamp": time.time(),
                    }
                    writer.write((json.dumps(frame) + "\n").encode("utf-8"))
                await writer.drain()
                seq += 1
                await asyncio.sleep(0.05)
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def stop(self):
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()


def test_client_reconnection_and_restart():
    """Verify consumer reconnects after server disconnection and resumes consumption."""

    async def _run():
        port = 19877
        events = []

        def on_event(evt):
            events.append(evt)

        client = ObserverClient(
            mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event
        )
        client.start()

        # Step 1: Server not running yet; client waits without crashing
        await asyncio.sleep(0.4)
        assert len(events) == 0

        # Step 2: Start server; client should connect and receive events
        server = DummyStreamingServer(port=port)
        await server.start()

        await asyncio.sleep(0.6)
        assert len(events) > 0
        first_count = len(events)

        # Step 3: Stop server (simulating daemon restart / network drop)
        await server.stop()
        await asyncio.sleep(0.4)

        # Step 4: Restart server; client should reconnect and resume receiving events
        server2 = DummyStreamingServer(port=port)
        await server2.start()

        await asyncio.sleep(0.6)
        assert len(events) > first_count
        assert client.reconnect_count >= 1

        # Cleanup
        await server2.stop()
        client.stop()

    asyncio.run(_run())


def test_client_malformed_event_resilience():
    """Verify malformed non-JSON frames do not terminate consumer or corrupt message count."""

    async def _run():
        port = 19878
        events = []

        def on_event(evt):
            events.append(evt)

        server = DummyStreamingServer(port=port, inject_malformed=True)
        await server.start()

        client = ObserverClient(
            mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event
        )
        client.start()

        await asyncio.sleep(0.8)

        # Client must continue running despite malformed frames
        assert client.running
        assert len(events) > 0
        assert client.events_count > 0

        # Cleanup
        await server.stop()
        client.stop()

    asyncio.run(_run())


def test_shm_client_stale_heartbeat_recovery_and_interruptible_stop():
    """Verify SHM ObserverClient detects frozen heartbeat, recovers on restart, and stops rapidly."""
    ring_name = f"test_res_shm_{os.getpid()}_{time.time_ns()}"
    writer1 = SHMWriter(name=ring_name, slot_count=64)
    events = []

    client = ObserverClient(
        mode="shm",
        ring_name=ring_name,
        on_event_callback=events.append,
        shm_writer_dead_timeout=0.25,
    )
    client.start()

    writer2 = None
    try:
        deadline = time.monotonic() + 2.0
        while not client.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert client.connected

        writer1.write_tick(
            seq=1, symbol="BTCUSDT", source="BINANCE", price=65000.0, size=1.0
        )
        deadline = time.monotonic() + 2.0
        while len(events) < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(events) == 1

        # Simulate frozen/crashed publisher heartbeat without unlink
        struct.pack_into("<d", writer1.shm.buf, 64, time.time() - 5.0)

        deadline = time.monotonic() + 2.0
        while client.connected and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not client.connected
        assert client.reconnect_count >= 1

        # Restart publisher -> client reconnects and consumes new epoch ticks
        writer1.close()
        writer2 = SHMWriter(name=ring_name, slot_count=64)
        writer2.write_tick(
            seq=1, symbol="BTCUSDT", source="DATABENTO", price=66000.0, size=1.0
        )

        deadline = time.monotonic() + 3.0
        while len(events) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(events) == 2
        assert events[1]["source"] == "DATABENTO"
        assert events[1]["price"] == 66000.0

        # Close writer2 and ensure stop() interrupts exponential backoff promptly
        writer2.close()
        writer2 = None
        deadline = time.monotonic() + 2.0
        while client.connected and time.monotonic() < deadline:
            time.sleep(0.02)

        t0 = time.perf_counter()
        client.stop()
        assert (time.perf_counter() - t0) < 0.5
    finally:
        client.stop()
        writer1.close()
        if writer2 is not None:
            writer2.close()
