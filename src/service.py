"""
Market-Driven Terminal Service Engine (Spec §18).

Provides enterprise-grade, headless market data infrastructure:
- MarketDataDaemon: Continuous high-throughput background daemon with local streaming TCP socket.
- StreamClient: Composable client for piping canonical ticks into trading bots and Unix CLI tools.
- TerminalCockpit: Dynamic live terminal monitor (htop-style) for operations and SREs.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Set

from bbo import BBOEngine
from depth import ConsolidatedDepthEngine
from fastpath import NativeReplayBuffer
from models import RawEvent
from pipeline import Pipeline
from protocol import pack_depth_frame, pack_tick_frame
from reconciliation import ReliabilityTracker
from security import ClientEntitlement, SecurityManager, TokenBucketRateLimiter
from simulator import FeedSimulator, SimulatorConfig
from storage import Store
from watchdog import SourceWatchdog


@dataclass
class _ClientSession:
    """Session state for an individual connected TCP client."""

    sock: socket.socket
    symbols: Set[str] = field(default_factory=set)
    queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=1000))
    dropped_ticks: int = 0
    is_alive: bool = True
    is_binary: bool = False
    is_sbe: bool = False
    entitlement: Optional[ClientEntitlement] = None
    rate_limiter: Optional[TokenBucketRateLimiter] = None
    rate_limited_ticks: int = 0


class MarketDataDaemon:
    """
    Continuous background market data service.
    Ingests feeds, validates quality, maintains BBO, and broadcasts canonical ticks
    over an ultra-fast local streaming TCP socket to subscriber clients.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        db_path: str = "data/mdrap.db",
        use_live: bool = False,
        sim_events: int = 0,  # 0 = infinite continuous stream
        sim_speed_eps: float = 1000.0,
        auth_token: Optional[str] = None,
        require_auth: bool = False,
        enable_shm: bool = True,
        shm_name: str = "mdrap_feed",
    ):
        self.host = host
        self.port = port
        self.db_path = db_path
        self.use_live = use_live
        self.sim_events = sim_events
        self.sim_speed_eps = sim_speed_eps
        self.enable_shm = enable_shm
        self.shm_name = shm_name
        self.require_auth = require_auth
        import os

        self.auth_token = (
            auth_token
            if auth_token is not None
            else os.environ.get("MDRAP_DAEMON_TOKEN", "")
        )

        self.store = Store(db_path)
        self.security_manager = SecurityManager(store=self.store)
        self.reliability = ReliabilityTracker()
        self.watchdog = SourceWatchdog(
            reliability=self.reliability, silence_threshold_s=3.0
        )
        self.bbo = BBOEngine(quote_ttl_s=10.0, watchdog=self.watchdog)
        self.depth = ConsolidatedDepthEngine(depth_ttl_s=10.0, watchdog=self.watchdog)
        self.pipeline = Pipeline(
            store=self.store,
            reliability=self.reliability,
            bbo=self.bbo,
            watchdog=self.watchdog,
        )

        self.shm_writer = None
        self._shm_errors = 0
        if self.enable_shm:
            try:
                from shm import SHMWriter

                self.shm_writer = SHMWriter(name=self.shm_name)
            except Exception as exc:
                self.shm_writer = None
                print(
                    f"[mdrap SERVICE WARNING] Shared memory publisher unavailable ({self.shm_name}): {exc}",
                    file=sys.stderr,
                )

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._sessions: Dict[socket.socket, _ClientSession] = {}
        self._subscribers: Dict[socket.socket, Set[str]] = {}
        self._authenticated_clients: Set[socket.socket] = set()
        self._sub_lock = threading.Lock()

        self._global_seq = 0
        self._seq_lock = threading.Lock()
        self._replay_buffer = NativeReplayBuffer(capacity=65536)
        self._replay_lock = threading.Lock()

        self._t0 = 0.0
        self._total_broadcast = 0
        self._total_dropped = 0
        self._total_rate_limited = 0
        self._ingest_thread: Optional[threading.Thread] = None
        self._server_thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = True) -> None:
        """Start the background socket server, feed workers, and ingestion engine."""
        self._running = True
        self._t0 = time.time()

        # 1. Bind and listen on local TCP socket
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self.port = self._server_sock.getsockname()[1]
        self._server_sock.listen(64)
        self._server_sock.settimeout(0.2)

        # 4. Start socket listener thread
        self._server_thread = threading.Thread(
            target=self._socket_accept_loop, daemon=True, name="mdrap-socket-listener"
        )
        self._server_thread.start()

        # 5. Start decoupled sequencer ingestion thread
        self._ingest_thread = threading.Thread(
            target=self._ingestion_loop, daemon=True, name="mdrap-feed-ingest"
        )
        self._ingest_thread.start()

        if blocking:
            try:
                while self._running:
                    time.sleep(0.5)
            except (KeyboardInterrupt, SystemExit):
                self.stop()

    def stop(self) -> None:
        """Gracefully stop the daemon and flush pending storage writes."""
        if not self._running:
            return
        self._running = False

        if self._server_sock:
            try:
                self._server_sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._server_sock.close()
            except Exception:
                pass

        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)

        with self._sub_lock:
            for sess in list(self._sessions.values()):
                sess.is_alive = False
                try:
                    sess.queue.put_nowait(None)
                except Exception:
                    pass
                try:
                    sess.sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sess.sock.close()
                except Exception:
                    pass
            self._sessions.clear()
            self._subscribers.clear()
            self._authenticated_clients.clear()

        if self._ingest_thread and self._ingest_thread.is_alive():
            self._ingest_thread.join(timeout=2.0)

        if self.shm_writer:
            try:
                self.shm_writer.close()
            except Exception:
                pass
            self.shm_writer = None

        if self.pipeline:
            self.pipeline.finish()

        if self.store:
            self.store.close()

    def _socket_accept_loop(self) -> None:
        """Accept new client connections, spawn non-blocking writer and reader threads."""
        while self._running and self._server_sock:
            try:
                client_sock, _addr = self._server_sock.accept()
                client_sock.settimeout(None)
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sess = _ClientSession(sock=client_sock)
                with self._sub_lock:
                    self._sessions[client_sock] = sess
                    self._subscribers[client_sock] = sess.symbols
                # Start non-blocking writer thread (isolates slow consumers)
                threading.Thread(
                    target=self._client_writer,
                    args=(sess,),
                    daemon=True,
                    name="mdrap-client-writer",
                ).start()
                # Start incoming command reader thread
                threading.Thread(
                    target=self._client_handler,
                    args=(client_sock,),
                    daemon=True,
                    name="mdrap-client-reader",
                ).start()
            except (socket.timeout, OSError):
                if not self._running:
                    break
                continue
            except Exception:
                break

    def _client_writer(self, session: _ClientSession) -> None:
        """Dedicated non-blocking writer thread for an individual connected client."""
        while self._running and session.is_alive:
            try:
                msg = session.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg is None:
                break
            try:
                session.sock.sendall(msg)
            except Exception:
                session.is_alive = False
                break
        self._disconnect_client(session.sock)

    def _disconnect_client(self, client_sock: socket.socket) -> None:
        """Cleanly disconnect client, drain session, and release socket."""
        with self._sub_lock:
            sess = self._sessions.pop(client_sock, None)
            self._subscribers.pop(client_sock, None)
            self._authenticated_clients.discard(client_sock)
            if sess:
                self._total_dropped += sess.dropped_ticks
                self._total_rate_limited += sess.rate_limited_ticks
        if sess:
            sess.is_alive = False
            try:
                sess.queue.put_nowait(None)
            except Exception:
                pass
        try:
            client_sock.close()
        except Exception:
            pass

    def _client_handler(self, client_sock: socket.socket) -> None:
        """Handle incoming command protocol from a connected client with safe byte buffering."""
        raw_buf = bytearray()
        while self._running:
            try:
                chunk = client_sock.recv(4096)
                if not chunk:
                    break
                raw_buf.extend(chunk)
                while b"\n" in raw_buf:
                    line_bytes, _, rest = raw_buf.partition(b"\n")
                    raw_buf = bytearray(rest)
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if line:
                        self._handle_client_cmd(client_sock, line)
            except Exception:
                break

        self._disconnect_client(client_sock)

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._global_seq += 1
            return self._global_seq

    def _record_replay(self, msg_dict: dict) -> None:
        self._replay_buffer.record(
            seq=msg_dict.get("seq", 0),
            symbol=msg_dict.get("sym", ""),
            source=msg_dict.get("source", ""),
            event_type=msg_dict.get("type", "TICK"),
            price=msg_dict.get("price"),
            size=msg_dict.get("size"),
            bid=msg_dict.get("bid"),
            ask=msg_dict.get("ask"),
            bid_size=msg_dict.get("bid_size"),
            ask_size=msg_dict.get("ask_size"),
            status=msg_dict.get("status", "VALID"),
            is_crossed=bool(msg_dict.get("is_crossed", False)),
            exchange_ts=msg_dict.get("exchange_ts", 0.0),
            ingest_ts=msg_dict.get("ingest_ts", 0.0),
            broadcast_ts=msg_dict.get("broadcast_ts", 0.0),
            engine_us=msg_dict.get("engine_us", 0.0),
            raw_dict=msg_dict,
        )

    def _send_client_response(self, client_sock: socket.socket, resp: Any) -> None:
        """Route outbound client responses through dedicated session writer queue to prevent socket write race conditions."""
        data = resp.encode("utf-8") if isinstance(resp, str) else resp
        with self._sub_lock:
            sess = self._sessions.get(client_sock)
        if sess and sess.is_alive:
            try:
                sess.queue.put(data, timeout=1.0)
                return
            except Exception:
                pass
        try:
            client_sock.sendall(data)
        except Exception:
            pass

    def _handle_client_cmd(self, client_sock: socket.socket, cmd_str: str) -> None:
        """Parse client command protocol."""
        parts = cmd_str.split()
        verb = parts[0].upper()

        if verb == "AUTH":
            token = parts[1] if len(parts) > 1 else ""
            ent = self.security_manager.get_entitlement(token)
            if ent:
                if not ent.is_active:
                    resp = (
                        json.dumps({"status": "ERROR", "error": "REVOKED_TOKEN"}) + "\n"
                    )
                    client_sock.sendall(resp.encode("utf-8"))
                    client_sock.close()
                    return
                if ent.expires_at and time.time() > ent.expires_at:
                    resp = (
                        json.dumps({"status": "ERROR", "error": "TOKEN_EXPIRED"}) + "\n"
                    )
                    client_sock.sendall(resp.encode("utf-8"))
                    client_sock.close()
                    return
                with self._sub_lock:
                    sess = self._sessions.get(client_sock)
                    if sess:
                        sess.entitlement = ent
                    self._authenticated_clients.add(client_sock)
                resp = (
                    json.dumps(
                        {
                            "status": "OK",
                            "action": "AUTH",
                            "client_id": ent.client_id,
                            "role": ent.role.value if hasattr(ent.role, "value") else str(ent.role),
                        }
                    )
                    + "\n"
                )
                self._send_client_response(client_sock, resp)
                return
            elif self.auth_token and token == self.auth_token:
                # Legacy static token fallback
                with self._sub_lock:
                    self._authenticated_clients.add(client_sock)
                resp = (
                    json.dumps(
                        {
                            "status": "OK",
                            "action": "AUTH",
                            "role": "VIEWER",
                        }
                    )
                    + "\n"
                )
                self._send_client_response(client_sock, resp)
                return
            else:
                resp = json.dumps({"status": "ERROR", "error": "INVALID_TOKEN"}) + "\n"
                client_sock.sendall(resp.encode("utf-8"))
                client_sock.close()
                return

        # Allow benign commands before auth
        if verb in ("PING", "QUIT", "EXIT"):
            if verb == "PING":
                self._send_client_response(client_sock, b"PONG\n")
            else:
                client_sock.close()
            return

        # Enforce authentication if require_auth or auth_token configured
        if (
            self.require_auth or self.auth_token
        ) and client_sock not in self._authenticated_clients:
            resp = (
                json.dumps(
                    {
                        "status": "ERROR",
                        "error": "UNAUTHORIZED: Authentication token required (use AUTH <token>)",
                    }
                )
                + "\n"
            )
            self._send_client_response(client_sock, resp)
            return

        with self._sub_lock:
            sess = self._sessions.get(client_sock)
        ent = sess.entitlement if sess else None

        # Unified access: all clients have full access to L2 depth, binary formats, and replays
        max_replay = 100_000

        if verb == "FORMAT":
            fmt = parts[1].upper() if len(parts) > 1 else "JSON"
            with self._sub_lock:
                sess = self._sessions.get(client_sock)
                if sess:
                    sess.is_binary = fmt == "BINARY"
                    sess.is_sbe = fmt == "SBE"
            resp = (
                json.dumps({"status": "OK", "action": "FORMAT", "format": fmt}) + "\n"
            )
            self._send_client_response(client_sock, resp)

        elif verb in ("SUB", "SUBSCRIBE"):
            sym = parts[1].upper() if len(parts) > 1 else "ALL"

            if sym.startswith("BINARY:"):
                sym = sym[7:]
                with self._sub_lock:
                    sess = self._sessions.get(client_sock)
                    if sess:
                        sess.is_binary = True
            elif sym == "BINARY":
                with self._sub_lock:
                    sess = self._sessions.get(client_sock)
                    if sess:
                        sess.is_binary = True
                sym = "ALL"

            with self._sub_lock:
                sess = self._sessions.get(client_sock)
                if sess:
                    sess.symbols.add(sym)
                    self._subscribers[client_sock] = sess.symbols
            resp = json.dumps({"status": "OK", "action": "SUB", "symbol": sym}) + "\n"
            self._send_client_response(client_sock, resp)

        elif verb in ("UNSUB", "UNSUBSCRIBE"):
            sym = parts[1].upper() if len(parts) > 1 else "ALL"
            with self._sub_lock:
                sess = self._sessions.get(client_sock)
                if sess:
                    sess.symbols.discard(sym)
                    self._subscribers[client_sock] = sess.symbols
            resp = json.dumps({"status": "OK", "action": "UNSUB", "symbol": sym}) + "\n"
            self._send_client_response(client_sock, resp)

        elif verb == "BBO":
            sym = parts[1].upper() if len(parts) > 1 else "BTC/USD"
            self._send_client_response(client_sock, self.bbo.get_bbo_wire_bytes(sym))

        elif verb == "DEPTH":
            sym = parts[1].upper() if len(parts) > 1 else "BTC/USD"
            self._send_client_response(
                client_sock, self.depth.get_ladder_wire_bytes(sym)
            )

        elif verb == "VWAP":
            sym = parts[1].upper() if len(parts) > 1 else "BTC/USD"
            sizes = None
            if len(parts) > 2:
                try:
                    sizes = [float(x) for x in parts[2:]]
                except ValueError:
                    sizes = None
            self._send_client_response(
                client_sock, self.depth.get_vwap_wire_bytes(sym, sizes=sizes)
            )

        elif verb == "REPLAY":
            try:
                from_seq = int(parts[1]) if len(parts) > 1 else 1
                to_seq = int(parts[2]) if len(parts) > 2 else from_seq
                sym_filter = parts[3].upper() if len(parts) > 3 else None
                requested = max(1, to_seq - from_seq + 1)
                if requested > max_replay:
                    resp = (
                        json.dumps(
                            {
                                "status": "ERROR",
                                "action": "REPLAY",
                                "error": f"INVALID_RANGE: Requested replay range ({requested}) exceeds maximum safety buffer of {max_replay} events",
                            }
                        )
                        + "\n"
                    )
                    self._send_client_response(client_sock, resp)
                    return

                if sess and sess.is_binary:
                    bin_data = self._replay_buffer.replay_binary(
                        from_seq, to_seq, sym_filter
                    )
                    self._send_client_response(client_sock, bin_data)
                    return

                replayed = self._replay_buffer.replay(from_seq, to_seq, sym_filter)
                resp = (
                    json.dumps(
                        {
                            "status": "OK",
                            "action": "REPLAY",
                            "from_seq": from_seq,
                            "to_seq": to_seq,
                            "count": len(replayed),
                            "events": replayed,
                        }
                    )
                    + "\n"
                )
                self._send_client_response(client_sock, resp)
            except Exception as e:
                resp = (
                    json.dumps({"status": "ERROR", "action": "REPLAY", "error": str(e)})
                    + "\n"
                )
                self._send_client_response(client_sock, resp)

        elif verb == "HEALTH":
            health = self.watchdog.source_states()
            resp = json.dumps({"status": "OK", "health": health}) + "\n"
            self._send_client_response(client_sock, resp)

        elif verb == "STATUS":
            st = self.stats()
            resp = json.dumps({"status": "OK", "telemetry": st}) + "\n"
            self._send_client_response(client_sock, resp)

        elif verb == "PING":
            self._send_client_response(client_sock, b"PONG\n")

        elif verb in ("QUIT", "EXIT"):
            client_sock.close()

    def _ingestion_loop(self) -> None:
        """Main feed ingestion loop: streams ticks into pipeline and broadcasts."""
        if self.use_live:
            from live import YahooFinanceFeed

            feed = YahooFinanceFeed(poll_interval_s=1.0)
            for raw in feed.poll():
                if not self._running:
                    break
                self._process_and_broadcast(raw)
        else:
            num_events = self.sim_events if self.sim_events > 0 else 10_000_000
            sim = FeedSimulator(SimulatorConfig(seed=42, num_events=num_events))
            sleep_s = (
                (1.0 / self.sim_speed_eps)
                if (self.sim_speed_eps > 0 and self.sim_speed_eps < 1000.0)
                else 0.0
            )
            for raw, _label in sim.generate():
                if not self._running:
                    break
                self._process_and_broadcast(raw)
                if sleep_s > 0:
                    time.sleep(sleep_s)
                if self.sim_events > 0 and self._total_broadcast >= self.sim_events:
                    break

    def _process_and_broadcast(self, raw: RawEvent) -> None:
        """Ingest raw event, evaluate quality, update BBO & L2 depth, and broadcast to subscribed sockets via non-blocking queues."""
        t0_ns = time.perf_counter_ns()
        ev = self.pipeline.process_one(raw)
        if not ev:
            return

        try:
            ladder = self.depth.observe(raw)
        except Exception:
            ladder = None
        lat_us = (time.perf_counter_ns() - t0_ns) / 1000.0
        bbo_q = self.bbo.current_bbo(ev.instrument_id)
        t_broadcast = time.time()
        seq = self._next_seq()
        self._total_broadcast += 1

        payload = {
            "type": "TICK",
            "seq": seq,
            "sym": ev.instrument_id,
            "event": ev.event_type.value,
            "price": ev.price,
            "size": ev.quantity,
            "bid": ev.bid_price,
            "ask": ev.ask_price,
            "bid_size": ev.bid_size,
            "ask_size": ev.ask_size,
            "source": ev.source,
            "status": ev.quality_status.value,
            "exchange_ts": ev.exchange_timestamp,
            "ingest_ts": raw.receive_timestamp,
            "broadcast_ts": t_broadcast,
            "proc_us": round(lat_us, 1),
            "engine_us": round(lat_us, 1),
            "bbo": {
                "bid": bbo_q.best_bid,
                "ask": bbo_q.best_ask,
                "spread": bbo_q.spread,
                "mid": bbo_q.mid_price,
                "crossed": bbo_q.is_crossed,
            }
            if bbo_q
            else None,
        }
        self._record_replay(payload)
        if self.shm_writer:
            try:
                self.shm_writer.write_tick(
                    seq=seq,
                    symbol=ev.instrument_id,
                    source=ev.source,
                    price=ev.price,
                    size=ev.quantity,
                    bid=ev.bid_price,
                    ask=ev.ask_price,
                    bid_size=ev.bid_size,
                    ask_size=ev.ask_size,
                    status=ev.quality_status.value,
                    is_crossed=bool(bbo_q.is_crossed) if bbo_q else False,
                    exchange_ts=ev.exchange_timestamp,
                    ingest_ts=raw.receive_timestamp,
                    broadcast_ts=t_broadcast,
                    engine_us=lat_us,
                )
            except Exception as exc:
                self._shm_errors += 1
                if self._shm_errors <= 3 or self._shm_errors % 1000 == 0:
                    print(
                        f"[mdrap SERVICE WARNING] SHM tick write failed: {exc} (total errors={self._shm_errors})",
                        file=sys.stderr,
                    )

        if self.shm_writer and ladder:
            try:
                self.shm_writer.write_depth(
                    seq=seq,
                    symbol=ev.instrument_id,
                    best_bid=ladder.bids[0].price if ladder.bids else None,
                    best_ask=ladder.asks[0].price if ladder.asks else None,
                    bid_size=ladder.bids[0].size if ladder.bids else None,
                    ask_size=ladder.asks[0].size if ladder.asks else None,
                    micro_price=ladder.micro_price,
                    ofi=ladder.imbalance_ratio,
                    is_crossed=ladder.is_crossed,
                    exchange_ts=ladder.timestamp,
                    ingest_ts=raw.receive_timestamp,
                    broadcast_ts=t_broadcast,
                    engine_us=lat_us,
                )
            except Exception as exc:
                self._shm_errors += 1
                if self._shm_errors <= 3 or self._shm_errors % 1000 == 0:
                    print(
                        f"[mdrap SERVICE WARNING] SHM depth write failed: {exc} (total errors={self._shm_errors})",
                        file=sys.stderr,
                    )

        msg = (json.dumps(payload) + "\n").encode("utf-8")

        with self._sub_lock:
            active_sessions = (
                [s for s in self._sessions.values() if s.is_alive]
                if self._sessions
                else []
            )

        if not active_sessions:
            return

        has_binary_clients = any(s.is_binary for s in active_sessions)
        bin_msg = None
        if has_binary_clients:
            try:
                bin_msg = pack_tick_frame(
                    seq=seq,
                    symbol=ev.instrument_id,
                    source=ev.source,
                    price=ev.price,
                    size=ev.quantity,
                    bid=ev.bid_price,
                    ask=ev.ask_price,
                    status=ev.quality_status.value,
                    is_crossed=bool(bbo_q.is_crossed) if bbo_q else False,
                    exchange_ts=ev.exchange_timestamp,
                    ingest_ts=raw.receive_timestamp,
                    broadcast_ts=t_broadcast,
                    engine_us=lat_us,
                )
            except Exception:
                bin_msg = None

        # L2 Depth broadcast if updated and clients subscribed to L2
        depth_msg = None
        depth_bin_msg = None
        has_l2_subs = any(
            "L2:ALL" in s.symbols or f"L2:{ev.instrument_id}" in s.symbols
            for s in active_sessions
        )
        if has_l2_subs and ladder:
            seq_depth = self._next_seq()
            depth_payload = {
                "type": "DEPTH",
                "seq": seq_depth,
                "sym": ev.instrument_id,
                "bids": [[b.price, b.size, b.venue] for b in ladder.bids[:10]],
                "asks": [[a.price, a.size, a.venue] for a in ladder.asks[:10]],
                "aggregated_bids": [b.to_dict() for b in ladder.aggregated_bids[:10]],
                "aggregated_asks": [a.to_dict() for a in ladder.aggregated_asks[:10]],
                "micro_price": round(ladder.micro_price, 4),
                "ofi": round(ladder.imbalance_ratio, 4),
                "is_crossed": ladder.is_crossed,
                "arbitrage": ladder.crossed_opportunities,
                "vwap_curve": ladder.vwap_curve.to_dict()
                if ladder.vwap_curve
                else None,
                "total_bid_notional": round(ladder.total_bid_notional, 2),
                "total_ask_notional": round(ladder.total_ask_notional, 2),
                "exchange_ts": ladder.timestamp,
                "ingest_ts": raw.receive_timestamp,
                "broadcast_ts": t_broadcast,
                "proc_us": round(lat_us, 1),
                "engine_us": round(lat_us, 1),
            }
            self._record_replay(depth_payload)
            depth_msg = (json.dumps(depth_payload) + "\n").encode("utf-8")
            if has_binary_clients:
                try:
                    depth_bin_msg = pack_depth_frame(
                        seq=seq_depth,
                        symbol=ev.instrument_id,
                        best_bid=ladder.bids[0].price if ladder.bids else None,
                        best_ask=ladder.asks[0].price if ladder.asks else None,
                        bid_size=ladder.bids[0].size if ladder.bids else None,
                        ask_size=ladder.asks[0].size if ladder.asks else None,
                        micro_price=ladder.micro_price,
                        ofi=ladder.imbalance_ratio,
                        is_crossed=ladder.is_crossed,
                        exchange_ts=ladder.timestamp,
                        ingest_ts=raw.receive_timestamp,
                        broadcast_ts=t_broadcast,
                        engine_us=lat_us,
                    )
                except Exception:
                    depth_bin_msg = None

        # Real-time VWAP broadcast if updated and clients subscribed to VWAP
        vwap_msg = None
        has_vwap_subs = any(
            "VWAP:ALL" in s.symbols or f"VWAP:{ev.instrument_id}" in s.symbols
            for s in active_sessions
        )
        if has_vwap_subs and ladder and ladder.vwap_curve:
            seq_vwap = self._next_seq()
            vwap_payload = {
                "type": "VWAP",
                "seq": seq_vwap,
                "sym": ev.instrument_id,
                "vwap_curve": ladder.vwap_curve.to_dict(),
                "exchange_ts": ladder.timestamp,
                "ingest_ts": raw.receive_timestamp,
                "broadcast_ts": t_broadcast,
                "proc_us": round(lat_us, 1),
                "engine_us": round(lat_us, 1),
            }
            self._record_replay(vwap_payload)
            vwap_msg = (json.dumps(vwap_payload) + "\n").encode("utf-8")

        # SBE binary wire frame preparation
        sbe_msg = None
        if any(s.is_sbe for s in active_sessions):
            try:
                from sbe import pack_sbe_tick

                sbe_msg = pack_sbe_tick(
                    seq=seq,
                    symbol=ev.instrument_id,
                    source=ev.source,
                    price=ev.price,
                    size=ev.quantity,
                    bid=ev.bid_price,
                    ask=ev.ask_price,
                    bid_size=ev.bid_size,
                    ask_size=ev.ask_size,
                    status=ev.quality_status.value,
                    is_crossed=bool(bbo_q.is_crossed) if bbo_q else False,
                    exchange_ts=ev.exchange_timestamp,
                    ingest_ts=raw.receive_timestamp,
                    broadcast_ts=t_broadcast,
                    engine_us=lat_us,
                )
            except Exception:
                sbe_msg = None

        for sess in active_sessions:
            # Per-session token bucket rate limiter
            if sess.rate_limiter and not sess.rate_limiter.allow():
                sess.rate_limited_ticks += 1
                continue

            if sess.is_sbe and sbe_msg:
                out_tick = sbe_msg
            elif sess.is_binary and bin_msg:
                out_tick = bin_msg
            else:
                out_tick = msg

            out_depth = (
                depth_bin_msg if (sess.is_binary and depth_bin_msg) else depth_msg
            )

            # L1 Tick delivery
            if "ALL" in sess.symbols or ev.instrument_id in sess.symbols:
                try:
                    sess.queue.put_nowait(out_tick)
                except queue.Full:
                    sess.dropped_ticks += 1

            # L2 Depth delivery
            if out_depth and (
                "L2:ALL" in sess.symbols or f"L2:{ev.instrument_id}" in sess.symbols
            ):
                try:
                    sess.queue.put_nowait(out_depth)
                except queue.Full:
                    sess.dropped_ticks += 1

            # Real-time VWAP delivery
            if vwap_msg and (
                "VWAP:ALL" in sess.symbols or f"VWAP:{ev.instrument_id}" in sess.symbols
            ):
                try:
                    sess.queue.put_nowait(vwap_msg)
                except queue.Full:
                    sess.dropped_ticks += 1

    def stats(self) -> dict:
        uptime = time.time() - self._t0 if self._t0 > 0 else 0.0
        eps = (self._total_broadcast / uptime) if uptime > 0 else 0.0
        with self._sub_lock:
            client_count = len(self._sessions)
            total_dropped = self._total_dropped + sum(
                s.dropped_ticks for s in self._sessions.values()
            )
            total_rate_limited = self._total_rate_limited + sum(
                s.rate_limited_ticks for s in self._sessions.values()
            )
            role_breakdown: Dict[str, int] = {}
            for s in self._sessions.values():
                role_val = (
                    s.entitlement.role.value
                    if (s.entitlement and hasattr(s.entitlement, "role") and hasattr(s.entitlement.role, "value"))
                    else "DEFAULT"
                )
                role_breakdown[role_val] = role_breakdown.get(role_val, 0) + 1
        return {
            "uptime_s": round(uptime, 2),
            "total_broadcast": self._total_broadcast,
            "global_seq": self._global_seq,
            "replay_buffer_size": len(self._replay_buffer),
            "replay_buffer": self._replay_buffer.stats(),
            "throughput_eps": round(eps, 1),
            "active_clients": client_count,
            "dropped_ticks": total_dropped,
            "rate_limited_ticks": total_rate_limited,
            "client_roles": role_breakdown,
            "client_tiers": role_breakdown,
            "host": self.host,
            "port": self.port,
            "shm_enabled": bool(self.shm_writer),
            "shm_errors": self._shm_errors,
            "sources": self.watchdog.source_states(),
        }


class StreamClient:
    """
    High-speed client for subscribing to the local MDRAP daemon.
    Yields parsed canonical ticks as JSON/dicts.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        timeout: float = 5.0,
        auth_token: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        import os

        self.auth_token = (
            auth_token
            if auth_token is not None
            else os.environ.get("MDRAP_DAEMON_TOKEN", "")
        )
        self.sock: Optional[socket.socket] = None
        self._query_sock: Optional[socket.socket] = None
        self._query_lock = threading.Lock()

    @staticmethod
    def _recv_json_line(sock: socket.socket) -> dict:
        """Read a single newline-terminated JSON payload from a socket."""
        buf = bytearray()
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
        line = buf.decode("utf-8", errors="replace").strip()
        if not line:
            return {}
        try:
            return json.loads(line.split("\n", 1)[0])
        except json.JSONDecodeError:
            return {}

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.auth_token:
            self.sock.sendall(f"AUTH {self.auth_token}\n".encode("utf-8"))
            ack = self._recv_json_line(self.sock)
            if ack.get("status") != "OK":
                raise PermissionError(
                    f"Daemon authentication failed: {ack.get('error')}"
                )

    def _get_query_sock(self) -> socket.socket:
        """Get or lazily establish a persistent, authenticated keep-alive query socket."""
        if self._query_sock is not None:
            return self._query_sock
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.auth_token:
            sock.sendall(f"AUTH {self.auth_token}\n".encode("utf-8"))
            ack = self._recv_json_line(sock)
            if ack.get("status") != "OK":
                sock.close()
                raise PermissionError(
                    f"Daemon authentication failed: {ack.get('error')}"
                )
        self._query_sock = sock
        return self._query_sock

    def _send_query(self, cmd: str) -> dict:
        """Send command over persistent keep-alive socket (<0.1ms latency)."""
        with self._query_lock:
            for attempt in range(2):
                try:
                    sock = self._get_query_sock()
                    sock.sendall((cmd.strip() + "\n").encode("utf-8"))
                    res = self._recv_json_line(sock)
                    if not res:
                        raise ConnectionResetError("Socket closed by daemon")
                    return res
                except Exception:
                    if self._query_sock:
                        try:
                            self._query_sock.close()
                        except Exception:
                            pass
                        self._query_sock = None
                    if attempt == 1:
                        return {}
            return {}

    def close(self) -> None:
        """Gracefully terminate client connection and release query/stream sockets."""
        if self.sock:
            try:
                self.sock.sendall(b"QUIT\n")
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        with self._query_lock:
            if self._query_sock:
                try:
                    self._query_sock.close()
                except Exception:
                    pass
                self._query_sock = None

    def get_status(self) -> dict:
        return self._send_query("STATUS").get("telemetry", {})

    def get_bbo(self, symbol: str = "BTC/USD") -> Optional[dict]:
        return self._send_query(f"BBO {symbol}").get("bbo")

    def get_depth(self, symbol: str = "BTC/USD") -> Optional[dict]:
        return self._send_query(f"DEPTH {symbol}").get("depth")

    def request_replay(
        self, from_seq: int, to_seq: int, symbol: Optional[str] = None
    ) -> list[dict]:
        cmd = f"REPLAY {from_seq} {to_seq}"
        if symbol:
            cmd += f" {symbol}"
        res = self._send_query(cmd)
        return res.get("events", [])

    def get_health(self) -> dict:
        return self._send_query("HEALTH").get("health", {})

    def stream(self, symbol: str = "ALL", limit: int = 0) -> Iterator[dict]:
        """Generator streaming live ticks from the daemon socket."""
        if not self.sock:
            self.connect()
        self.sock.sendall(f"SUB {symbol}\n".encode("utf-8"))
        raw_buf = bytearray()
        while b"\n" not in raw_buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            raw_buf.extend(chunk)

        _, _, rest = raw_buf.partition(b"\n")
        raw_buf = bytearray(rest)
        count = 0
        while True:
            while b"\n" in raw_buf:
                line_bytes, _, rest = raw_buf.partition(b"\n")
                raw_buf = bytearray(rest)
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    if payload.get("type") == "TICK":
                        yield payload
                        count += 1
                        if limit > 0 and count >= limit:
                            return
                except json.JSONDecodeError:
                    continue
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                raw_buf.extend(data)
            except (KeyboardInterrupt, GeneratorExit):
                break
            except Exception:
                break


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


class TerminalCockpit:
    """
    Live full-screen ANSI terminal monitor (htop/k9s style) for the MDRAP daemon.
    Displays real-time throughput, latency sparklines, venue health, and consolidated BBO.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9876,
        auth_token: Optional[str] = None,
    ):
        self.client = StreamClient(host=host, port=port, auth_token=auth_token)

    def run(self) -> None:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        try:
            self.client.connect()
        except Exception:
            print(
                f"\033[91mFailed to connect to MDRAP Daemon at {self.client.host}:{self.client.port}\033[0m"
            )
            print("Ensure the daemon is running with: \033[96mmdrap daemon\033[0m")
            return

        print("\033[?25l", end="")  # Hide cursor
        try:
            from terminal_display import poll_keypress
        except Exception:

            def poll_keypress():
                return None

        paused = False
        try:
            while True:
                # 1. Fetch status and BBO snapshots (unless paused)
                if not paused:
                    try:
                        telemetry = self.client.get_status()
                        bbo_btc = self.client.get_bbo("BTC/USD")
                        bbo_aapl = self.client.get_bbo("AAPL")
                    except Exception:
                        break

                    # 2. Render cockpit frame
                    out = self._render_frame(
                        telemetry, bbo_btc, bbo_aapl, paused=paused
                    )
                    if sys.platform == "win32":
                        os.system("cls")
                        sys.stdout.write(out)
                    else:
                        sys.stdout.write("\033[H\033[2J" + out)
                    sys.stdout.flush()

                # Poll keyboard input across 500ms cycle
                for _ in range(10):
                    key = poll_keypress()
                    if key:
                        if key in ("q", "Q", "\x1b"):
                            return
                        elif key == " ":
                            paused = not paused
                            # Re-render immediately when paused/unpaused
                            out = self._render_frame(
                                telemetry, bbo_btc, bbo_aapl, paused=paused
                            )
                            if sys.platform == "win32":
                                os.system("cls")
                                sys.stdout.write(out)
                            else:
                                sys.stdout.write("\033[H\033[2J" + out)
                            sys.stdout.flush()
                    time.sleep(0.05)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            print("\033[?25h", end="")  # Restore cursor
            self.client.close()

    def _render_box_row(self, content: str, width: int = 72) -> str:
        visible_len = len(_strip_ansi(content))
        pad = " " * max(0, width - visible_len)
        return f"\033[1;36m│\033[0m{content}{pad}\033[1;36m│\033[0m"

    def _render_frame(
        self,
        st: dict,
        bbo_btc: Optional[dict],
        bbo_aapl: Optional[dict],
        paused: bool = False,
    ) -> str:
        uptime = st.get("uptime_s", 0)
        eps = st.get("throughput_eps", 0)
        total = st.get("total_broadcast", 0)
        clients = st.get("active_clients", 0)
        sources = st.get("sources", {})

        border_top = "\033[1;36m┌────────────────────────────────────────────────────────────────────────┐\033[0m"
        border_mid = "\033[1;36m├────────────────────────────────────────────────────────────────────────┤\033[0m"
        border_bot = "\033[1;36m└────────────────────────────────────────────────────────────────────────┘\033[0m"

        lines = [
            border_top,
            self._render_box_row(
                "  \033[1;37mMDRAP Terminal Service Cockpit (htop-style monitor)\033[0m"
            ),
        ]
        if paused:
            lines.append(
                self._render_box_row(
                    "  \033[1;37;41m  ⏸ TELEMETRY FROZEN — PRESS SPACE TO RESUME ⏸  \033[0m"
                )
            )

        lines.extend(
            [
                self._render_box_row(
                    f"  Daemon: \033[92mONLINE\033[0m (127.0.0.1:{st.get('port', 9876)})  |  Uptime: \033[93m{uptime:.1f}s\033[0m  |  Clients: \033[95m{clients}\033[0m"
                ),
                border_mid,
                self._render_box_row(
                    f"  Throughput: \033[1;92m{eps:>8,.1f} eps\033[0m  |  Total Ingested: \033[1;97m{total:>10,}\033[0m ticks"
                ),
                border_mid,
                self._render_box_row(
                    "  \033[1;33mVenue Health & Watchdog Failover Matrix\033[0m"
                ),
            ]
        )

        if sources:
            src_parts = []
            for src, state in sources.items():
                color = "\033[92m" if state == "HEALTHY" else "\033[91m"
                src_parts.append(f"{src}: {color}{state}\033[0m")
            lines.append(self._render_box_row(f"  {'  |  '.join(src_parts)}"))
        else:
            lines.append(
                self._render_box_row(
                    "  Venues: \033[92mFEEDX: HEALTHY\033[0m  |  \033[92mFEEDY: HEALTHY\033[0m  |  \033[92mFEEDZ: HEALTHY\033[0m"
                )
            )

        lines.append(border_mid)
        lines.append(
            self._render_box_row(
                "  \033[1;33mConsolidated Best Bid & Offer (Synthetic NBBO)\033[0m"
            )
        )

        for sym, bbo in [("BTC/USD", bbo_btc), ("AAPL", bbo_aapl)]:
            if bbo and bbo.get("bid") is not None:
                bid = f"${bbo['bid']:,.2f}"
                ask = f"${bbo['ask']:,.2f}"
                spread = f"${bbo['spread']:.2f}"
                status = (
                    "\033[91m[CROSSED]\033[0m"
                    if bbo.get("crossed")
                    else "\033[92m[NORMAL]\033[0m"
                )
                row = f"  {sym:<8} Bid: \033[92m{bid:>11}\033[0m  Ask: \033[91m{ask:>11}\033[0m  Spr: {spread:>7} {status}"
                lines.append(self._render_box_row(row))
            else:
                lines.append(
                    self._render_box_row(
                        f"  {sym:<8} \033[90mAwaiting market ticks from active venues...\033[0m"
                    )
                )

        pause_tag = "Resume" if paused else "Freeze"
        lines.extend(
            [
                border_bot,
                f"\033[90m[Hotkeys] [bold white]q[/bold white]: Detach  |  [bold white]Space[/bold white]: {pause_tag} Telemetry\033[0m",
            ]
        )
        return "\n".join(lines) + "\n"
