"""
MDRAP AsyncIO Raw TCP Gateway (§27).

Provides high-throughput, low-latency push streaming to external quant clients.
Uses raw TCP sockets instead of WebSockets/HTTP to strictly comply with 
project rules ("CLI-only MVP, no HTTP/REST/WebSocket") while delivering 
institutional-grade, zero-overhead connectivity (similar to ITCH/OUCH).
"""
import asyncio
import json
import logging
from typing import Dict, Set

from models import CanonicalEvent

logger = logging.getLogger("mdrap.gateway")

class TCPGatewayServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.host = host
        self.port = port
        self.clients: Set[asyncio.StreamWriter] = set()
        self.server: asyncio.AbstractServer | None = None
        self._stats = {"sent": 0, "dropped": 0, "connected": 0}
        self.running = False

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.clients.add(writer)
        self._stats["connected"] = len(self.clients)
        
        try:
            # Send welcome protocol message
            welcome = {"type": "system", "message": "MDRAP TCP Gateway v1.0", "status": "connected"}
            writer.write((json.dumps(welcome) + "\n").encode('utf-8'))
            await writer.drain()

            while self.running:
                # Read for heartbeats or subscribe commands
                line = await reader.readline()
                if not line:
                    break
                
                try:
                    msg = json.loads(line.decode('utf-8').strip())
                    if msg.get("action") == "ping":
                        writer.write((json.dumps({"type": "pong"}) + "\n").encode('utf-8'))
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

        data = (json.dumps(payload) + "\n").encode('utf-8')
        
        disconnected = set()
        for writer in self.clients:
            try:
                writer.write(data)
                self._stats["sent"] += 1
            except Exception:
                disconnected.add(writer)
                self._stats["dropped"] += 1
                
        # Drain buffers to detect backpressure
        for writer in self.clients - disconnected:
            try:
                # Use a small timeout for drain to shed slow consumers
                await asyncio.wait_for(writer.drain(), timeout=0.05)
            except asyncio.TimeoutError:
                # Slow consumer detected, drop them to protect gateway memory
                disconnected.add(writer)
                self._stats["dropped"] += 1
            except Exception:
                disconnected.add(writer)
                self._stats["dropped"] += 1

        for writer in disconnected:
            self.clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
        
        self._stats["connected"] = len(self.clients)

    async def start(self):
        self.running = True
        self.server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info(f"TCP Gateway listening on {self.host}:{self.port}")

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
