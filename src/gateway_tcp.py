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
        require_auth: bool = False,
    ):
        self.host = host
        self.port = port
        self.security_manager = security_manager
        self.ssl_context = ssl_context
        self.require_auth = require_auth
        self.clients: set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None
        self._stats = {"sent": 0, "dropped": 0, "connected": 0, "auth_failures": 0}
        self.running = False

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        # Optional TLS / Auth gate
        if self.require_auth and self.security_manager is not None:
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
                    err_msg = json.dumps({"type": "error", "message": "Unauthorized: invalid or inactive token"}) + "\n"
                    writer.write(err_msg.encode("utf-8"))
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return
            except Exception:
                self._stats["auth_failures"] += 1
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return

        self.clients.add(writer)
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
                except json.JSONDecodeError:
                    pass

        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.clients.discard(writer)
            self._stats["connected"] = len(self.clients)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def broadcast(self, payload: dict):
        """Broadcast any JSON-serializable dictionary to all connected TCP clients."""
        if not self.clients:
            return
        data = (json.dumps(payload) + "\n").encode("utf-8")
        dead = []
        for writer in list(self.clients):
            try:
                writer.write(data)
                await asyncio.wait_for(writer.drain(), timeout=0.05)
                self._stats["sent"] += 1
            except Exception:
                dead.append(writer)
                self._stats["dropped"] += 1
        for w in dead:
            self.clients.discard(w)
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        self._stats["connected"] = len(self.clients)

    async def start(self):
        self.running = True
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port, ssl=self.ssl_context
        )
        proto = "TLS" if self.ssl_context else "TCP"
        logger.info(f"{proto} Gateway listening on {self.host}:{self.port} (require_auth={self.require_auth})")

    async def stop(self):
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for w in list(self.clients):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass
        self.clients.clear()
