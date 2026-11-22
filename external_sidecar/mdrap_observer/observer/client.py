"""Observer resilient client connector using documented MDRAP public APIs."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from mdrap.service import StreamClient
from mdrap.shm import SHMReader

logger = logging.getLogger("mdrap.observer.client")


class ObserverClient:
    """
    Resilient sidecar consumer that ingests market data from either MDRAP's
    lock-free Shared Memory ring buffer (sub-microsecond IPC) or TCP StreamClient.
    """

    def __init__(
        self,
        mode: str = "tcp",
        host: str = "127.0.0.1",
        port: int = 9000,
        ring_name: str = "mdrap_feed",
        on_event_callback: Optional[Callable[[dict], None]] = None,
    ):
        self.mode = mode.lower()
        self.host = host
        self.port = port
        self.ring_name = ring_name
        self.on_event = on_event_callback
        self.running = False
        self.connected = False
        self._thread: Optional[threading.Thread] = None
        self._current_client: Optional[StreamClient] = None
        self._current_reader: Optional[SHMReader] = None
        self.reconnect_count = 0
        self.events_count = 0
        self.malformed_messages = 0

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ObserverIngressWorker")
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self.connected = False
        if self._current_client:
            try:
                self._current_client.close()
            except Exception:
                pass
        if self._current_reader:
            try:
                self._current_reader.close()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run_loop(self) -> None:
        if self.mode == "shm":
            self._run_shm()
        else:
            self._run_tcp()

    def _run_shm(self) -> None:
        """Shared-memory consumer loop with automatic reconnection on ring recreation."""
        initial_connect = True
        last_seq = None
        while self.running:
            reader: Optional[SHMReader] = None
            try:
                reader = SHMReader(name=self.ring_name)
                self._current_reader = reader
                self.connected = True
                if not initial_connect:
                    self.reconnect_count += 1
                initial_connect = False
                logger.info(f"Attached to SHM ring buffer '{self.ring_name}'")

                if last_seq is None:
                    head_seq = reader.read_latest_seq()
                    start_seq = max(1, head_seq + 1) if head_seq > 0 else 1
                else:
                    start_seq = last_seq + 1

                while self.running:
                    for evt in reader.stream(start_seq=start_seq, timeout=0.2):
                        if not self.running:
                            break
                        if not isinstance(evt, dict):
                            self.malformed_messages += 1
                            continue
                        if evt.get("type") == "EPOCH_CHANGE":
                            logger.info(f"SHM epoch change detected: {evt}")
                            start_seq = 1
                            last_seq = 0
                            continue
                        if evt.get("type") == "PUBLISHER_DEAD":
                            logger.warning(f"SHM publisher dead: {evt}")
                            break

                        if "seq" in evt:
                            last_seq = evt["seq"]
                            start_seq = last_seq + 1

                        self.events_count += 1
                        if self.on_event:
                            self.on_event(evt)

            except Exception as e:
                self.connected = False
                if not initial_connect:
                    self.reconnect_count += 1
                logger.debug(f"SHM Reader error: {e}. Retrying in 0.5s...")
                time.sleep(0.5)
            finally:
                if reader:
                    try:
                        reader.close()
                    except Exception:
                        pass
                self._current_reader = None

    def _run_tcp(self) -> None:
        """TCP StreamClient loop with exponential backoff reconnection."""
        backoff = 0.2
        initial_connect = True
        while self.running:
            client: Optional[StreamClient] = None
            try:
                client = StreamClient(host=self.host, port=self.port, timeout=2.0)
                client.connect()
                self._current_client = client
                self.connected = True
                if not initial_connect:
                    self.reconnect_count += 1
                initial_connect = False
                backoff = 0.2

                for event in client.stream(symbol="ALL"):
                    if not self.running:
                        break
                    if not isinstance(event, dict):
                        self.malformed_messages += 1
                        continue
                    self.events_count += 1
                    if self.on_event:
                        self.on_event(event)

            except (ConnectionRefusedError, TimeoutError, OSError) as e:
                self.connected = False
                if not initial_connect:
                    self.reconnect_count += 1
                logger.debug(f"TCP connection failed ({e}). Reconnecting in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff = min(3.0, backoff * 1.5)
            except Exception as e:
                self.connected = False
                if not initial_connect:
                    self.reconnect_count += 1
                logger.warning(f"Unexpected TCP error: {e}. Retrying in {backoff:.1f}s...")
                time.sleep(backoff)
                backoff = min(3.0, backoff * 1.5)
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
                self._current_client = None
