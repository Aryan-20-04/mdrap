"""Observer resilient client connector using documented MDRAP public APIs."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from mdrap.service import StreamClient
from mdrap.shm import ReaderState, SHMReader

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
        shm_writer_dead_timeout: float = 2.0,
    ):
        self.mode = mode.lower()
        self.host = host
        self.port = port
        self.ring_name = ring_name
        self.on_event = on_event_callback
        self.shm_writer_dead_timeout = shm_writer_dead_timeout
        self.running = False
        self.connected = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_client: Optional[StreamClient] = None
        self._current_reader: Optional[SHMReader] = None
        self.reconnect_count = 0
        self.events_count = 0
        self.malformed_messages = 0
        self.shm_overrun_laps = 0
        self.shm_skipped_ticks = 0

    def start(self) -> None:
        self._stop_event.clear()
        self.running = True
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="ObserverIngressWorker"
        )
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        self.connected = False
        self._stop_event.set()
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
        """Shared-memory consumer loop with epoch tracking, dead-writer detection, and backoff."""
        initial_connect = True
        last_seq: Optional[int] = None
        last_epoch_id: Optional[int] = None
        backoff = 0.1
        max_backoff = 2.0

        while self.running and not self._stop_event.is_set():
            reader: Optional[SHMReader] = None
            should_backoff = False
            last_reader_laps = 0
            last_reader_skipped = 0

            def _sync_overrun() -> None:
                nonlocal last_reader_laps, last_reader_skipped
                if reader is None:
                    return
                dl = reader.overrun_stats.total_laps - last_reader_laps
                ds = reader.overrun_stats.skipped_ticks - last_reader_skipped
                if dl > 0:
                    self.shm_overrun_laps += dl
                    last_reader_laps = reader.overrun_stats.total_laps
                if ds > 0:
                    self.shm_skipped_ticks += ds
                    last_reader_skipped = reader.overrun_stats.skipped_ticks

            try:
                reader = SHMReader(name=self.ring_name)
                if not reader.is_writer_alive(max_stale_s=self.shm_writer_dead_timeout):
                    raise RuntimeError("SHM publisher heartbeat stale on attach")

                self._current_reader = reader
                self.connected = True
                backoff = 0.1

                current_epoch = reader.epoch_id
                if last_epoch_id is None or current_epoch != last_epoch_id:
                    # First attach or publisher restarted with a new epoch:
                    # start from the earliest retained committed sequence in the ring.
                    start_seq = reader.earliest_available_seq(record_overrun=True)
                    last_seq = None
                    last_epoch_id = current_epoch
                    _sync_overrun()
                else:
                    start_seq = (
                        last_seq + 1
                        if last_seq is not None
                        else reader.earliest_available_seq(record_overrun=True)
                    )
                    _sync_overrun()

                initial_connect = False
                logger.info(
                    f"Attached to SHM ring buffer '{self.ring_name}' "
                    f"(epoch={current_epoch}, start_seq={start_seq})"
                )

                while self.running and not self._stop_event.is_set():
                    publisher_dead = False
                    for evt in reader.stream(
                        start_seq=start_seq,
                        timeout=0.2,
                        writer_dead_timeout=self.shm_writer_dead_timeout,
                    ):
                        if not self.running or self._stop_event.is_set():
                            break
                        if not isinstance(evt, dict):
                            self.malformed_messages += 1
                            continue
                        ev_type = evt.get("type")
                        if ev_type == "EPOCH_CHANGE":
                            logger.info(f"SHM epoch change detected: {evt}")
                            new_epoch = evt.get("new_epoch") or reader.epoch_id
                            last_epoch_id = new_epoch
                            last_seq = None
                            start_seq = reader.earliest_available_seq(
                                record_overrun=False
                            )
                            _sync_overrun()
                            if not initial_connect:
                                self.reconnect_count += 1
                            continue
                        if ev_type == "PUBLISHER_DEAD":
                            logger.warning(f"SHM publisher dead: {evt}")
                            publisher_dead = True
                            break

                        if "seq" in evt:
                            last_seq = evt["seq"]
                            start_seq = last_seq + 1

                        self.events_count += 1
                        if self.on_event:
                            self.on_event(evt)

                    _sync_overrun()

                    if not self.running or self._stop_event.is_set():
                        break

                    if (
                        publisher_dead
                        or reader.state != ReaderState.ATTACHED
                        or not reader.check_epoch_valid()
                    ):
                        self.connected = False
                        if not initial_connect:
                            self.reconnect_count += 1
                        should_backoff = True
                        break

                    # Idle 0.2s timeout with healthy publisher; keep polling cleanly
                    if last_seq is None and start_seq == 0:
                        start_seq = reader.earliest_available_seq(record_overrun=False)

            except Exception as e:
                self.connected = False
                if not initial_connect:
                    self.reconnect_count += 1
                should_backoff = True
                logger.debug(f"SHM Reader error: {e}. Retrying in {backoff:.2f}s...")
            finally:
                _sync_overrun()
                if reader:
                    try:
                        reader.close()
                    except Exception:
                        pass
                self._current_reader = None

            if should_backoff and self.running and not self._stop_event.is_set():
                self._stop_event.wait(backoff)
                backoff = min(max_backoff, backoff * 2.0)

    def _run_tcp(self) -> None:
        """TCP StreamClient loop with exponential backoff reconnection."""
        backoff = 0.2
        initial_connect = True
        while self.running and not self._stop_event.is_set():
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
                    if not self.running or self._stop_event.is_set():
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
                logger.debug(
                    f"TCP connection failed ({e}). Reconnecting in {backoff:.1f}s..."
                )
                self._stop_event.wait(backoff)
                backoff = min(3.0, backoff * 1.5)
            except Exception as e:
                self.connected = False
                if not initial_connect:
                    self.reconnect_count += 1
                logger.warning(
                    f"Unexpected TCP error: {e}. Retrying in {backoff:.1f}s..."
                )
                self._stop_event.wait(backoff)
                backoff = min(3.0, backoff * 1.5)
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
                self._current_client = None
