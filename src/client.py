"""
MDRAP Official Institutional Client SDK (Spec §18).

High-performance, zero-dependency client library for subscribing to MDRAP
normalized market data streams (Consolidated L1 NBBO and Consolidated L2 Depth Ladders),
with automated monotonic sequence validation, wire-to-wire latency tracking,
and transparent in-memory gap recovery.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import socket
import time
from typing import Any, Callable, Dict, Generator, Iterator, List, Optional, Set, Tuple


@dataclass
class MarketEvent:
    """
    Normalized market event received from MDRAP streaming distribution.
    Represents either a Consolidated L1 Tick or a Consolidated L2 Depth snapshot.
    """
    seq: int
    event_type: str                   # 'TICK' or 'DEPTH'
    symbol: str
    price: Optional[float] = None
    size: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    source: str = ""
    status: str = "VALID"
    bbo: Optional[dict] = None
    # L2 Depth specific fields
    bids: List[List[Any]] = field(default_factory=list)  # [[price, size, venue], ...]
    asks: List[List[Any]] = field(default_factory=list)  # [[price, size, venue], ...]
    aggregated_bids: List[dict] = field(default_factory=list)
    aggregated_asks: List[dict] = field(default_factory=list)
    vwap_curve: Optional[dict] = None
    total_bid_notional: Optional[float] = None
    total_ask_notional: Optional[float] = None
    micro_price: Optional[float] = None
    ofi: Optional[float] = None
    is_crossed: bool = False
    arbitrage: List[dict] = field(default_factory=list)
    # Timestamps & Telemetry
    exchange_ts: float = 0.0
    ingest_ts: float = 0.0
    broadcast_ts: float = 0.0
    recv_ts: float = 0.0
    engine_us: float = 0.0

    @property
    def is_depth(self) -> bool:
        return self.event_type == "DEPTH"

    @property
    def is_tick(self) -> bool:
        return self.event_type == "TICK"

    @property
    def spread(self) -> Optional[float]:
        if self.bid_price is not None and self.ask_price is not None:
            return round(self.ask_price - self.bid_price, 4)
        if self.bids and self.asks:
            return round(self.asks[0][0] - self.bids[0][0], 4)
        return None

    @property
    def wire_latency_us(self) -> float:
        """Network transmission latency from daemon broadcast to client reception in microseconds."""
        if self.broadcast_ts > 0 and self.recv_ts >= self.broadcast_ts:
            return round((self.recv_ts - self.broadcast_ts) * 1_000_000.0, 1)
        return 0.0

    @property
    def total_platform_latency_us(self) -> float:
        """Total time from feed ingestion to client reception in microseconds."""
        if self.ingest_ts > 0 and self.recv_ts >= self.ingest_ts:
            return round((self.recv_ts - self.ingest_ts) * 1_000_000.0, 1)
        return 0.0

    @classmethod
    def from_dict(cls, data: dict, recv_ts: float = 0.0) -> MarketEvent:
        recv = recv_ts if recv_ts > 0 else time.time()
        ev_type = str(data.get("type", "TICK")).upper()
        sym = str(data.get("sym", ""))

        if ev_type == "VWAP":
            curve = data.get("vwap_curve", {})
            return cls(
                seq=int(data.get("seq", 0)),
                event_type="VWAP",
                symbol=sym,
                bid_price=curve.get("best_bid"),
                ask_price=curve.get("best_ask"),
                vwap_curve=curve,
                exchange_ts=float(data.get("exchange_ts", 0.0)),
                ingest_ts=float(data.get("ingest_ts", 0.0)),
                broadcast_ts=float(data.get("broadcast_ts", 0.0)),
                recv_ts=recv,
                engine_us=float(data.get("engine_us", data.get("proc_us", 0.0))),
            )
        elif ev_type == "DEPTH":
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            best_bid = bids[0][0] if bids else None
            best_ask = asks[0][0] if asks else None
            bid_sz = bids[0][1] if bids else None
            ask_sz = asks[0][1] if asks else None

            return cls(
                seq=int(data.get("seq", 0)),
                event_type="DEPTH",
                symbol=sym,
                bid_price=best_bid,
                ask_price=best_ask,
                bid_size=bid_sz,
                ask_size=ask_sz,
                bids=bids,
                asks=asks,
                aggregated_bids=data.get("aggregated_bids", []),
                aggregated_asks=data.get("aggregated_asks", []),
                vwap_curve=data.get("vwap_curve"),
                total_bid_notional=data.get("total_bid_notional"),
                total_ask_notional=data.get("total_ask_notional"),
                micro_price=data.get("micro_price"),
                ofi=data.get("ofi"),
                is_crossed=bool(data.get("is_crossed", False)),
                arbitrage=data.get("arbitrage", []),
                exchange_ts=float(data.get("exchange_ts", 0.0)),
                ingest_ts=float(data.get("ingest_ts", 0.0)),
                broadcast_ts=float(data.get("broadcast_ts", 0.0)),
                recv_ts=recv,
                engine_us=float(data.get("engine_us", data.get("proc_us", 0.0))),
            )
        else:
            return cls(
                seq=int(data.get("seq", 0)),
                event_type="TICK",
                symbol=sym,
                price=data.get("price"),
                size=data.get("size"),
                bid_price=data.get("bid"),
                ask_price=data.get("ask"),
                bid_size=data.get("bid_size"),
                ask_size=data.get("ask_size"),
                source=str(data.get("source", "")),
                status=str(data.get("status", "VALID")),
                bbo=data.get("bbo"),
                is_crossed=bool(data.get("bbo", {}).get("crossed", False)) if data.get("bbo") else False,
                exchange_ts=float(data.get("exchange_ts", 0.0)),
                ingest_ts=float(data.get("ingest_ts", 0.0)),
                broadcast_ts=float(data.get("broadcast_ts", 0.0)),
                recv_ts=recv,
                engine_us=float(data.get("engine_us", data.get("proc_us", 0.0))),
            )


class MDRAPClient:
    """
    Institutional Python Client for MDRAP Market Data Streaming.
    Supports L1 ticks, L2 depth ladders, microsecond latency metrics, and automated gap recovery.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        auth_token: Optional[str] = None,
        auto_replay: bool = True,
        max_replay_gap: int = 2000,
        timeout: float = 5.0,
        use_shm: bool = False,
        shm_name: str = "mdrap_feed",
        use_binary: bool = False,
        transport: str = "auto",  # 'auto', 'shm', 'binary', 'tcp'
    ):
        self.host = host
        self.port = port
        import os
        self.auth_token = auth_token if auth_token is not None else os.environ.get("MDRAP_DAEMON_TOKEN", "")
        self.auto_replay = auto_replay
        self.max_replay_gap = max_replay_gap
        self.timeout = timeout
        self.transport = transport
        self.use_shm = use_shm or (transport == "shm") or (transport == "auto" and port == 9876)
        self.shm_name = shm_name
        self.use_binary = use_binary or (transport == "binary")
        self.transport_type: str = "DISCONNECTED"

        self.sock: Optional[socket.socket] = None
        self.shm_reader = None
        self._subscribed_symbols: Set[str] = set()
        self._last_seq: Optional[int] = None

        # Telemetry stats
        self.tier: Optional[str] = None
        self.client_id: Optional[str] = None
        self._events_received = 0
        self._gaps_detected = 0
        self._events_replayed = 0
        self._wire_latencies_us: List[float] = []
        self._engine_latencies_us: List[float] = []

    def __enter__(self) -> MDRAPClient:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def connect(self) -> None:
        """
        Establish connection with MDRAP streaming daemon using decoupled fallback hierarchy:
        1. Shared Memory (<1µs) if localhost and available.
        2. Binary Wire Protocol (<30µs) over TCP.
        3. Standard JSON TCP (<2ms) fallback.
        """
        # 1. Tier 1: Try Zero-Copy Shared Memory
        is_local = self.host in ("127.0.0.1", "localhost", "::1")
        if (self.use_shm or self.transport in ("auto", "shm")) and is_local:
            if self.use_shm or self.transport == "shm":
                try:
                    from shm import SHMReader
                    reader = SHMReader(name=self.shm_name)
                    # Verify writer is active
                    if reader.is_writer_alive():
                        self.shm_reader = reader
                        self.transport_type = "SHM"
                        return
                    else:
                        reader.close()
                except Exception:
                    self.shm_reader = None

        if self.transport == "shm":
            raise ConnectionError(f"Shared memory '{self.shm_name}' unavailable or publisher not running.")

        # 2. Tier 2 / 3: Fallback to TCP Socket
        if self.sock is not None:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        if self.auth_token:
            sock.sendall(f"AUTH {self.auth_token}\n".encode("utf-8"))
            buf = ""
            while "\n" not in buf:
                chunk = sock.recv(4096).decode("utf-8")
                if not chunk:
                    break
                buf += chunk
            ack = json.loads(buf.strip().split("\n")[0])
            if ack.get("status") != "OK":
                sock.close()
                raise PermissionError(f"MDRAP authentication failed: {ack.get('error')}")
            self.tier = ack.get("tier")
            self.client_id = ack.get("client_id")

        if self.use_binary:
            try:
                sock.sendall(b"FORMAT BINARY\n")
                self.transport_type = "BINARY_TCP"
            except Exception:
                self.transport_type = "JSON_TCP"
        else:
            self.transport_type = "JSON_TCP"

        self.sock = sock

    def close(self) -> None:
        """Cleanly close connection."""
        if self.shm_reader:
            try:
                self.shm_reader.close()
            except Exception:
                pass
            self.shm_reader = None

        if self.sock:
            try:
                self.sock.sendall(b"QUIT\n")
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def is_connected(self) -> bool:
        return self.shm_reader is not None or self.sock is not None

    def _send_query(self, cmd: str) -> dict:
        """Execute an instantaneous query over a dedicated socket connection."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        try:
            if self.auth_token:
                sock.sendall(f"AUTH {self.auth_token}\n".encode("utf-8"))
                buf = ""
                while "\n" not in buf:
                    chunk = sock.recv(4096).decode("utf-8")
                    if not chunk:
                        break
                    buf += chunk
                ack = json.loads(buf.strip().split("\n")[0])
                if ack.get("status") != "OK":
                    return {"error": "UNAUTHORIZED"}

            sock.sendall((cmd.strip() + "\n").encode("utf-8"))
            buf = ""
            while "\n" not in buf:
                chunk = sock.recv(4096).decode("utf-8")
                if not chunk:
                    break
                buf += chunk
            line = buf.strip().split("\n")[0] if buf else "{}"
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return {}
        finally:
            sock.close()

    def get_status(self) -> dict:
        """Fetch daemon operational status and throughput metrics."""
        return self._send_query("STATUS").get("telemetry", {})

    def get_health(self) -> dict:
        """Fetch real-time venue health from the watchdog."""
        return self._send_query("HEALTH").get("health", {})

    def get_bbo(self, symbol: str = "BTC/USD") -> Optional[dict]:
        """Fetch current Consolidated NBBO quote for an instrument."""
        return self._send_query(f"BBO {symbol}").get("bbo")

    def get_depth(self, symbol: str = "BTC/USD") -> Optional[dict]:
        """Fetch current Consolidated L2 Depth ladder for an instrument."""
        return self._send_query(f"DEPTH {symbol}").get("depth")

    def get_vwap(self, symbol: str = "BTC/USD", sizes: Optional[List[float]] = None) -> Optional[dict]:
        """
        Fetch real-time VWAP slicing curve and liquidity depth for an instrument.
        """
        cmd = f"VWAP {symbol}"
        if sizes:
            cmd += " " + " ".join(str(s) for s in sizes)
        resp = self._send_query(cmd)
        if resp.get("status") == "ERROR":
            err = resp.get("error", "Unknown error")
            if "FORBIDDEN" in err:
                raise PermissionError(f"MDRAP entitlement error: {err}")
            raise RuntimeError(f"MDRAP server error: {err}")
        return resp.get("vwap_curve")

    def ping(self) -> float:
        """Measure round-trip ping latency to daemon in milliseconds."""
        t0 = time.perf_counter()
        resp = self._send_query("STATUS")
        dt_ms = (time.perf_counter() - t0) * 1000.0
        return round(dt_ms, 2)

    def subscribe(self, symbols: list[str] | str, include_depth: bool = False, include_vwap: bool = False) -> None:
        """
        Subscribe to market data for specified symbols.
        Set include_depth=True to receive Consolidated L2 Depth ladders.
        Set include_vwap=True to receive Real-Time VWAP Slicing Curves.
        """
        if not self.is_connected():
            self.connect()

        if isinstance(symbols, str):
            symbols = [symbols]

        for s in symbols:
            s_clean = s.upper()
            self._subscribed_symbols.add(s_clean)
            if include_depth:
                self._subscribed_symbols.add(f"L2:{s_clean}")
            if include_vwap:
                self._subscribed_symbols.add(f"VWAP:{s_clean}")

            if self.sock:
                self.sock.sendall(f"SUB {s_clean}\n".encode("utf-8"))
                if include_depth:
                    self.sock.sendall(f"SUB L2:{s_clean}\n".encode("utf-8"))
                if include_vwap:
                    self.sock.sendall(f"SUB VWAP:{s_clean}\n".encode("utf-8"))

    def unsubscribe(self, symbols: list[str] | str, include_depth: bool = False, include_vwap: bool = False) -> None:
        """Unsubscribe from specified symbols."""
        if not self.is_connected():
            return

        if isinstance(symbols, str):
            symbols = [symbols]

        for s in symbols:
            s_clean = s.upper()
            self._subscribed_symbols.discard(s_clean)
            if include_depth:
                self._subscribed_symbols.discard(f"L2:{s_clean}")
            if include_vwap:
                self._subscribed_symbols.discard(f"VWAP:{s_clean}")

            if self.sock:
                self.sock.sendall(f"UNSUB {s_clean}\n".encode("utf-8"))
                if include_depth:
                    self.sock.sendall(f"UNSUB L2:{s_clean}\n".encode("utf-8"))
                if include_vwap:
                    self.sock.sendall(f"UNSUB VWAP:{s_clean}\n".encode("utf-8"))

    def request_replay(
        self,
        from_seq: int,
        to_seq: int,
        symbol: Optional[str] = None,
    ) -> List[MarketEvent]:
        """
        Explicitly request historical replayed events from the daemon's circular cache.
        Returns ordered list of MarketEvents.
        """
        cmd = f"REPLAY {from_seq} {to_seq}"
        if symbol:
            cmd += f" {symbol}"
        res = self._send_query(cmd)
        if res.get("status") == "ERROR" and "FORBIDDEN" in str(res.get("error", "")):
            raise PermissionError(res.get("error"))
        raw_events = res.get("events", [])
        now = time.time()
        return [MarketEvent.from_dict(e, recv_ts=now) for e in raw_events]

    def stream(
        self,
        timeout: Optional[float] = None,
        max_events: Optional[int] = None,
    ) -> Generator[MarketEvent, None, None]:
        """
        Synchronous generator yielding live MarketEvent objects.
        Validates stream sequence numbers and transparently recovers gaps via in-memory replay.
        """
        if not self.is_connected():
            self.connect()

        # If streaming via Shared Memory (sub-microsecond IPC)
        if self.shm_reader:
            # Check epoch before streaming in case publisher restarted while client was idle
            if not self.shm_reader.check_epoch_valid():
                try:
                    self.shm_reader.close()
                    from shm import SHMReader
                    self.shm_reader = SHMReader(name=self.shm_name)
                except Exception:
                    pass

            count = 0
            t_start = time.time()
            while True:
                rem_timeout = max(0.001, timeout - (time.time() - t_start)) if timeout is not None else None
                rem_events = (max_events - count) if max_events is not None else None
                epoch_reset = False

                try:
                    for item in self.shm_reader.stream(timeout=rem_timeout, max_events=rem_events):
                        # Detect publisher restart via epoch generation check
                        if not self.shm_reader.check_epoch_valid():
                            # Publisher restarted! Re-attach cleanly without crashing
                            try:
                                self.shm_reader.close()
                                from shm import SHMReader
                                self.shm_reader = SHMReader(name=self.shm_name)
                                epoch_reset = True
                                break  # Break to outer while loop to resume with new reader
                            except Exception:
                                break

                        sym = item.get("sym")
                        if "ALL" in self._subscribed_symbols or not self._subscribed_symbols or sym in self._subscribed_symbols:
                            t_recv = item.get("recv_ts", time.time())
                            ev = MarketEvent.from_dict(item, recv_ts=t_recv)
                            self._events_received += 1
                            self._record_latency(ev)
                            yield ev
                            count += 1
                            if max_events and count >= max_events:
                                return

                    if max_events and count >= max_events:
                        return

                    if epoch_reset:
                        if timeout is not None and (time.time() - t_start) >= timeout:
                            return
                        continue

                    if timeout is not None:
                        return
                except Exception:
                    # If SHM stream encountered fatal error, attempt graceful fallback to TCP
                    try:
                        if self.shm_reader:
                            self.shm_reader.close()
                            self.shm_reader = None
                    except Exception:
                        pass
                    break

            if not self.shm_reader:
                # Fallback to TCP if SHM degraded
                self.connect()

        # If streaming via Binary Wire Protocol
        if self.use_binary:
            if not self._subscribed_symbols:
                self.subscribe("ALL")
            from protocol import BinaryStreamParser
            parser = BinaryStreamParser()
            self.sock.settimeout(timeout if timeout is not None else self.timeout)
            count = 0

            while True:
                try:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break
                    events = parser.feed(chunk)
                    for data in events:
                        t_recv = time.time()
                        ev = MarketEvent.from_dict(data, recv_ts=t_recv)

                        # Monotonic sequence gap verification & automated recovery
                        if self._last_seq is not None and ev.seq > self._last_seq + 1:
                            gap_size = ev.seq - (self._last_seq + 1)
                            self._gaps_detected += 1
                            if self.auto_replay and gap_size <= self.max_replay_gap:
                                missing = self.request_replay(self._last_seq + 1, ev.seq - 1)
                                for m in missing:
                                    self._events_replayed += 1
                                    self._events_received += 1
                                    self._record_latency(m)
                                    yield m
                                    count += 1
                                    if max_events and count >= max_events:
                                        return

                        self._last_seq = ev.seq
                        self._events_received += 1
                        self._record_latency(ev)

                        yield ev
                        count += 1
                        if max_events and count >= max_events:
                            return
                except (socket.timeout, TimeoutError):
                    if timeout is not None:
                        return
                    continue
                except (KeyboardInterrupt, GeneratorExit):
                    break
                except Exception:
                    break
            return

        # If not already subscribed to anything on TCP, default to ALL
        if not self._subscribed_symbols:
            self.subscribe("ALL")

        self.sock.settimeout(timeout if timeout is not None else self.timeout)
        raw_buf = bytearray()
        count = 0

        while True:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                raw_buf.extend(chunk)

                while b"\n" in raw_buf:
                    line_bytes, _, rest = raw_buf.partition(b"\n")
                    raw_buf = bytearray(rest)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if data.get("status") == "ERROR" and "FORBIDDEN" in str(data.get("error", "")):
                        raise PermissionError(data.get("error"))

                    # Skip non-market control frames (e.g. SUB ack)
                    msg_type = data.get("type")
                    if msg_type not in ("TICK", "DEPTH"):
                        continue

                    t_recv = time.time()
                    ev = MarketEvent.from_dict(data, recv_ts=t_recv)

                    # Monotonic sequence gap verification & automated recovery
                    if self._last_seq is not None and ev.seq > self._last_seq + 1:
                        gap_size = ev.seq - (self._last_seq + 1)
                        self._gaps_detected += 1
                        if self.auto_replay and gap_size <= self.max_replay_gap:
                            missing = self.request_replay(self._last_seq + 1, ev.seq - 1)
                            for m in missing:
                                self._events_replayed += 1
                                self._events_received += 1
                                self._record_latency(m)
                                yield m
                                count += 1
                                if max_events and count >= max_events:
                                    return

                    self._last_seq = ev.seq
                    self._events_received += 1
                    self._record_latency(ev)

                    yield ev
                    count += 1
                    if max_events and count >= max_events:
                        return

            except (socket.timeout, TimeoutError):
                if timeout is not None:
                    return
                continue
            except (KeyboardInterrupt, GeneratorExit):
                break
            except Exception:
                break

    def _record_latency(self, ev: MarketEvent) -> None:
        if ev.wire_latency_us > 0:
            self._wire_latencies_us.append(ev.wire_latency_us)
            if len(self._wire_latencies_us) > 10000:
                self._wire_latencies_us = self._wire_latencies_us[-5000:]
        if ev.engine_us > 0:
            self._engine_latencies_us.append(ev.engine_us)
            if len(self._engine_latencies_us) > 10000:
                self._engine_latencies_us = self._engine_latencies_us[-5000:]

    def stats(self) -> Dict[str, Any]:
        """Return comprehensive client telemetry and latency percentiles."""
        wire_sorted = sorted(self._wire_latencies_us) if self._wire_latencies_us else []
        eng_sorted = sorted(self._engine_latencies_us) if self._engine_latencies_us else []

        def p(arr: list[float], pct: float) -> float:
            if not arr:
                return 0.0
            idx = int(len(arr) * pct)
            return arr[min(idx, len(arr) - 1)]

        overruns = self.shm_reader.overrun_stats.total_laps if self.shm_reader else 0
        skipped = self.shm_reader.overrun_stats.skipped_ticks if self.shm_reader else 0

        return {
            "transport_type": self.transport_type,
            "events_received": self._events_received,
            "gaps_detected": self._gaps_detected,
            "events_replayed": self._events_replayed,
            "overruns": overruns,
            "skipped_ticks": skipped,
            "wire_latency_p50_us": p(wire_sorted, 0.50),
            "wire_latency_p95_us": p(wire_sorted, 0.95),
            "wire_latency_p99_us": p(wire_sorted, 0.99),
            "engine_latency_p50_us": p(eng_sorted, 0.50),
            "engine_latency_p99_us": p(eng_sorted, 0.99),
        }


# ---------------------------------------------------------------------------
# Asyncio TCP Gateway Client (MDRAP Python Client SDK)
# ---------------------------------------------------------------------------

_sdk_logger = logging.getLogger("mdrap.sdk")


class MDrapClient:
    """Provides an asynchronous Python interface to the MDRAP TCP Gateway."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.running = False
        self._handlers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}

    def on(self, msg_type: str, handler: Callable[[Dict[str, Any]], None]):
        """Register an event handler for a specific message type."""
        if msg_type not in self._handlers:
            self._handlers[msg_type] = []
        self._handlers[msg_type].append(handler)

    def _dispatch(self, msg: Dict[str, Any]):
        """Dispatch message to registered handlers."""
        msg_type = msg.get("type")
        if not msg_type:
            return
        for handler in self._handlers.get(msg_type, []):
            try:
                handler(msg)
            except Exception as e:
                _sdk_logger.error(f"Error in handler for {msg_type}: {e}")

    async def connect(self) -> bool:
        """Establish connection to the MDRAP TCP Gateway."""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            line = await self.reader.readline()
            welcome = json.loads(line.decode("utf-8").strip())
            if welcome.get("type") == "system" and welcome.get("status") == "connected":
                _sdk_logger.info(f"Connected to MDRAP Gateway at {self.host}:{self.port}")
                self.running = True
                self._dispatch(welcome)
                return True
            return False
        except Exception as e:
            _sdk_logger.error(f"Failed to connect: {e}")
            return False

    async def _heartbeat_loop(self):
        """Send periodic pings to keep the connection alive."""
        while self.running and self.writer:
            try:
                ping_msg = json.dumps({"action": "ping"}) + "\n"
                self.writer.write(ping_msg.encode("utf-8"))
                await self.writer.drain()
                await asyncio.sleep(10.0)
            except Exception:
                self.running = False
                break

    async def _read_loop(self):
        """Read incoming events and dispatch them."""
        while self.running and self.reader:
            try:
                line = await self.reader.readline()
                if not line:
                    self.running = False
                    break
                msg = json.loads(line.decode("utf-8").strip())
                self._dispatch(msg)
            except Exception as e:
                _sdk_logger.error(f"Read error: {e}")
                self.running = False
                break

    async def subscribe(self, on_event: Optional[Callable[[Dict[str, Any]], None]] = None):
        """Subscribe to live events and block while listening."""
        if on_event:
            self.on("event", on_event)

        if not self.reader or not self.writer:
            connected = await self.connect()
            if not connected:
                raise ConnectionError("Could not connect to MDRAP Gateway")

        try:
            await asyncio.gather(self._heartbeat_loop(), self._read_loop())
        finally:
            await self.close()

    async def close(self):
        """Close the connection to the Gateway."""
        self.running = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None

    def to_dataframe(self, events: List[Dict[str, Any]]) -> Any:
        """Convert a list of raw event dictionaries to a Pandas DataFrame."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is not installed. Run `pip install pandas` to use this feature.")

        df = pd.DataFrame(events)
        if not df.empty and "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], unit="s")
            df.set_index("ts", inplace=True)
            df.sort_index(inplace=True)
        return df

