"""MDRAP AsyncIO Raw TCP Gateway (§27).

Provides high-throughput, low-latency push streaming to external quant clients.
Uses raw TCP sockets with newline-delimited framing instead of WebSockets/HTTP,
delivering institutional-grade, zero-overhead connectivity (similar to ITCH/OUCH).

Features:
  - Bounded write buffering with backpressure timeout (`asyncio.wait_for(drain, 0.05)`).
  - Dead client eviction on network drops or slow consumers.
  - Heartbeat ping/pong frame negotiation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

__stability__ = "beta"

logger = logging.getLogger("mdrap.gateway")


class TCPGatewayServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9000,
        security_manager: Any = None,
        ssl_context: Any = None,
        require_auth: bool | None = None,
        require_tls: bool | None = None,
    ):
        self.host = host
        self.port = port
        self.security_manager = security_manager
        self.ssl_context = ssl_context
        if require_auth is not None:
            self.require_auth = require_auth
        else:
            env_auth = os.environ.get("MDRAP_REQUIRE_AUTH")
            if env_auth is not None:
                self.require_auth = env_auth.lower() in ("1", "true", "yes")
            else:
                self.require_auth = security_manager is not None

        if self.require_auth and self.security_manager is None:
            raise ValueError("require_auth=True requires a valid security_manager")

        if require_tls is not None:
            self.require_tls = require_tls
        else:
            self.require_tls = os.environ.get("MDRAP_REQUIRE_TLS", "0").lower() in (
                "1",
                "true",
                "yes",
            )

        if self.require_tls and self.ssl_context is None:
            raise ValueError("require_tls=True requires a valid ssl_context")

        self.clients: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None
        self._stats = {"sent": 0, "dropped": 0, "connected": 0, "auth_failures": 0}
        self.running = False
        self.client_queue_size: int = int(
            os.environ.get("MDRAP_TCP_QUEUE_SIZE", "1024")
        )
        self.max_buffer_size: int = int(os.environ.get("MDRAP_TCP_MAX_BUFFER", "65536"))
        self._client_queues: dict[asyncio.StreamWriter, asyncio.Queue] = {}
        self._client_tasks: dict[asyncio.StreamWriter, asyncio.Task] = {}

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        # Optional TLS / Auth gate
        if self.require_auth:
            if self.security_manager is None:
                logger.error(
                    "Auth required but security manager is None; rejecting connection"
                )
                writer.close()
                await writer.wait_closed()
                return
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line:
                    writer.close()
                    await writer.wait_closed()
                    return
                auth_msg = json.loads(line.decode("utf-8").strip())
                token = auth_msg.get("token") or auth_msg.get("api_key")
                ent = self.security_manager.get_entitlement(token, active_only=True)
                if not ent:
                    self._stats["auth_failures"] += 1
                    err_msg = (
                        json.dumps(
                            {
                                "type": "error",
                                "message": "Unauthorized: invalid or inactive token",
                            }
                        )
                        + "\n"
                    )
                    writer.write(err_msg.encode("utf-8"))
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
            except Exception as exc:
                self._stats["auth_failures"] += 1
                logger.debug("[gateway_tcp] Auth failure on client connect: %s", exc)
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception as close_exc:
                    logger.debug(
                        "[gateway_tcp] Failed closing rejected client: %s", close_exc
                    )
                return

        q: asyncio.Queue = asyncio.Queue(maxsize=self.client_queue_size)
        self.clients.add(writer)
        self._client_queues[writer] = q
        writer_task = asyncio.create_task(self._client_writer_loop(writer, q))
        self._client_tasks[writer] = writer_task
        self._stats["connected"] = len(self.clients)

        try:
            # Send welcome protocol message
            welcome = {
                "type": "system",
                "message": "MDRAP TCP Gateway v1.0",
                "status": "connected",
            }
            writer.write((json.dumps(welcome) + "\n").encode("utf-8"))
            await writer.drain()

            while self.running:
                # Read for heartbeats or subscribe commands
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode("utf-8").strip())
                    if msg.get("action") == "ping":
                        writer.write(
                            (json.dumps({"type": "pong"}) + "\n").encode("utf-8")
                        )
                        await writer.drain()
                except json.JSONDecodeError as jde:
                    logger.debug("[gateway_tcp] JSON decode error: %s", jde)

        except (
            asyncio.CancelledError,
            ConnectionResetError,
            BrokenPipeError,
        ) as conn_err:
            logger.debug("[gateway_tcp] Connection closed (%s)", conn_err)
        finally:
            self._cleanup_client(writer)

    def _cleanup_client(self, writer: asyncio.StreamWriter) -> None:
        self.clients.discard(writer)
        self._client_queues.pop(writer, None)
        task = self._client_tasks.pop(writer, None)
        if task and not task.done() and task != asyncio.current_task():
            task.cancel()
        try:
            writer.close()
        except Exception as close_exc:
            logger.debug("[gateway_tcp] Error closing client writer: %s", close_exc)
        self._stats["connected"] = len(self.clients)

    async def _client_writer_loop(
        self, writer: asyncio.StreamWriter, q: asyncio.Queue
    ) -> None:
        try:
            while self.running:
                data = await q.get()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()
        except Exception as exc:
            logger.debug("[gateway_tcp] Client writer loop exception: %s", exc)
        finally:
            self._cleanup_client(writer)

    async def _send_to_client(self, writer: asyncio.StreamWriter, data: bytes) -> bool:
        try:
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=0.05)
            return True
        except Exception as bcast_exc:
            logger.debug(
                "[gateway_tcp] Broadcast failed, evicting client: %s", bcast_exc
            )
            return False

    async def broadcast(self, payload: dict):
        """Broadcast JSON message to all connected clients asynchronously with zero head-of-line blocking."""
        if not self.clients:
            return
        data = (json.dumps(payload) + "\n").encode("utf-8")
        dead = []
        direct_writers = []
        for w in list(self.clients):
            # Check transport write buffer size if available
            transport = getattr(w, "transport", None)
            if transport is not None and hasattr(transport, "get_write_buffer_size"):
                try:
                    buf_sz = transport.get_write_buffer_size()
                    if (
                        isinstance(buf_sz, (int, float))
                        and buf_sz > self.max_buffer_size
                    ):
                        dead.append(w)
                        self._stats["dropped"] += 1
                        continue
                except Exception:
                    pass

            q = self._client_queues.get(w)
            if q is not None:
                try:
                    q.put_nowait(data)
                    self._stats["sent"] += 1
                except asyncio.QueueFull:
                    dead.append(w)
                    self._stats["dropped"] += 1
            else:
                direct_writers.append(w)

        if direct_writers:
            results = await asyncio.gather(
                *(self._send_to_client(w, data) for w in direct_writers),
                return_exceptions=False,
            )
            for w, ok in zip(direct_writers, results):
                if ok:
                    self._stats["sent"] += 1
                else:
                    dead.append(w)
                    self._stats["dropped"] += 1

        if dead:
            for w in dead:
                self._cleanup_client(w)

    async def start(self):
        self.running = True
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port, ssl=self.ssl_context
        )
        proto = "TLS" if self.ssl_context else "TCP"
        logger.info(
            f"{proto} Gateway listening on {self.host}:{self.port} (require_auth={self.require_auth})"
        )

    async def stop(self):
        self.running = False
        for task in list(self._client_tasks.values()):
            task.cancel()
        for w in list(self.clients):
            try:
                w.close()
            except Exception:
                pass
        self.clients.clear()
        self._client_queues.clear()
        self._client_tasks.clear()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for w in list(self.clients):
            try:
                w.close()
                await w.wait_closed()
            except Exception as close_exc:
                logger.debug(
                    "[gateway_tcp] Error closing client during shutdown: %s", close_exc
                )
        self.clients.clear()
