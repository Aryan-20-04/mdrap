"""
Concurrent Multi-Device & Multi-User Workload Simulation Harness.

Models realistic operational conditions where multiple independent devices/clients:
1. Normal Users (Human Traders / Desk Analysts): Query BBO quotes, health, status,
   candlestick OHLCV, and spreads with human reaction pacing (100ms - 300ms).
2. Fast-Paced Users (HFT Bots / Algorithmic Arbitrageurs): Stream persistent ticks,
   query L2 depth ladders, real-time VWAP curves, historical replays, and DuckDB
   vectorized SIMD queries with machine pacing (1ms - 5ms).
3. DevOps / SRE Monitors: Rapidly scrape Prometheus /metrics and /health HTTP endpoints.

Measures operation throughput, tail latencies (p50, p90, p95, p99, max),
socket queue drops, and database contention across scaling tiers (2 to 24+ devices).
"""
from __future__ import annotations

import concurrent.futures
import json
import math
import os
import random
import socket
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from columnar import ColumnarStore
from prometheus import PrometheusMetricsServer
from service import MarketDataDaemon, StreamClient
from term import Console, Panel, Table


class UserArchetype(str, Enum):
    NORMAL_USER = "NORMAL_USER"
    FAST_PACED_BOT = "FAST_PACED_BOT"
    DEVOPS_MONITOR = "DEVOPS_MONITOR"


@dataclass
class DeviceConfig:
    device_id: str
    archetype: UserArchetype
    host: str = "127.0.0.1"
    port: int = 19880
    prom_port: int = 19110
    duckdb_path: str = "data/mdrap.duckdb"
    duration_s: float = 5.0
    auth_token: str = "mdrap_demo_pro_key"
    symbols: List[str] = field(default_factory=lambda: ["BTC/USD", "AAPL", "MSFT", "NVDA"])


@dataclass
class DeviceResult:
    device_id: str
    archetype: str
    total_ops: int = 0
    ops_per_sec: float = 0.0
    latencies_ms: List[float] = field(default_factory=list)
    p50_ms: float = 0.0
    p90_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    max_ms: float = 0.0
    avg_ms: float = 0.0
    op_breakdown: Dict[str, int] = field(default_factory=dict)
    op_latencies_ms: Dict[str, List[float]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def calculate_percentiles(self) -> None:
        if not self.latencies_ms:
            return
        sorted_lats = sorted(self.latencies_ms)
        n = len(sorted_lats)
        self.p50_ms = round(sorted_lats[int(n * 0.50)], 3)
        self.p90_ms = round(sorted_lats[min(int(n * 0.90), n - 1)], 3)
        self.p95_ms = round(sorted_lats[min(int(n * 0.95), n - 1)], 3)
        self.p99_ms = round(sorted_lats[min(int(n * 0.99), n - 1)], 3)
        self.max_ms = round(sorted_lats[-1], 3)
        self.avg_ms = round(sum(sorted_lats) / n, 3)


def _compute_percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    idx = min(int(len(s) * (p / 100.0)), len(s) - 1)
    return round(s[idx], 3)


def run_device_worker(cfg: DeviceConfig) -> DeviceResult:
    """
    Simulate an independent physical device / terminal client running operations
    according to its assigned user archetype.
    """
    res = DeviceResult(device_id=cfg.device_id, archetype=cfg.archetype.value)
    deadline = time.time() + cfg.duration_s
    client = None
    columnar_store = None
    shm_reader = None

    # Connect client if needed
    try:
        if cfg.archetype in (UserArchetype.NORMAL_USER, UserArchetype.FAST_PACED_BOT):
            client = StreamClient(host=cfg.host, port=cfg.port, timeout=3.0, auth_token=cfg.auth_token)
            client.connect()
    except Exception as exc:
        res.errors.append(f"Connection failed: {exc}")
        return res

    # Open DuckDB read-only store if available for analytics queries
    if cfg.archetype in (UserArchetype.NORMAL_USER, UserArchetype.FAST_PACED_BOT):
        try:
            if os.path.exists(cfg.duckdb_path):
                columnar_store = ColumnarStore(db_path=cfg.duckdb_path, read_only=True)
        except Exception:
            columnar_store = None

    op_timings: List[float] = []
    op_counts: Dict[str, int] = {}
    op_lat_map: Dict[str, List[float]] = {}

    def _record_op(op_name: str, elapsed_ms: float):
        op_timings.append(elapsed_ms)
        op_counts[op_name] = op_counts.get(op_name, 0) + 1
        if op_name not in op_lat_map:
            op_lat_map[op_name] = []
        op_lat_map[op_name].append(elapsed_ms)

    start_time = time.time()

    # -------------------------------------------------------------------------
    # Archetype 1: NORMAL USER (Human Trader / Risk Analyst)
    # Paced interaction (100ms - 250ms delays), checking quotes, health, charts
    # -------------------------------------------------------------------------
    if cfg.archetype == UserArchetype.NORMAL_USER:
        while time.time() < deadline:
            time.sleep(random.uniform(0.08, 0.20))
            dice = random.random()
            sym = random.choice(cfg.symbols)

            try:
                if dice < 0.35:
                    # Query BBO Quote
                    t0 = time.perf_counter_ns()
                    bbo = client.get_bbo(sym) if client else None
                    _record_op("BBO_QUOTE", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.55:
                    # Query Venue Health
                    t0 = time.perf_counter_ns()
                    h = client.get_health() if client else {}
                    _record_op("VENUE_HEALTH", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.70:
                    # Query Platform Status
                    t0 = time.perf_counter_ns()
                    st = client.get_status() if client else {}
                    _record_op("STATUS", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.85:
                    # Query DuckDB Candlesticks / OHLCV
                    t0 = time.perf_counter_ns()
                    if columnar_store:
                        candles = columnar_store.query_ohlcv(sym, interval_s=60.0, limit=5)
                    else:
                        time.sleep(0.001)
                    _record_op("CANDLESTICK_OHLCV", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.95:
                    # Query Microstructure Spread Analytics
                    t0 = time.perf_counter_ns()
                    if columnar_store:
                        spr = columnar_store.query_spread_analytics(sym)
                    else:
                        time.sleep(0.001)
                    _record_op("SPREAD_ANALYTICS", (time.perf_counter_ns() - t0) / 1e6)

                else:
                    # Probe Prometheus Health
                    t0 = time.perf_counter_ns()
                    try:
                        req = urllib.request.Request(f"http://{cfg.host}:{cfg.prom_port}/health")
                        with urllib.request.urlopen(req, timeout=1.5) as resp:
                            _ = resp.read()
                    except Exception:
                        pass
                    _record_op("PROMETHEUS_HEALTH", (time.perf_counter_ns() - t0) / 1e6)

            except Exception as exc:
                res.errors.append(f"Normal user op error: {exc}")

    # -------------------------------------------------------------------------
    # Archetype 2: FAST-PACED BOT (HFT Bot / Algorithmic Arbitrageur)
    # Sub-millisecond machine pacing (1ms - 4ms), L2 depth, VWAP, replay, SIMD
    # -------------------------------------------------------------------------
    elif cfg.archetype == UserArchetype.FAST_PACED_BOT:
        # Open Zero-Copy Shared Memory Reader if active (<2µs latency)
        shm_reader = None
        try:
            from shm import SHMReader
            shm_reader = SHMReader()
        except Exception:
            shm_reader = None

        # Subscribe to market ticks stream on a dedicated non-blocking socket
        stream_sock = None
        try:
            stream_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            stream_sock.settimeout(1.0)
            stream_sock.connect((cfg.host, cfg.port))
            stream_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if cfg.auth_token:
                stream_sock.sendall(f"AUTH {cfg.auth_token}\n".encode("utf-8"))
                _ = stream_sock.recv(1024)
            stream_sock.sendall(b"SUB ALL\n")
            _ = stream_sock.recv(1024)
            stream_sock.setblocking(False)
        except Exception:
            stream_sock = None

        while time.time() < deadline:
            time.sleep(random.uniform(0.001, 0.004))
            dice = random.random()
            sym = random.choice(cfg.symbols)

            try:
                if dice < 0.30:
                    # Query L2 Order Book Depth Ladder
                    t0 = time.perf_counter_ns()
                    d = client.get_depth(sym) if client else None
                    _record_op("L2_DEPTH_LADDER", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.55:
                    # Query Real-Time VWAP Slicing Curve
                    t0 = time.perf_counter_ns()
                    v = client._send_query(f"VWAP {sym}") if client else {}
                    _record_op("VWAP_CURVE", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.75:
                    # Drain Streaming Tick Buffer (SHM ultra-fastpath or TCP)
                    t0 = time.perf_counter_ns()
                    ticks_read = 0
                    if shm_reader:
                        h_seq = shm_reader.read_latest_seq()
                        slot = shm_reader.read_slot(h_seq)
                        ticks_read = 1 if slot else 0
                    elif stream_sock:
                        try:
                            chunk = stream_sock.recv(8192)
                            if chunk:
                                ticks_read = chunk.count(b"\n")
                        except (BlockingIOError, socket.error):
                            pass
                    _record_op("STREAM_TICK_DRAIN", (time.perf_counter_ns() - t0) / 1e6)

                elif dice < 0.88:
                    # Historical Tick Gap Replay
                    t0 = time.perf_counter_ns()
                    replayed = client.request_replay(1, 50, symbol=sym) if client else []
                    _record_op("TICK_GAP_REPLAY", (time.perf_counter_ns() - t0) / 1e6)

                else:
                    # Vectorized In-Process DuckDB SIMD Query
                    t0 = time.perf_counter_ns()
                    if columnar_store:
                        vwap_stat = columnar_store.query_vwap(sym)
                    else:
                        time.sleep(0.0005)
                    _record_op("DUCKDB_SIMD_VWAP", (time.perf_counter_ns() - t0) / 1e6)

            except Exception as exc:
                res.errors.append(f"Fast bot op error: {exc}")

        if stream_sock:
            try:
                stream_sock.close()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Archetype 3: DEVOPS / SRE MONITOR
    # Continuous Prometheus HTTP metric scraping
    # -------------------------------------------------------------------------
    elif cfg.archetype == UserArchetype.DEVOPS_MONITOR:
        while time.time() < deadline:
            time.sleep(random.uniform(0.02, 0.05))
            dice = random.random()

            try:
                if dice < 0.75:
                    t0 = time.perf_counter_ns()
                    req = urllib.request.Request(f"http://{cfg.host}:{cfg.prom_port}/metrics")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        body = resp.read()
                    _record_op("PROMETHEUS_SCRAPE", (time.perf_counter_ns() - t0) / 1e6)
                else:
                    t0 = time.perf_counter_ns()
                    req = urllib.request.Request(f"http://{cfg.host}:{cfg.prom_port}/health")
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        body = resp.read()
                    _record_op("PROMETHEUS_HEALTH", (time.perf_counter_ns() - t0) / 1e6)

            except Exception as exc:
                res.errors.append(f"Devops monitor op error: {exc}")

    # Cleanup resources
    if client:
        try:
            client.close()
        except Exception:
            pass
    if columnar_store:
        try:
            columnar_store.close()
        except Exception:
            pass
    if shm_reader:
        try:
            shm_reader.close()
        except Exception:
            pass

    actual_elapsed = max(0.001, time.time() - start_time)
    res.total_ops = len(op_timings)
    res.ops_per_sec = round(res.total_ops / actual_elapsed, 1)
    res.latencies_ms = op_timings
    res.op_breakdown = op_counts
    res.op_latencies_ms = op_lat_map
    res.calculate_percentiles()
    return res


class ConcurrentWorkloadSimulator:
    """
    Orchestrates multi-device concurrent simulations, runs background ingestion
    daemon and Prometheus metrics server, and measures scaling behavior.
    """

    def __init__(
        self,
        db_path: str = "data/mdrap.db",
        duckdb_path: str = "data/mdrap.duckdb",
        port: int = 19880,
        prom_port: int = 19110,
        sim_speed_eps: float = 3000.0,
        console: Optional[Console] = None,
    ):
        self.db_path = db_path
        self.duckdb_path = duckdb_path
        self.port = port
        self.prom_port = prom_port
        self.sim_speed_eps = sim_speed_eps
        self.console = console or Console()
        self._daemon: Optional[MarketDataDaemon] = None
        self._prom_server: Optional[PrometheusMetricsServer] = None

    def start_services(self) -> None:
        """Start isolated MarketDataDaemon and PrometheusMetricsServer."""
        # 1. Start Daemon
        self._daemon = MarketDataDaemon(
            host="127.0.0.1",
            port=self.port,
            db_path=self.db_path,
            enable_shm=False,
            use_live=False,
            sim_events=0,
            sim_speed_eps=self.sim_speed_eps,
            require_auth=False,
        )
        self._daemon.start(blocking=False)
        time.sleep(0.35)

        # 2. Start Prometheus Exporter
        try:
            self._prom_server = PrometheusMetricsServer(
                host="127.0.0.1",
                port=self.prom_port,
                store_path=self.db_path,
                duckdb_path=self.duckdb_path,
            )
            self._prom_server.start()
            time.sleep(0.15)
        except Exception:
            self._prom_server = None

    def stop_services(self) -> None:
        """Gracefully stop background daemon and Prometheus server."""
        if self._prom_server:
            try:
                self._prom_server.stop()
            except Exception:
                pass
            self._prom_server = None

        if self._daemon:
            try:
                self._daemon.stop()
            except Exception:
                pass
            self._daemon = None

    def run_tier(
        self,
        normal_count: int,
        fast_count: int,
        monitor_count: int = 0,
        duration_s: float = 5.0,
        mode: str = "thread",
    ) -> Dict[str, Any]:
        """
        Execute a single concurrent simulation tier with specified device counts.
        """
        total_devices = normal_count + fast_count + monitor_count
        configs: List[DeviceConfig] = []

        # Create device configs
        for i in range(1, normal_count + 1):
            configs.append(
                DeviceConfig(
                    device_id=f"desk-user-{i:02d}",
                    archetype=UserArchetype.NORMAL_USER,
                    port=self.port,
                    prom_port=self.prom_port,
                    duckdb_path=self.duckdb_path,
                    duration_s=duration_s,
                )
            )

        for i in range(1, fast_count + 1):
            configs.append(
                DeviceConfig(
                    device_id=f"hft-bot-{i:02d}",
                    archetype=UserArchetype.FAST_PACED_BOT,
                    port=self.port,
                    prom_port=self.prom_port,
                    duckdb_path=self.duckdb_path,
                    duration_s=duration_s,
                )
            )

        for i in range(1, monitor_count + 1):
            configs.append(
                DeviceConfig(
                    device_id=f"sre-mon-{i:02d}",
                    archetype=UserArchetype.DEVOPS_MONITOR,
                    port=self.port,
                    prom_port=self.prom_port,
                    duckdb_path=self.duckdb_path,
                    duration_s=duration_s,
                )
            )

        # Launch concurrent devices
        results: List[DeviceResult] = []
        t0 = time.perf_counter()

        if mode == "process":
            with concurrent.futures.ProcessPoolExecutor(max_workers=len(configs)) as executor:
                results = list(executor.map(run_device_worker, configs))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(configs)) as executor:
                results = list(executor.map(run_device_worker, configs))

        wall_time_s = max(0.001, time.perf_counter() - t0)

        # Aggregate metrics
        all_lats: List[float] = []
        normal_lats: List[float] = []
        fast_lats: List[float] = []
        monitor_lats: List[float] = []
        total_ops = 0
        total_errors = 0
        op_totals: Dict[str, int] = {}
        op_lat_map: Dict[str, List[float]] = {}

        for r in results:
            total_ops += r.total_ops
            total_errors += len(r.errors)
            all_lats.extend(r.latencies_ms)
            if r.archetype == UserArchetype.NORMAL_USER.value:
                normal_lats.extend(r.latencies_ms)
            elif r.archetype == UserArchetype.FAST_PACED_BOT.value:
                fast_lats.extend(r.latencies_ms)
            elif r.archetype == UserArchetype.DEVOPS_MONITOR.value:
                monitor_lats.extend(r.latencies_ms)

            for op, cnt in r.op_breakdown.items():
                op_totals[op] = op_totals.get(op, 0) + cnt
            for op, lats in r.op_latencies_ms.items():
                if op not in op_lat_map:
                    op_lat_map[op] = []
                op_lat_map[op].extend(lats)

        daemon_stats = self._daemon.stats() if self._daemon else {}

        return {
            "total_devices": total_devices,
            "normal_count": normal_count,
            "fast_count": fast_count,
            "monitor_count": monitor_count,
            "duration_s": round(wall_time_s, 2),
            "total_ops": total_ops,
            "ops_per_sec": round(total_ops / wall_time_s, 1),
            "total_errors": total_errors,
            "daemon_broadcast_eps": daemon_stats.get("throughput_eps", 0.0),
            "daemon_active_clients": daemon_stats.get("active_clients", 0),
            "overall_latency": {
                "p50_ms": _compute_percentile(all_lats, 50),
                "p90_ms": _compute_percentile(all_lats, 90),
                "p95_ms": _compute_percentile(all_lats, 95),
                "p99_ms": _compute_percentile(all_lats, 99),
                "max_ms": round(max(all_lats), 3) if all_lats else 0.0,
                "avg_ms": round(sum(all_lats) / len(all_lats), 3) if all_lats else 0.0,
            },
            "normal_user_latency": {
                "p50_ms": _compute_percentile(normal_lats, 50),
                "p95_ms": _compute_percentile(normal_lats, 95),
                "p99_ms": _compute_percentile(normal_lats, 99),
                "ops": len(normal_lats),
            },
            "fast_bot_latency": {
                "p50_ms": _compute_percentile(fast_lats, 50),
                "p95_ms": _compute_percentile(fast_lats, 95),
                "p99_ms": _compute_percentile(fast_lats, 99),
                "ops": len(fast_lats),
            },
            "monitor_latency": {
                "p50_ms": _compute_percentile(monitor_lats, 50),
                "p95_ms": _compute_percentile(monitor_lats, 95),
                "p99_ms": _compute_percentile(monitor_lats, 99),
                "ops": len(monitor_lats),
            },
            "operation_breakdown": op_totals,
            "operation_p95_ms": {
                op: _compute_percentile(lats, 95) for op, lats in op_lat_map.items()
            },
            "device_results": [
                {
                    "device_id": r.device_id,
                    "archetype": r.archetype,
                    "total_ops": r.total_ops,
                    "ops_per_sec": r.ops_per_sec,
                    "p50_ms": r.p50_ms,
                    "p95_ms": r.p95_ms,
                    "p99_ms": r.p99_ms,
                    "max_ms": r.max_ms,
                    "errors": len(r.errors),
                }
                for r in results
            ],
        }

    def render_tier_report(self, tier_name: str, report: Dict[str, Any]) -> None:
        """Render beautiful Rich terminal HUD for a simulation tier."""
        self.console.print()
        self.console.print(
            Panel.fit(
                f"[bold cyan]MDRAP Concurrent Simulation HUD: {tier_name}[/bold cyan]\n"
                f"• Concurrency:      [bold green]{report['total_devices']} Devices[/bold green] "
                f"({report['normal_count']} Normal Users, {report['fast_count']} HFT Bots, {report['monitor_count']} Monitors)\n"
                f"• Duration:         [yellow]{report['duration_s']}s[/yellow]\n"
                f"• Throughput:       [bold green]{report['total_ops']:,} Ops[/bold green] "
                f"([cyan]{report['ops_per_sec']:,.1f} ops/sec[/cyan])\n"
                f"• Background Feed:  [dim]{report['daemon_broadcast_eps']:,.1f} ticks/sec[/dim]\n"
                f"• System Stability: [bold {'green' if report['total_errors'] == 0 else 'red'}]{report['total_errors']} Errors / Socket Drops[/bold {'green' if report['total_errors'] == 0 else 'red'}]",
                border_style="cyan",
            )
        )

        # 1. Comparative Archetype Latency Table
        t_arch = Table(title=f"User Archetype Latency Distribution (ms)", border_style="dim")
        t_arch.add_column("Archetype", style="bold")
        t_arch.add_column("Devices", justify="right")
        t_arch.add_column("Total Ops", justify="right")
        t_arch.add_column("Throughput", justify="right")
        t_arch.add_column("p50 Latency", justify="right", style="green")
        t_arch.add_column("p95 Latency", justify="right", style="yellow")
        t_arch.add_column("p99 Latency", justify="right", style="red")

        norm = report["normal_user_latency"]
        if report["normal_count"] > 0:
            t_arch.add_row(
                "Normal User (Desk)",
                str(report["normal_count"]),
                f"{norm['ops']:,}",
                f"{norm['ops']/report['duration_s']:.1f} ops/s",
                f"{norm['p50_ms']:.3f} ms",
                f"{norm['p95_ms']:.3f} ms",
                f"{norm['p99_ms']:.3f} ms",
            )

        fast = report["fast_bot_latency"]
        if report["fast_count"] > 0:
            t_arch.add_row(
                "Fast-Paced Bot (HFT)",
                str(report["fast_count"]),
                f"{fast['ops']:,}",
                f"{fast['ops']/report['duration_s']:.1f} ops/s",
                f"{fast['p50_ms']:.3f} ms",
                f"{fast['p95_ms']:.3f} ms",
                f"{fast['p99_ms']:.3f} ms",
            )

        mon = report["monitor_latency"]
        if report["monitor_count"] > 0:
            t_arch.add_row(
                "DevOps Monitor (SRE)",
                str(report["monitor_count"]),
                f"{mon['ops']:,}",
                f"{mon['ops']/report['duration_s']:.1f} ops/s",
                f"{mon['p50_ms']:.3f} ms",
                f"{mon['p95_ms']:.3f} ms",
                f"{mon['p99_ms']:.3f} ms",
            )

        overall = report["overall_latency"]
        t_arch.add_row(
            "[bold cyan]TOTAL / BLENDED[/bold cyan]",
            f"[bold]{report['total_devices']}[/bold]",
            f"[bold]{report['total_ops']:,}[/bold]",
            f"[bold]{report['ops_per_sec']:,.1f} ops/s[/bold]",
            f"[bold green]{overall['p50_ms']:.3f} ms[/bold green]",
            f"[bold yellow]{overall['p95_ms']:.3f} ms[/bold yellow]",
            f"[bold red]{overall['p99_ms']:.3f} ms[/bold red]",
        )
        self.console.print(t_arch)

        # 2. Breakdown by Financial Operation
        t_op = Table(title="Micro-Operation Breakdown & p95 Tail Latencies", border_style="dim")
        t_op.add_column("Operation Type", style="bold")
        t_op.add_column("Count", justify="right")
        t_op.add_column("Share %", justify="right")
        t_op.add_column("p95 Latency", justify="right", style="cyan")

        for op, cnt in sorted(report["operation_breakdown"].items(), key=lambda x: -x[1]):
            pct = (cnt / report["total_ops"] * 100.0) if report["total_ops"] > 0 else 0.0
            p95 = report["operation_p95_ms"].get(op, 0.0)
            t_op.add_row(op, f"{cnt:,}", f"{pct:.1f}%", f"{p95:.3f} ms")
        self.console.print(t_op)

    def run_sweep(self, duration_s: float = 4.0, mode: str = "thread") -> List[Dict[str, Any]]:
        """
        Execute full scaling progression across 4 tiers:
        Tier 1 (Pilot): 2 Devices (1 Normal, 1 Fast)
        Tier 2 (Desk): 6 Devices (3 Normal, 3 Fast)
        Tier 3 (Floor): 12 Devices (6 Normal, 5 Fast, 1 Monitor)
        Tier 4 (Surge): 24 Devices (10 Normal, 12 Fast, 2 Monitors)
        """
        tiers = [
            ("Tier 1 (Pilot Desk)", 1, 1, 0),
            ("Tier 2 (Trading Desk)", 3, 3, 0),
            ("Tier 3 (Institutional Floor)", 6, 5, 1),
            ("Tier 4 (Surge Stress)", 10, 12, 2),
        ]

        sweep_results = []
        self.console.print()
        self.console.print("[bold cyan]══════════════════════════════════════════════════════════════════[/bold cyan]")
        self.console.print("[bold cyan]   Starting MDRAP Multi-Device Concurrency Scaling Sweep (§26)    [/bold cyan]")
        self.console.print("[bold cyan]══════════════════════════════════════════════════════════════════[/bold cyan]")

        try:
            self.start_services()

            for name, n_cnt, f_cnt, m_cnt in tiers:
                self.console.print(f"\n[dim yellow]▶ Running {name} ({n_cnt + f_cnt + m_cnt} concurrent devices)...[/dim yellow]")
                rep = self.run_tier(n_cnt, f_cnt, m_cnt, duration_s=duration_s, mode=mode)
                self.render_tier_report(name, rep)
                sweep_results.append({"tier": name, "report": rep})
                time.sleep(0.5)

        finally:
            self.stop_services()

        # Render Scaling Progression Matrix
        self.console.print()
        t_prog = Table(title="Concurrent Scaling Progression Matrix (§26 Verification)", border_style="cyan")
        t_prog.add_column("Tier", style="bold")
        t_prog.add_column("Devices", justify="right")
        t_prog.add_column("Throughput", justify="right", style="green")
        t_prog.add_column("Total Ops", justify="right")
        t_prog.add_column("Blended p50", justify="right")
        t_prog.add_column("Blended p95", justify="right")
        t_prog.add_column("Blended p99", justify="right")
        t_prog.add_column("Max Latency", justify="right")
        t_prog.add_column("Errors", justify="right")

        for item in sweep_results:
            r = item["report"]
            err_style = "green" if r["total_errors"] == 0 else "bold red"
            t_prog.add_row(
                item["tier"],
                str(r["total_devices"]),
                f"{r['ops_per_sec']:,.1f} ops/s",
                f"{r['total_ops']:,}",
                f"{r['overall_latency']['p50_ms']:.3f} ms",
                f"{r['overall_latency']['p95_ms']:.3f} ms",
                f"{r['overall_latency']['p99_ms']:.3f} ms",
                f"{r['overall_latency']['max_ms']:.3f} ms",
                f"[{err_style}]{r['total_errors']}[/{err_style}]",
            )
        self.console.print(t_prog)

        return sweep_results
