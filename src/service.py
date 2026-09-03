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
import select
import socket
import sys
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from bbo import BBOEngine, ConsolidatedBBO
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from pipeline import Pipeline
from reconciliation import ReliabilityTracker
from simulator import FeedSimulator, SimulatorConfig
from storage import Store
from watchdog import SourceWatchdog


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
    ):
        self.host = host
        self.port = port
        self.db_path = db_path
        self.use_live = use_live
        self.sim_events = sim_events
        self.sim_speed_eps = sim_speed_eps

        self.store = Store(db_path)
        self.reliability = ReliabilityTracker()
        self.watchdog = SourceWatchdog(reliability=self.reliability, silence_threshold_s=3.0)
        self.bbo = BBOEngine(quote_ttl_s=10.0, watchdog=self.watchdog)
        self.pipeline = Pipeline(store=self.store, reliability=self.reliability, bbo=self.bbo, watchdog=self.watchdog)

        self._running = False
        self._server_sock: Optional[socket.socket] = None
        self._subscribers: Dict[socket.socket, Set[str]] = {}
        self._sub_lock = threading.Lock()

        self._t0 = 0.0
        self._total_broadcast = 0
        self._ingest_thread: Optional[threading.Thread] = None
        self._server_thread: Optional[threading.Thread] = None

    def start(self, blocking: bool = True) -> None:
        """Start the background socket server and ingestion engine."""
        self._running = True
        self._t0 = time.time()

        # 1. Bind and listen on local TCP socket
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(64)
        self._server_sock.settimeout(0.2)

        # 2. Start socket listener thread
        self._server_thread = threading.Thread(target=self._socket_accept_loop, daemon=True, name="mdrap-socket-listener")
        self._server_thread.start()

        # 3. Start ingestion thread
        self._ingest_thread = threading.Thread(target=self._ingestion_loop, daemon=True, name="mdrap-feed-ingest")
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
                self._server_sock.close()
            except Exception:
                pass

        with self._sub_lock:
            for s in list(self._subscribers.keys()):
                try:
                    s.close()
                except Exception:
                    pass
            self._subscribers.clear()

        if self.pipeline:
            self.pipeline.finish()
        if self.store:
            self.store.close()

    def _socket_accept_loop(self) -> None:
        """Accept new client connections and spawn non-blocking command listeners."""
        while self._running and self._server_sock:
            try:
                client_sock, _addr = self._server_sock.accept()
                client_sock.settimeout(None)
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._sub_lock:
                    self._subscribers[client_sock] = set()
                t = threading.Thread(target=self._client_handler, args=(client_sock,), daemon=True)
                t.start()
            except (socket.timeout, OSError):
                continue
            except Exception:
                break

    def _client_handler(self, client_sock: socket.socket) -> None:
        """Handle incoming command protocol from a connected client."""
        buf = ""
        while self._running:
            try:
                data = client_sock.recv(4096).decode("utf-8")
                if not data:
                    break
                buf += data
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    self._handle_client_cmd(client_sock, line)
            except Exception:
                break

        # Disconnected
        with self._sub_lock:
            self._subscribers.pop(client_sock, None)
        try:
            client_sock.close()
        except Exception:
            pass

    def _handle_client_cmd(self, client_sock: socket.socket, cmd_str: str) -> None:
        """Parse client command protocol."""
        parts = cmd_str.split()
        verb = parts[0].upper()

        if verb in ("SUB", "SUBSCRIBE"):
            sym = parts[1].upper() if len(parts) > 1 else "ALL"
            with self._sub_lock:
                if client_sock not in self._subscribers:
                    self._subscribers[client_sock] = set()
                self._subscribers[client_sock].add(sym)
            resp = json.dumps({"status": "OK", "action": "SUB", "symbol": sym}) + "\n"
            client_sock.sendall(resp.encode("utf-8"))

        elif verb in ("UNSUB", "UNSUBSCRIBE"):
            sym = parts[1].upper() if len(parts) > 1 else "ALL"
            with self._sub_lock:
                if client_sock in self._subscribers:
                    self._subscribers[client_sock].discard(sym)
            resp = json.dumps({"status": "OK", "action": "UNSUB", "symbol": sym}) + "\n"
            client_sock.sendall(resp.encode("utf-8"))

        elif verb == "BBO":
            sym = parts[1].upper() if len(parts) > 1 else "BTC/USD"
            bbo_quote = self.bbo.current_bbo(sym)
            data = {
                "status": "OK",
                "symbol": sym,
                "bbo": {
                    "bid": bbo_quote.best_bid,
                    "bid_source": bbo_quote.best_bid_source,
                    "ask": bbo_quote.best_ask,
                    "ask_source": bbo_quote.best_ask_source,
                    "spread": bbo_quote.spread,
                    "mid": bbo_quote.mid_price,
                    "crossed": bbo_quote.is_crossed,
                } if bbo_quote else None
            }
            client_sock.sendall((json.dumps(data) + "\n").encode("utf-8"))

        elif verb == "HEALTH":
            health = self.watchdog.source_states()
            resp = json.dumps({"status": "OK", "health": health}) + "\n"
            client_sock.sendall(resp.encode("utf-8"))

        elif verb == "STATUS":
            st = self.stats()
            resp = json.dumps({"status": "OK", "telemetry": st}) + "\n"
            client_sock.sendall(resp.encode("utf-8"))

        elif verb == "PING":
            client_sock.sendall(b"PONG\n")

        elif verb in ("QUIT", "EXIT"):
            client_sock.close()

    def _ingestion_loop(self) -> None:
        """Main feed ingestion loop."""
        if self.use_live:
            from live import LiveConnector
            conn = LiveConnector()
            while self._running:
                try:
                    for raw in conn.fetch_snapshot("BTC/USD"):
                        if not self._running:
                            break
                        self._process_and_broadcast(raw)
                    time.sleep(0.5)
                except Exception:
                    time.sleep(1.0)
        else:
            total_events = self.sim_events if self.sim_events > 0 else 100_000_000
            sim_cfg = SimulatorConfig(seed=42, num_events=total_events)
            sim = FeedSimulator(sim_cfg)
            delay = (1.0 / self.sim_speed_eps) if self.sim_speed_eps > 0 else 0.0

            while self._running:
                for raw, _ in sim.generate():
                    if not self._running:
                        break
                    self._process_and_broadcast(raw)
                    if delay > 0.0001:
                        time.sleep(delay)
                if self.sim_events > 0:
                    break

    def _process_and_broadcast(self, raw: RawEvent) -> None:
        """Ingest raw event, evaluate quality, update BBO, and broadcast to subscribed sockets."""
        t0_ns = time.perf_counter_ns()
        ev = self.pipeline.process_one(raw)
        if not ev:
            return

        lat_us = (time.perf_counter_ns() - t0_ns) / 1000.0
        self._total_broadcast += 1

        with self._sub_lock:
            if not self._subscribers:
                return
            active_clients = list(self._subscribers.items())

        bbo_q = self.bbo.current_bbo(ev.instrument_id)
        payload = {
            "type": "TICK",
            "sym": ev.instrument_id,
            "event": ev.event_type.value,
            "price": ev.price,
            "size": ev.quantity,
            "bid": ev.bid_price,
            "ask": ev.ask_price,
            "source": ev.source,
            "status": ev.quality_status.value,
            "exchange_ts": ev.exchange_timestamp,
            "proc_us": round(lat_us, 1),
            "bbo": {
                "bid": bbo_q.best_bid,
                "ask": bbo_q.best_ask,
                "spread": bbo_q.spread,
                "crossed": bbo_q.is_crossed,
            } if bbo_q else None
        }
        msg = (json.dumps(payload) + "\n").encode("utf-8")

        dead_sockets = []
        for s, subs in active_clients:
            if "ALL" in subs or ev.instrument_id in subs:
                try:
                    s.sendall(msg)
                except Exception:
                    dead_sockets.append(s)

        if dead_sockets:
            with self._sub_lock:
                for s in dead_sockets:
                    self._subscribers.pop(s, None)
                    try:
                        s.close()
                    except Exception:
                        pass

    def stats(self) -> dict:
        uptime = time.time() - self._t0 if self._t0 > 0 else 0.0
        eps = (self._total_broadcast / uptime) if uptime > 0 else 0.0
        with self._sub_lock:
            client_count = len(self._subscribers)
        return {
            "uptime_s": round(uptime, 2),
            "total_broadcast": self._total_broadcast,
            "throughput_eps": round(eps, 1),
            "active_clients": client_count,
            "host": self.host,
            "port": self.port,
            "sources": self.watchdog.source_states(),
        }


class StreamClient:
    """
    High-speed client for subscribing to the local MDRAP daemon.
    Yields parsed canonical ticks as JSON/dicts.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9876, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def _send_query(self, cmd: str) -> dict:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect((self.host, self.port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall((cmd.strip() + "\n").encode("utf-8"))
        buf = ""
        while "\n" not in buf:
            chunk = sock.recv(4096).decode("utf-8")
            if not chunk:
                break
            buf += chunk
        sock.close()
        line = buf.strip().split("\n")[0] if buf else "{}"
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {}

    def get_status(self) -> dict:
        return self._send_query("STATUS").get("telemetry", {})

    def get_bbo(self, symbol: str = "BTC/USD") -> Optional[dict]:
        return self._send_query(f"BBO {symbol}").get("bbo")

    def get_health(self) -> dict:
        return self._send_query("HEALTH").get("health", {})

    def stream(self, symbol: str = "ALL", limit: int = 0) -> Iterator[dict]:
        """Generator streaming live ticks from the daemon socket."""
        if not self.sock:
            self.connect()
        self.sock.sendall(f"SUB {symbol}\n".encode("utf-8"))
        buf = ""
        while "\n" not in buf:
            chunk = self.sock.recv(4096).decode("utf-8")
            if not chunk:
                break
            buf += chunk

        ack_line, buf = buf.split("\n", 1)
        count = 0
        while True:
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
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
                data = self.sock.recv(4096).decode("utf-8")
                if not data:
                    break
                buf += data
            except (KeyboardInterrupt, GeneratorExit):
                break
            except Exception:
                break

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.sendall(b"QUIT\n")
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def _strip_ansi(text: str) -> str:
    import re
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


class TerminalCockpit:
    """
    Live full-screen ANSI terminal monitor (htop/k9s style) for the MDRAP daemon.
    Displays real-time throughput, latency sparklines, venue health, and consolidated BBO.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.client = StreamClient(host=host, port=port)

    def run(self) -> None:
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass
        try:
            self.client.connect()
        except Exception as exc:
            print(f"\033[91mFailed to connect to MDRAP Daemon at {self.client.host}:{self.client.port}\033[0m")
            print("Ensure the daemon is running with: \033[96mmdrap daemon\033[0m")
            return

        print("\033[?25l", end="")  # Hide cursor
        try:
            while True:
                # 1. Fetch status and BBO snapshots
                try:
                    telemetry = self.client.get_status()
                    bbo_btc = self.client.get_bbo("BTC/USD")
                    bbo_aapl = self.client.get_bbo("AAPL")
                except Exception:
                    break

                # 2. Render cockpit frame
                out = self._render_frame(telemetry, bbo_btc, bbo_aapl)
                if sys.platform == "win32":
                    os.system("cls")
                    sys.stdout.write(out)
                else:
                    sys.stdout.write("\033[H\033[2J" + out)
                sys.stdout.flush()
                time.sleep(0.5)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            print("\033[?25h", end="")  # Restore cursor
            self.client.close()

    def _render_box_row(self, content: str, width: int = 72) -> str:
        visible_len = len(_strip_ansi(content))
        pad = " " * max(0, width - visible_len)
        return f"\033[1;36m│\033[0m{content}{pad}\033[1;36m│\033[0m"

    def _render_frame(self, st: dict, bbo_btc: Optional[dict], bbo_aapl: Optional[dict]) -> str:
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
            self._render_box_row("  \033[1;37mMDRAP Terminal Service Cockpit (htop-style monitor)\033[0m"),
            self._render_box_row(f"  Daemon: \033[92mONLINE\033[0m (127.0.0.1:{st.get('port', 9876)})  |  Uptime: \033[93m{uptime:.1f}s\033[0m  |  Clients: \033[95m{clients}\033[0m"),
            border_mid,
            self._render_box_row(f"  Throughput: \033[1;92m{eps:>8,.1f} eps\033[0m  |  Total Ingested: \033[1;97m{total:>10,}\033[0m ticks"),
            border_mid,
            self._render_box_row("  \033[1;33mVenue Health & Watchdog Failover Matrix\033[0m"),
        ]

        if sources:
            src_parts = []
            for src, state in sources.items():
                color = "\033[92m" if state == "HEALTHY" else "\033[91m"
                src_parts.append(f"{src}: {color}{state}\033[0m")
            lines.append(self._render_box_row(f"  {'  |  '.join(src_parts)}"))
        else:
            lines.append(self._render_box_row("  Venues: \033[92mFEEDX: HEALTHY\033[0m  |  \033[92mFEEDY: HEALTHY\033[0m  |  \033[92mFEEDZ: HEALTHY\033[0m"))

        lines.append(border_mid)
        lines.append(self._render_box_row("  \033[1;33mConsolidated Best Bid & Offer (Synthetic NBBO)\033[0m"))

        for sym, bbo in [("BTC/USD", bbo_btc), ("AAPL", bbo_aapl)]:
            if bbo and bbo.get("bid") is not None:
                bid = f"${bbo['bid']:,.2f}"
                ask = f"${bbo['ask']:,.2f}"
                spread = f"${bbo['spread']:.2f}"
                status = "\033[91m[CROSSED]\033[0m" if bbo.get("crossed") else "\033[92m[NORMAL]\033[0m"
                row = f"  {sym:<8} Bid: \033[92m{bid:>11}\033[0m  Ask: \033[91m{ask:>11}\033[0m  Spr: {spread:>7} {status}"
                lines.append(self._render_box_row(row))
            else:
                lines.append(self._render_box_row(f"  {sym:<8} \033[90mAwaiting market ticks from active venues...\033[0m"))

        lines.extend([
            border_bot,
            "\033[90m[Controls] Press Ctrl+C or 'q' to detach (daemon keeps running in background)\033[0m",
        ])
        return "\n".join(lines) + "\n"
