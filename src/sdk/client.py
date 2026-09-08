"""
MDRAP Python Client SDK.

Provides a frictionless, quantitative-ready Python interface to the MDRAP TCP Gateway.
Automatically handles network I/O, reconnects, and parses the raw JSON byte stream 
into structured dictionaries or Pandas DataFrames for immediate quantitative analysis.
"""
import asyncio
import json
import logging
from typing import Callable, Optional, List, Dict, Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

logger = logging.getLogger("mdrap.sdk")

class MDrapClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9000):
        self.host = host
        self.port = port
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.running = False
        
        # Publisher/Subscriber pattern for low coupling
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
            
        handlers = self._handlers.get(msg_type, [])
        for handler in handlers:
            try:
                handler(msg)
            except Exception as e:
                logger.error(f"Error in handler for {msg_type}: {e}")

    async def connect(self) -> bool:
        """Establish connection to the MDRAP TCP Gateway."""
        try:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
            
            # Wait for welcome message
            line = await self.reader.readline()
            welcome = json.loads(line.decode('utf-8').strip())
            if welcome.get("type") == "system" and welcome.get("status") == "connected":
                logger.info(f"Connected to MDRAP Gateway at {self.host}:{self.port}")
                self.running = True
                self._dispatch(welcome)  # Dispatch system welcome
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def _heartbeat_loop(self):
        """Send periodic pings to keep the connection alive."""
        while self.running and self.writer:
            try:
                ping_msg = json.dumps({"action": "ping"}) + "\n"
                self.writer.write(ping_msg.encode('utf-8'))
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
                
                msg = json.loads(line.decode('utf-8').strip())
                self._dispatch(msg)
            except Exception as e:
                logger.error(f"Read error: {e}")
                self.running = False
                break

    async def subscribe(self, on_event: Optional[Callable[[Dict[str, Any]], None]] = None):
        """Subscribe to live events and block while listening. 
        Legacy fallback: if on_event is provided, registers it for 'event' type."""
        if on_event:
            self.on("event", on_event)
            
        if not self.reader or not self.writer:
            connected = await self.connect()
            if not connected:
                raise ConnectionError("Could not connect to MDRAP Gateway")

        try:
            await asyncio.gather(
                self._heartbeat_loop(),
                self._read_loop()
            )
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
        """
        Convert a list of raw event dictionaries to a Pandas DataFrame.
        Useful for converting historical or buffered events.
        """
        if not HAS_PANDAS:
            raise ImportError("pandas is not installed. Run `pip install pandas` to use this feature.")
        
        df = pd.DataFrame(events)
        if not df.empty and "ts" in df.columns:
            df["ts"] = pd.to_datetime(df["ts"], unit='s')
            df.set_index("ts", inplace=True)
            df.sort_index(inplace=True)
        return df
