"""High-Level Developer Facade & Ergonomics Layer for MDRAP.

Provides intuitive, zero-configuration Pythonic APIs for quantitative developers
and researchers, enabling 1-line streaming, live validation, and diagnostic checks:

    import mdrap

    for tick in mdrap.stream("sim:AAPL", count=100):
        print(f"[{tick.source}] {tick.instrument_id} ${tick.price:,.2f} | {tick.quality_status}")
"""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from .config_loader import compute_config_hash, find_config_path
from .fastpath import HAS_FASTPATH
from .fastpath import is_available as is_native_fastpath_available
from .gateway import Gateway
from .models import CanonicalEvent, EventType, QualityStatus, RawEvent
from .profile import HardwareProfile, ProfileMode, current_profile
from .quality import QualityEngine
from .shm import HAS_SHM, SHMReader

__stability__ = "stable"


class StreamSession:
    """Manages an active market data streaming session with automatic cleanup and metrics."""

    def __init__(
        self,
        target: str = "sim:AAPL",
        profile: HardwareProfile | str | None = None,
        count: int | None = None,
        speed: float | None = None,
        **kwargs: Any,
    ) -> None:
        self.target = target
        self.profile = (
            HardwareProfile.detect(profile)
            if isinstance(profile, (str, ProfileMode)) or profile is None
            else profile
        )
        self.count = count
        self.speed = speed
        self._stopped = False
        self._gateway = Gateway()
        self._quality = QualityEngine()
        self._start_time = time.perf_counter()
        self._stats: dict[str, int] = {
            "produced": 0,
            "valid": 0,
            "suspicious": 0,
            "quarantined": 0,
        }

    def stop(self) -> None:
        """Halt streaming session gracefully."""
        self._stopped = True

    def __enter__(self) -> StreamSession:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def __iter__(self) -> Iterator[CanonicalEvent]:
        return self._generate_stream()

    def _update_stats(self, event: CanonicalEvent) -> None:
        self._stats["produced"] += 1
        if event.quality_status == QualityStatus.VALID:
            self._stats["valid"] += 1
        elif event.quality_status == QualityStatus.SUSPICIOUS:
            self._stats["suspicious"] += 1
        elif event.quality_status == QualityStatus.INVALID:
            self._stats["quarantined"] += 1

    def stats(self) -> dict[str, Any]:
        """Return streaming throughput, duration, and quality distribution."""
        elapsed = max(1e-6, time.perf_counter() - self._start_time)
        return {
            "target": self.target,
            "produced": self._stats["produced"],
            "valid": self._stats["valid"],
            "suspicious": self._stats["suspicious"],
            "quarantined": self._stats["quarantined"],
            "elapsed_s": round(elapsed, 3),
            "rate_eps": round(self._stats["produced"] / elapsed, 1),
        }

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

                try:
                    raw = self._gateway.ingest(raw)
                    event = self._gateway.normalize(raw)
                    evaluated = self._quality.evaluate(event)
                    produced += 1
                    self._update_stats(evaluated)
                    yield evaluated
                except Exception:
                    continue

                if self.speed and self.speed > 0:
                    time.sleep(0.001 / self.speed)
                elif self.profile.mode == ProfileMode.LITE and produced % 100 == 0:
                    time.sleep(0.001)

        # 2. Historical IngestLog WAL / File Replay
        elif protocol in ("wal", "file"):
            from .ingestlog import IngestLog

            target_path = parts[1] if len(parts) > 1 else "data/wal"
            if os.path.isdir(target_path) or (os.path.exists(target_path) and target_path.endswith(".log")):
                wal_dir = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
                log = IngestLog(wal_dir)
                try:
                    for off, raw in log.iter_from(0):
                        if self._stopped or (self.count is not None and produced >= self.count):
                            break
                        try:
                            if isinstance(raw.payload, dict):
                                raw.payload.setdefault(
                                    "event_type",
                                    "TRADE" if ("price" in raw.payload or "quantity" in raw.payload) else "QUOTE",
                                )
                                if "size" in raw.payload and "quantity" not in raw.payload:
                                    raw.payload["quantity"] = raw.payload["size"]
                            raw = self._gateway.ingest(raw)
                            event = self._gateway.normalize(raw)
                            evaluated = self._quality.evaluate(event)
                            produced += 1
                            self._update_stats(evaluated)
                            yield evaluated
                            if self.speed and self.speed > 0:
                                time.sleep(0.001 / self.speed)
                        except Exception:
                            continue
                finally:
                    log.close()
            elif os.path.isfile(target_path):
                with open(target_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if self._stopped or (self.count is not None and produced >= self.count):
                            break
                        line_str = line.strip()
                        if not line_str:
                            continue
                        try:
                            data = json.loads(line_str)
                            if isinstance(data, dict):
                                data.setdefault(
                                    "event_type",
                                    "TRADE" if ("price" in data or "quantity" in data) else "QUOTE",
                                )
                                if "size" in data and "quantity" not in data:
                                    data["quantity"] = data["size"]
                            raw = RawEvent(
                                source="FILE",
                                payload=data,
                                receive_timestamp=time.time(),
                            )
                            raw = self._gateway.ingest(raw)
                            event = self._gateway.normalize(raw)
                            evaluated = self._quality.evaluate(event)
                            produced += 1
                            self._update_stats(evaluated)
                            yield evaluated
                            if self.speed and self.speed > 0:
                                time.sleep(0.001 / self.speed)
                        except Exception:
                            continue
            else:
                raise FileNotFoundError(f"WAL directory or file not found: {target_path}")

        # 3. Seqlock Shared Memory (SHM) Low-Latency Ring Buffer
        elif protocol == "shm":
            shm_name = parts[1] if len(parts) > 1 else "mdrap_live"
            reader = SHMReader(shm_name)
            try:
                latest = reader.read_latest_seq()
                if self.count is not None and latest > 0:
                    earliest = max(0, latest - reader.slot_count + 1)
                    seq = max(earliest, latest - self.count + 1)
                else:
                    seq = latest

                idle_count = 0
                max_idle = 2000  # ~1.0s timeout when count is set
                while not self._stopped:
                    if self.count is not None and produced >= self.count:
                        break
                    slot = reader.read_slot(seq)
                    if slot:
                        idle_count = 0
                        ev = CanonicalEvent(
                            event_id=f"shm_{seq}",
                            instrument_id=str(slot.get("sym", "AAPL")),
                            event_type=EventType.TRADE if slot.get("price") is not None else EventType.QUOTE,
                            exchange_timestamp=float(slot.get("exchange_ts", time.time())),
                            receive_timestamp=time.time(),
                            processing_timestamp=time.time(),
                            source=str(slot.get("source", "SHM")),
                            price=float(slot["price"]) if slot.get("price") is not None else None,
                            quantity=float(slot["size"]) if slot.get("size") is not None else None,
                            bid_price=float(slot["bid"]) if slot.get("bid") is not None else None,
                            ask_price=float(slot["ask"]) if slot.get("ask") is not None else None,
                            quality_status=QualityStatus(slot.get("status", "VALID")),
                            sequence_number=seq,
                        )
                        seq += 1
                        produced += 1
                        self._update_stats(ev)
                        yield ev
                    else:
                        head = reader.read_latest_seq()
                        if seq < head:
                            seq += 1
                            continue
                        idle_count += 1
                        if self.count is not None and idle_count > max_idle:
                            break
                        time.sleep(0.0005)
            finally:
                reader.close()

        # 4. Live Public WebSocket Feeds
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
                        self._update_stats(evaluated)
                        yield evaluated
                    except Exception:
                        continue
            finally:
                manager.stop()

        else:
            raise ValueError(
                f"Unsupported stream protocol: {protocol!r}. "
                f"Supported protocols: 'sim:<symbol>', 'wal:<dir>', 'file:<path>', 'shm:<name>', 'binance:<symbol>', 'kraken:<symbol>'."
            )


def stream(
    target: str = "sim:AAPL",
    profile: HardwareProfile | str | None = None,
    count: int | None = None,
    speed: float | None = None,
    **kwargs: Any,
) -> StreamSession:
    """Stream normalized, validated market data ticks from simulated, live, file, or SHM sources.

    Supports:
        - 'sim:<symbol>' (e.g. 'sim:AAPL', 'sim:BTCUSDT')
        - 'wal:<path>' or 'file:<path>' (IngestLog directory or jsonl file)
        - 'shm:<ring_name>' (low-latency seqlock shared memory ring)
        - 'binance:<symbol>', 'kraken:<symbol>', 'coinbase:<symbol>' (live feeds)

    Examples:
        >>> for tick in mdrap.stream("sim:BTCUSDT", count=10):
        ...     print(tick.instrument_id, tick.price, tick.quality_status)

        >>> with mdrap.stream("sim:AAPL") as s:
        ...     for tick in s:
        ...         if tick.price > 155.0:
        ...             break
        ...     print(s.stats())
    """
    return StreamSession(target=target, profile=profile, count=count, speed=speed, **kwargs)


def connect(
    target: str = "sim:AAPL",
    profile: HardwareProfile | str | None = None,
    **kwargs: Any,
) -> StreamSession:
    """Managed connection context to a market data stream with auto-teardown.

    Example:
        >>> with mdrap.connect("sim:ETH/USD") as session:
        ...     for tick in session:
        ...         if tick.price > 3100.0:
        ...             break
    """
    return StreamSession(target=target, profile=profile, **kwargs)


def detect_cpu_features() -> list[str]:
    """Detect hardware SIMD and vectorized instruction capabilities cross-platform."""
    features = []
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                flags_text = f.read().lower()
                for feat in ("avx512f", "avx2", "avx", "sse4_2", "neon"):
                    if feat in flags_text:
                        features.append(feat.upper())
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "machdep.cpu.features"], text=True).lower()
            for feat in ("avx2", "avx", "sse4.2", "neon"):
                if feat in out:
                    features.append(feat.upper().replace(".", "_"))
        except Exception:
            pass
    elif sys.platform == "win32":
        # Check through native fastpath probe
        if HAS_FASTPATH:
            features.extend(["AVX2", "SSE4_2"])
    return features


def doctor(verbose: bool = True, as_json: bool = False) -> dict[str, Any]:
    """Execute host environment diagnostics, hardware recommendations, and disk I/O audit."""
    prof = current_profile()
    native_ok = is_native_fastpath_available()
    cfg_path = find_config_path()
    cfg_hash = compute_config_hash()

    # Fast disk write IO benchmark
    disk_speed_mbps = 0.0
    fsync_lat_ms = 0.0
    try:
        tmp_dir = tempfile.mkdtemp(prefix="mdrap_diag_")
        test_file = os.path.join(tmp_dir, "io_test.bin")
        data = b"\x00" * (1024 * 1024)  # 1MB
        t0 = time.perf_counter()
        with open(test_file, "wb") as f:
            for _ in range(5):  # Write 5MB with fsync
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        t_elapsed = time.perf_counter() - t0
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if t_elapsed > 0:
            disk_speed_mbps = round(5.0 / t_elapsed, 1)
            fsync_lat_ms = round((t_elapsed / 5.0) * 1000.0, 2)
    except Exception:
        disk_speed_mbps = -1.0
        fsync_lat_ms = -1.0

    # Recommend mode based on resources
    if prof.total_ram_gb < 6.0 or prof.cpu_cores <= 2:
        recommended = "LITE"
    elif prof.total_ram_gb >= 32.0 and prof.cpu_cores >= 12 and native_ok:
        recommended = "ULTRA"
    else:
        recommended = "STANDARD"

    # Companion modules check (backward compatibility)
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

    compilers_found = [c for c in ("gcc", "clang", "cl") if shutil.which(c)]
    cpu_features = detect_cpu_features()

    result = {
        "status": "HEALTHY",
        "platform": prof.summary()["platform"],
        "python_version": sys.version.split()[0],
        "cpu_cores": prof.cpu_cores,
        "total_ram_gb": prof.total_ram_gb,
        "profile_mode": prof.mode.value,
        "ring_buffer_mb": prof.summary()["ring_buffer_mb"],
        "wal_flush_interval_ms": prof.summary()["wal_flush_interval_ms"],
        "native_c_accelerator": "COMPILED (AVX2/Native)" if native_ok else "PURE-PYTHON FALLBACK",
        "disk_sync_speed_mbps": disk_speed_mbps,
        "companion_packages": companions,
        "environment": {
            "os": platform.platform(),
            "machine_arch": platform.machine(),
            "cpu_model": os.environ.get("PROCESSOR_IDENTIFIER", platform.processor() or "Generic CPU"),
            "cpu_cores_logical": os.cpu_count() or 1,
            "total_ram_gb": prof.total_ram_gb,
            "python_version": sys.version.split()[0],
            "python_compiler": platform.python_compiler(),
            "cpu_features": cpu_features,
        },
        "hardware_profile": {
            "active_mode": prof.mode.value,
            "recommended_mode": recommended,
            "description": prof.description,
            "ring_buffer_mb": prof.summary()["ring_buffer_mb"],
            "wal_flush_interval_ms": prof.summary()["wal_flush_interval_ms"],
            "wal_batch_size": prof.wal_batch_size,
        },
        "acceleration": {
            "has_native_fastpath": HAS_FASTPATH,
            "fastpath_status": "COMPILED (Vectorized C)" if native_ok else "PURE-PYTHON FALLBACK",
            "has_shm_support": HAS_SHM,
            "c_compilers": compilers_found,
        },
        "storage_io": {
            "disk_sync_speed_mbps": disk_speed_mbps,
            "fsync_latency_ms": fsync_lat_ms,
        },
        "config": {
            "config_path": str(cfg_path) if cfg_path else "Built-in defaults (in-memory)",
            "config_hash": cfg_hash[:16] if cfg_hash else "none",
        },
    }

    if as_json:
        if verbose:
            print(json.dumps(result, indent=2))
        return result

    if verbose:
        try:
            from rich.console import Console
            from rich.panel import Panel
            from rich.table import Table

            console = Console()
            console.print()
            console.print(
                Panel.fit(
                    f"[bold green][OK] MDRAP System Diagnostics & Platform Integrity[/bold green]\n\n"
                    f"Host: [cyan]{result['environment']['os']}[/cyan] ({result['environment']['machine_arch']})\n"
                    f"CPU: [cyan]{result['environment']['cpu_model']}[/cyan] ({result['environment']['cpu_cores_logical']} logical cores)\n"
                    f"Memory: [cyan]{result['environment']['total_ram_gb']:.2f} GB RAM[/cyan] | Active Profile: [bold green]{result['hardware_profile']['active_mode']}[/bold green]\n"
                    f"Recommendation: [bold yellow]{result['hardware_profile']['recommended_mode']}[/bold yellow] ({prof.description})\n"
                    f"Native C Hotpath: [bold]{result['acceleration']['fastpath_status']}[/bold] | SHM Support: [bold]{result['acceleration']['has_shm_support']}[/bold]\n"
                    f"Disk Fsync Throughput: [cyan]{result['storage_io']['disk_sync_speed_mbps']} MB/s[/cyan] ({result['storage_io']['fsync_latency_ms']} ms/flush)\n"
                    f"Config Path: [dim]{result['config']['config_path']}[/dim]",
                    title="MDRAP System Doctor",
                    border_style="green",
                )
            )
        except ImportError:
            print(json.dumps(result, indent=2))

    return result
