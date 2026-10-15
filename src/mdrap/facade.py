"""High-Level Developer Facade & Ergonomics Layer for MDRAP.

Provides intuitive, zero-configuration Pythonic APIs for quantitative developers
and researchers, enabling 3-line streaming, live validation, and diagnostic checks:

    import mdrap

    for tick in mdrap.stream("sim:AAPL", count=100):
        print(f"[{tick.source}] {tick.instrument_id} ${tick.price:,.2f} | {tick.quality_status}")
"""

from __future__ import annotations

from collections.abc import Generator, Iterator
from contextlib import contextmanager
import math
import os
import shutil
import sys
import tempfile
import time

__stability__ = "stable"
from typing import Any

from .fastpath import is_available as is_native_fastpath_available
from .gateway import Gateway
from .models import CanonicalEvent, EventType, QualityStatus, RawEvent
from .profile import HardwareProfile, ProfileMode, current_profile
from .quality import QualityEngine


class StreamSession:
    """Manages an active market data streaming session with automatic cleanup."""

    def __init__(
        self,
        target: str = "sim:AAPL",
        profile: HardwareProfile | str | None = None,
        count: int | None = None,
    ) -> None:
        self.target = target
        self.profile = (
            HardwareProfile.detect(profile)
            if isinstance(profile, (str, ProfileMode)) or profile is None
            else profile
        )
        self.count = count
        self._stopped = False
        self._gateway = Gateway()
        self._quality = QualityEngine()

    def stop(self) -> None:
        """Halt streaming session gracefully."""
        self._stopped = True

    def __iter__(self) -> Iterator[CanonicalEvent]:
        return self._generate_stream()

    def _generate_stream(self) -> Iterator[CanonicalEvent]:
        target_clean = self.target.strip()
        parts = target_clean.split(":", 1)
        protocol = parts[0].lower()
        symbol = parts[1] if len(parts) > 1 else "AAPL"

        produced = 0
        t_base = time.time()
        price = 150.0 if "AAPL" in symbol else (65000.0 if "BTC" in symbol else 3000.0)

        # 1. Simulated High-Throughput Realistic Tick Generator
        if protocol in ("sim", "mock", "test") or not protocol:
            seq = 1
            while not self._stopped:
                if self.count is not None and produced >= self.count:
                    break

                # Micro-movement random walk
                t_now = t_base + (produced * 0.001)
                delta = math.sin(produced * 0.05) * 0.5 + (0.01 * (produced % 7 - 3))
                current_price = round(price + delta, 2)
                current_qty = round(10.0 + (produced % 15) * 2.5, 2)

                raw = RawEvent(
                    source=f"SIM_{protocol.upper()}",
                    payload={
                        "instrument": symbol,
                        "event_type": "TRADE",
                        "price": current_price,
                        "quantity": current_qty,
                        "sequence": seq,
                        "exchange_ts": t_now,
                    },
                    receive_timestamp=t_now,
                )
                seq += 1

                # Normalize and evaluate quality
                try:
                    raw = self._gateway.ingest(raw)
                    event = self._gateway.normalize(raw)
                    evaluated = self._quality.evaluate(event)
                    produced += 1
                    yield evaluated
                except Exception:
                    # In simulated stream, skip malformed ticks
                    continue

                # Adaptive yield / throttle if lite profile
                if self.profile.mode == ProfileMode.LITE and produced % 100 == 0:
                    time.sleep(0.001)

        # 2. Live WebSocket or External Feeds
        elif protocol in ("binance", "coinbase", "kraken"):
            from .ws_feed import WebSocketFeedManager

            manager = WebSocketFeedManager(venues=[protocol], symbols=[symbol])
            manager.start()
            try:
                while not self._stopped:
                    if self.count is not None and produced >= self.count:
                        break
                    raw = manager.get(timeout_s=1.0)
                    if raw is None:
                        continue
                    try:
                        raw = self._gateway.ingest(raw)
                        event = self._gateway.normalize(raw)
                        evaluated = self._quality.evaluate(event)
                        produced += 1
                        yield evaluated
                    except Exception:
                        continue
            finally:
                manager.stop()

        else:
            raise ValueError(
                f"Unsupported stream protocol: {protocol!r}. "
                f"Supported protocols: 'sim:<symbol>', 'binance:<symbol>', 'coinbase:<symbol>', 'kraken:<symbol>'."
            )


def stream(
    target: str = "sim:AAPL",
    profile: HardwareProfile | str | None = None,
    count: int | None = None,
) -> Generator[CanonicalEvent, None, None]:
    """Stream normalized, validated market data ticks.

    Example:
        >>> for tick in mdrap.stream("sim:BTC/USDT", count=5):
        ...     print(tick.instrument_id, tick.price)
    """
    session = StreamSession(target=target, profile=profile, count=count)
    yield from session


@contextmanager
def connect(
    target: str = "sim:AAPL",
    profile: HardwareProfile | str | None = None,
) -> Generator[StreamSession, None, None]:
    """Context manager for connecting to a market data stream with auto-teardown.

    Example:
        >>> with mdrap.connect("sim:ETH/USD") as session:
        ...     for tick in session:
        ...         if tick.price > 3100.0:
        ...             break
    """
    session = StreamSession(target=target, profile=profile)
    try:
        yield session
    finally:
        session.stop()


def doctor(verbose: bool = True) -> dict[str, Any]:
    """Execute host environment diagnostics and hardware benchmark report."""
    prof = current_profile()
    native_ok = is_native_fastpath_available()

    # Fast disk write IO benchmark
    disk_speed_mbps = 0.0
    try:
        tmp_dir = tempfile.mkdtemp(prefix="mdrap_diag_")
        test_file = os.path.join(tmp_dir, "io_test.bin")
        data = b"\x00" * (1024 * 1024)  # 1MB
        t0 = time.perf_counter()
        with open(test_file, "wb") as f:
            for _ in range(10):  # Write 10MB
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        t_elapsed = time.perf_counter() - t0
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if t_elapsed > 0:
            disk_speed_mbps = round(10.0 / t_elapsed, 1)
    except Exception:
        disk_speed_mbps = -1.0

    # Companion modules check
    companions = {
        "mdrap-options": False,
        "mdrap-analytics": False,
        "mdrap-strategies": False,
        "mdrap-contrib-vessel": False,
    }
    for comp in list(companions.keys()):
        try:
            mod_name = comp.replace("-", "_").replace("contrib_", "")
            if mod_name == "mdrap_vessel":
                mod_name = "mdrap_vessel"
            __import__(mod_name)
            companions[comp] = True
        except ImportError:
            companions[comp] = False

    result = {
        "platform": prof.summary()["platform"],
        "python_version": prof.summary()["python_version"],
        "cpu_cores": prof.cpu_cores,
        "total_ram_gb": prof.total_ram_gb,
        "profile_mode": prof.mode.value,
        "ring_buffer_mb": prof.summary()["ring_buffer_mb"],
        "wal_flush_interval_ms": prof.summary()["wal_flush_interval_ms"],
        "native_c_accelerator": "COMPILED (AVX2/Native)" if native_ok else "PURE-PYTHON FALLBACK",
        "disk_sync_speed_mbps": disk_speed_mbps,
        "companion_packages": companions,
    }

    if verbose:
        print("\n" + "=" * 65)
        print("          MDRAP PLATFORM & ENVIRONMENT DIAGNOSTICS")
        print("=" * 65)
        print(f"  Operating System  : {result['platform']}")
        print(f"  Python Runtime    : {result['python_version']}")
        print(f"  CPU Cores / RAM   : {result['cpu_cores']} cores / {result['total_ram_gb']} GB RAM")
        print(f"  Hardware Profile  : {result['profile_mode']} ({prof.description})")
        print(f"  Ring Buffer Size  : {result['ring_buffer_mb']} MB ({prof.ring_buffer_slots:,} slots)")
        print(f"  Native Fastpath   : {result['native_c_accelerator']}")
        print(f"  Disk Sync I/O     : {result['disk_sync_speed_mbps']} MB/s (fsync group commit)")
        print("-" * 65)
        print("  Commercial Companion Extensions:")
        for pkg, installed in companions.items():
            status_str = "[INSTALLED]" if installed else "[NOT INSTALLED (Core Free Mode)]"
            print(f"    - {pkg:<22}: {status_str}")
        print("=" * 65 + "\n")

    return result
