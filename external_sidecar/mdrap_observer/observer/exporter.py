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
    """Non-blocking background JSONL exporter for market events and health metrics.

    Explicit Durability Contract:
    - Clean shutdown (``stop(timeout_s=...)``) drains queued records within
      the configured timeout, flushes both JSONL streams (and calls
      ``os.fsync(fp.fileno())`` when ``fsync_on_flush=True``), closes file
      handles, and returns a structured completion summary. If the worker does
      not finish draining before ``timeout_s`` expires, ``stop()`` records
      ``incomplete_shutdown=True`` and ``abandoned_on_timeout``, logs an error,
      and optionally raises ``TimeoutError`` when ``raise_on_timeout=True``.
    - Abrupt termination (``SIGKILL``, power loss, unhandled process crash) is
      **NOT crash-durable** for in-queue or OS-buffered records: even with
      ``fsync_on_flush=True``, records still sitting in the in-memory queue at
      crash time are lost, and records written between periodic flushes remain
      subject to OS page-cache buffering until flushed/fsynced.
    - Bounded ``queue.Queue(maxsize=queue_size)`` (``queue_size > 0`` required)
      drops excess records non-blockingly when full, incrementing
      ``dropped_records += 1``.
    - File open, write, flush, fsync, and close ``OSError`` exceptions are
      caught, logged, and counted via ``write_errors += 1`` (and
      ``failed_records`` for unwritten/failed items) without crashing the
      background thread or leaking file handles.
    """

    def __init__(
        self,
        output_dir: str = "./observer_exports",
        flush_interval_s: float = 0.5,
        queue_size: int = 10000,
        fsync_on_flush: bool = False,
    ):
        if (
            isinstance(queue_size, bool)
            or not isinstance(queue_size, int)
            or queue_size <= 0
        ):
            raise ValueError(
                f"queue_size must be a positive integer (> 0), got {queue_size!r}"
            )
        if (
            isinstance(flush_interval_s, bool)
            or not isinstance(flush_interval_s, (int, float))
            or flush_interval_s <= 0.0
        ):
            raise ValueError(
                f"flush_interval_s must be a positive number (> 0), got {flush_interval_s!r}"
            )

        self.output_dir = output_dir
        self.flush_interval_s = float(flush_interval_s)
        self.queue_size = queue_size
        self.fsync_on_flush = bool(fsync_on_flush)
        self.running = False
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread: Optional[threading.Thread] = None
        self._stats_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_finalize_lock = threading.Lock()
        self._stopping: bool = False
        self._active_stops: int = 0
        self._lifecycle_epoch: int = 0

        self.events_submitted = 0
        self.health_submitted = 0
        self.accepted_records = 0
        self.events_written = 0
        self.health_written = 0
        self.events_flushed = 0
        self.health_flushed = 0
        self.events_fsynced = 0
        self.health_fsynced = 0
        self._unflushed_events = 0
        self._unflushed_health = 0
        self._unfsynced_events = 0
        self._unfsynced_health = 0
        self.dropped_records = 0
        self.failed_records = 0
        self.write_errors = 0
        self.open_errors = 0
        self.flush_errors = 0
        self.fsync_errors = 0
        self.close_errors = 0
        self.abandoned_on_timeout = 0
        self.incomplete_shutdown = False
        self.last_stop_result: Optional[dict[str, Any]] = None

        os.makedirs(self.output_dir, exist_ok=True)
        self.session_id = int(time.time())
        self.events_file = os.path.join(
            self.output_dir, f"events_{self.session_id}.jsonl"
        )
        self.health_file = os.path.join(
            self.output_dir, f"health_{self.session_id}.jsonl"
        )

    @property
    def submitted_records(self) -> int:
        with self._stats_lock:
            return self.events_submitted + self.health_submitted

    @property
    def written_records(self) -> int:
        with self._stats_lock:
            return self.events_written + self.health_written

    @property
    def flushed_records(self) -> int:
        with self._stats_lock:
            return self.events_flushed + self.health_flushed

    @property
    def fsynced_records(self) -> int:
        with self._stats_lock:
            return self.events_fsynced + self.health_fsynced

    def snapshot_stats(self) -> dict[str, Any]:
        """Return a consistent snapshot of exporter accounting and queue depth."""
        with self._stats_lock:
            submitted = self.events_submitted + self.health_submitted
            written = self.events_written + self.health_written
            flushed = self.events_flushed + self.health_flushed
            fsynced = self.events_fsynced + self.health_fsynced
            return {
                "submitted_records": submitted,
                "events_submitted": self.events_submitted,
                "health_submitted": self.health_submitted,
                "accepted_records": self.accepted_records,
                "written_records": written,
                "events_written": self.events_written,
                "health_written": self.health_written,
                "flushed_records": flushed,
                "events_flushed": self.events_flushed,
                "health_flushed": self.health_flushed,
                "fsynced_records": fsynced,
                "events_fsynced": self.events_fsynced,
                "health_fsynced": self.health_fsynced,
                "dropped_records": self.dropped_records,
                "failed_records": self.failed_records,
                "write_errors": self.write_errors,
                "open_errors": self.open_errors,
                "flush_errors": self.flush_errors,
                "fsync_errors": self.fsync_errors,
                "close_errors": self.close_errors,
                "abandoned_on_timeout": self.abandoned_on_timeout,
                "incomplete_shutdown": self.incomplete_shutdown,
                "queue_size": self._queue.qsize(),
                "queue_capacity": self.queue_size,
            }

    def _should_keep_running(self) -> bool:
        with self._lifecycle_lock:
            return self.running or not self._queue.empty()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopping:
                logger.error(
                    "Refusing to start a new ObserverExporter worker while shutdown is still in progress"
                )
                return
            if self._thread is not None and self._thread.is_alive():
                if self.running:
                    return
                logger.error(
                    "Refusing to start a new ObserverExporter worker while previous worker is still alive"
                )
                return
            self._lifecycle_epoch += 1
            self.incomplete_shutdown = False
            self.abandoned_on_timeout = 0
            self.running = True
            self._thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="ObserverExporterWorker"
            )
            self._thread.start()

    def _enqueue_record(self, kind: str, record: dict[str, Any]) -> None:
        with self._lifecycle_lock:
            if not self.running:
                return
            try:
                self._queue.put_nowait(record)
            except queue.Full:
                with self._stats_lock:
                    if kind == "health":
                        self.health_submitted += 1
                    else:
                        self.events_submitted += 1
                    self.dropped_records += 1
                return
            with self._stats_lock:
                if kind == "health":
                    self.health_submitted += 1
                else:
                    self.events_submitted += 1
                self.accepted_records += 1

    def record_event(self, event: Any) -> None:
        """Enqueue an event for asynchronous writing."""
        if not self.running:
            return
        if hasattr(event, "__dict__"):
            record = {"type": "event", "data": dict(event.__dict__), "ts": time.time()}
        elif isinstance(event, dict):
            record = {"type": "event", "data": dict(event), "ts": time.time()}
        else:
            record = {"type": "event", "data": str(event), "ts": time.time()}
        self._enqueue_record("event", record)

    def record_health(self, snapshot: dict[str, Any]) -> None:
        """Enqueue a system health snapshot."""
        if not self.running:
            return
        record = {"type": "health", "data": snapshot, "ts": time.time()}
        self._enqueue_record("health", record)

    def stop(
        self, timeout_s: Optional[float] = 30.0, raise_on_timeout: bool = False
    ) -> dict[str, Any]:
        """Stop accepting new records, wait up to timeout_s for drain, and report status."""
        if timeout_s is not None and (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or timeout_s < 0.0
        ):
            raise ValueError(
                f"timeout_s must be non-negative or None, got {timeout_s!r}"
            )

        with self._lifecycle_lock:
            self._active_stops += 1
            self._stopping = True
            self.running = False
            thread = self._thread
            stop_epoch = self._lifecycle_epoch

        finalized_active_stop = False
        try:
            if thread and thread.is_alive():
                thread.join(timeout=timeout_s)

            with self._stop_finalize_lock:
                thread_alive = bool(thread and thread.is_alive())
                worker_terminated = not thread_alive

                with self._stats_lock:
                    remaining_queue = self._queue.qsize()
                    queue_drained = remaining_queue == 0
                    drained = worker_terminated and queue_drained
                    timed_out = not drained

                    if not drained:
                        self.incomplete_shutdown = True
                        self.abandoned_on_timeout = remaining_queue
                    else:
                        self.incomplete_shutdown = False
                        self.abandoned_on_timeout = 0

                    submitted = self.events_submitted + self.health_submitted
                    written = self.events_written + self.health_written
                    flushed = self.events_flushed + self.health_flushed
                    fsynced = self.events_fsynced + self.health_fsynced
                    unflushed = self._unflushed_events + self._unflushed_health
                    unfsynced = self._unfsynced_events + self._unfsynced_health

                    open_succeeded = self.open_errors == 0
                    records_written_ok = self.failed_records == 0 and written == (
                        self.accepted_records - remaining_queue
                    )
                    flush_succeeded = (
                        worker_terminated
                        and open_succeeded
                        and self.flush_errors == 0
                        and unflushed == 0
                        and flushed == written
                    )
                    fsync_succeeded: Optional[bool] = (
                        (
                            worker_terminated
                            and open_succeeded
                            and self.fsync_errors == 0
                            and unfsynced == 0
                            and fsynced == written
                        )
                        if self.fsync_on_flush
                        else None
                    )
                    close_succeeded = (
                        worker_terminated and open_succeeded and self.close_errors == 0
                    )
                    flush_fsync_close_succeeded = (
                        flush_succeeded
                        and close_succeeded
                        and (fsync_succeeded is not False)
                    )
                    io_clean = (
                        self.write_errors == 0
                        and self.open_errors == 0
                        and self.flush_errors == 0
                        and self.fsync_errors == 0
                        and self.close_errors == 0
                        and self.failed_records == 0
                        and unflushed == 0
                        and (not self.fsync_on_flush or unfsynced == 0)
                    )
                    clean = drained and io_clean

                    stats: dict[str, Any] = {
                        "submitted_records": submitted,
                        "events_submitted": self.events_submitted,
                        "health_submitted": self.health_submitted,
                        "accepted_records": self.accepted_records,
                        "written_records": written,
                        "events_written": self.events_written,
                        "health_written": self.health_written,
                        "flushed_records": flushed,
                        "events_flushed": self.events_flushed,
                        "health_flushed": self.health_flushed,
                        "fsynced_records": fsynced,
                        "events_fsynced": self.events_fsynced,
                        "health_fsynced": self.health_fsynced,
                        "dropped_records": self.dropped_records,
                        "failed_records": self.failed_records,
                        "write_errors": self.write_errors,
                        "open_errors": self.open_errors,
                        "flush_errors": self.flush_errors,
                        "fsync_errors": self.fsync_errors,
                        "close_errors": self.close_errors,
                        "abandoned_on_timeout": self.abandoned_on_timeout,
                        "incomplete_shutdown": self.incomplete_shutdown,
                        "queue_size": remaining_queue,
                        "queue_capacity": self.queue_size,
                    }

                result: dict[str, Any] = {
                    "clean": clean,
                    "drained": drained,
                    "queue_drained": queue_drained,
                    "timed_out": timed_out,
                    "worker_terminated": worker_terminated,
                    "worker_alive": thread_alive,
                    "open_succeeded": open_succeeded,
                    "records_written_ok": records_written_ok,
                    "flush_succeeded": flush_succeeded,
                    "fsync_succeeded": fsync_succeeded,
                    "close_succeeded": close_succeeded,
                    "flush_fsync_close_succeeded": flush_fsync_close_succeeded,
                    "remaining_queue": remaining_queue,
                    **stats,
                }
                with self._lifecycle_lock:
                    if self._lifecycle_epoch == stop_epoch:
                        self.last_stop_result = dict(result)
                    self._active_stops -= 1
                    if self._active_stops == 0:
                        self._stopping = False
                    finalized_active_stop = True
        finally:
            if not finalized_active_stop:
                with self._lifecycle_lock:
                    self._active_stops -= 1
                    if self._active_stops == 0:
                        self._stopping = False

        if not clean:
            if not drained:
                logger.error(
                    "Incomplete exporter shutdown after timeout=%rs: worker_alive=%s, remaining_queue=%d",
                    timeout_s,
                    thread_alive,
                    remaining_queue,
                )
            else:
                logger.error(
                    "Exporter shutdown completed with I/O failures: write_errors=%d, failed_records=%d, open_errors=%d, flush_errors=%d, fsync_errors=%d, close_errors=%d",
                    stats["write_errors"],
                    stats["failed_records"],
                    stats["open_errors"],
                    stats["flush_errors"],
                    stats["fsync_errors"],
                    stats["close_errors"],
                )
            if not drained and raise_on_timeout:
                raise TimeoutError(
                    f"ObserverExporter did not drain within {timeout_s}s "
                    f"(remaining_queue={remaining_queue}, worker_alive={thread_alive})"
                )

        return result

    def _mark_flushed_unlocked(self, label: str) -> None:
        if label == "health":
            self.health_flushed += self._unflushed_health
            self._unflushed_health = 0
        else:
            self.events_flushed += self._unflushed_events
            self._unflushed_events = 0

    def _mark_fsynced_unlocked(self, label: str) -> None:
        if label == "health":
            self.health_fsynced += self._unfsynced_health
            self._unfsynced_health = 0
        else:
            self.events_fsynced += self._unfsynced_events
            self._unfsynced_events = 0

    def _flush_stream(self, fp: Any, label: str) -> None:
        if fp is None:
            return
        try:
            fp.flush()
            with self._stats_lock:
                self._mark_flushed_unlocked(label)
        except OSError as e:
            with self._stats_lock:
                self.write_errors += 1
                self.flush_errors += 1
            logger.warning(f"I/O flush error on {label}: {e}")
            return
        if self.fsync_on_flush and hasattr(fp, "fileno"):
            try:
                os.fsync(fp.fileno())
                with self._stats_lock:
                    self._mark_fsynced_unlocked(label)
            except OSError as e:
                with self._stats_lock:
                    self.write_errors += 1
                    self.fsync_errors += 1
                logger.warning(f"I/O fsync error on {label}: {e}")

    def _close_stream(self, fp: Any, label: str) -> None:
        if fp is None:
            return
        try:
            fp.flush()
            with self._stats_lock:
                self._mark_flushed_unlocked(label)
        except OSError as e:
            with self._stats_lock:
                self.write_errors += 1
                self.flush_errors += 1
            logger.warning(f"I/O final flush error on {label}: {e}")
        else:
            if self.fsync_on_flush and hasattr(fp, "fileno"):
                try:
                    os.fsync(fp.fileno())
                    with self._stats_lock:
                        self._mark_fsynced_unlocked(label)
                except OSError as e:
                    with self._stats_lock:
                        self.write_errors += 1
                        self.fsync_errors += 1
                    logger.warning(f"I/O final fsync error on {label}: {e}")
        finally:
            try:
                fp.close()
            except OSError as e:
                with self._stats_lock:
                    self.write_errors += 1
                    self.close_errors += 1
                logger.warning(f"I/O close error on {label}: {e}")

    def _writer_loop(self) -> None:
        events_fp = None
        health_fp = None
        try:
            events_fp = open(self.events_file, "a", encoding="utf-8")
            health_fp = open(self.health_file, "a", encoding="utf-8")
        except OSError as e:
            with self._stats_lock:
                self.open_errors += 1
                self.write_errors += 1
            logger.error(f"Failed to open export files: {e}")
            self._close_stream(events_fp, "events")
            self._close_stream(health_fp, "health")
            while self._should_keep_running():
                try:
                    self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                with self._stats_lock:
                    self.failed_records += 1
                self._queue.task_done()
            return

        last_flush = time.monotonic()

        try:
            while self._should_keep_running():
                try:
                    record = self._queue.get(timeout=0.1)
                except queue.Empty:
                    if time.monotonic() - last_flush >= self.flush_interval_s:
                        self._flush_stream(events_fp, "events")
                        self._flush_stream(health_fp, "health")
                        last_flush = time.monotonic()
                    continue

                try:
                    line = json.dumps(record, default=str) + "\n"
                    if record.get("type") == "health":
                        health_fp.write(line)
                        with self._stats_lock:
                            self.health_written += 1
                            self._unflushed_health += 1
                            self._unfsynced_health += 1
                    else:
                        events_fp.write(line)
                        with self._stats_lock:
                            self.events_written += 1
                            self._unflushed_events += 1
                            self._unfsynced_events += 1
                except OSError as e:
                    with self._stats_lock:
                        self.write_errors += 1
                        self.failed_records += 1
                    logger.warning(f"I/O write error: {e}")
                except Exception as e:
                    with self._stats_lock:
                        self.write_errors += 1
                        self.failed_records += 1
                    logger.warning(f"Serialization/write error: {e}")
                finally:
                    self._queue.task_done()

                if time.monotonic() - last_flush >= self.flush_interval_s:
                    self._flush_stream(events_fp, "events")
                    self._flush_stream(health_fp, "health")
                    last_flush = time.monotonic()
        finally:
            self._close_stream(events_fp, "events")
            self._close_stream(health_fp, "health")
