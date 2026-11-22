"""Observer JSONL event and health exporter with bounded queues and I/O error resilience."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("mdrap.observer.exporter")


class ObserverExporter:
    """Non-blocking background JSONL exporter for market events and health metrics."""

    def __init__(self, output_dir: str = "./observer_exports", flush_interval_s: float = 0.5, queue_size: int = 10000):
        self.output_dir = output_dir
        self.flush_interval_s = flush_interval_s
        self.running = False
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread: Optional[threading.Thread] = None

        self.events_written = 0
        self.health_written = 0
        self.dropped_records = 0
        self.write_errors = 0

        os.makedirs(self.output_dir, exist_ok=True)
        self.session_id = int(time.time())
        self.events_file = os.path.join(self.output_dir, f"events_{self.session_id}.jsonl")
        self.health_file = os.path.join(self.output_dir, f"health_{self.session_id}.jsonl")

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True, name="ObserverExporterWorker")
        self._thread.start()

    def record_event(self, event: Any) -> None:
        """Enqueue an event for asynchronous writing."""
        if not self.running:
            return
        try:
            if hasattr(event, "__dict__"):
                record = {"type": "event", "data": event.__dict__, "ts": time.time()}
            elif isinstance(event, dict):
                record = {"type": "event", "data": event, "ts": time.time()}
            else:
                record = {"type": "event", "data": str(event), "ts": time.time()}
            self._queue.put_nowait(record)
        except queue.Full:
            self.dropped_records += 1

    def record_health(self, snapshot: dict[str, Any]) -> None:
        """Enqueue a system health snapshot."""
        if not self.running:
            return
        try:
            record = {"type": "health", "data": snapshot, "ts": time.time()}
            self._queue.put_nowait(record)
        except queue.Full:
            self.dropped_records += 1

    def stop(self) -> None:
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _writer_loop(self) -> None:
        events_fp = None
        health_fp = None
        try:
            events_fp = open(self.events_file, "a", encoding="utf-8")
            health_fp = open(self.health_file, "a", encoding="utf-8")
        except OSError as e:
            self.write_errors += 1
            logger.error(f"Failed to open export files: {e}")
            return

        last_flush = time.time()

        try:
            while self.running or not self._queue.empty():
                try:
                    record = self._queue.get(timeout=0.1)
                except queue.Empty:
                    if time.time() - last_flush > self.flush_interval_s:
                        try:
                            events_fp.flush()
                            health_fp.flush()
                        except OSError as e:
                            self.write_errors += 1
                        last_flush = time.time()
                    continue

                line = json.dumps(record, default=str) + "\n"
                try:
                    if record.get("type") == "health":
                        health_fp.write(line)
                        self.health_written += 1
                    else:
                        events_fp.write(line)
                        self.events_written += 1
                except OSError as e:
                    self.write_errors += 1
                    logger.warning(f"I/O write error: {e}")

                self._queue.task_done()

                if time.time() - last_flush > self.flush_interval_s:
                    try:
                        events_fp.flush()
                        health_fp.flush()
                    except OSError:
                        self.write_errors += 1
                    last_flush = time.time()
        finally:
            try:
                events_fp.flush()
                health_fp.flush()
                events_fp.close()
                health_fp.close()
            except Exception:
                pass
