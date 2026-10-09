"""
Runtime Worker Supervision & Failure Recovery Engine (Phase 2 Workstream C).

Provides deterministic lifecycle supervision for background tasks and workers:
- Ingestion feed workers
- SPSC/SHM drainer workers
- Socket listener & broadcast workers

Invariants:
1. Bounded restart policy: Exponential backoff with maximum retries within sliding window.
2. Fatal error escalation: Unrecoverable errors (e.g. fatal I/O, corrupt journal, auth failure)
   immediately escalate to FATAL without futile restart loops.
3. Clean shutdown: Cooperatively cancels and joins all worker threads within deadline.
4. Comprehensive observability: Tracks worker status, restart timestamps, and crash reasons.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

__stability__ = "stable"

logger = logging.getLogger("mdrap.supervisor")


class WorkerState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    CRASHED = "CRASHED"
    RESTARTING = "RESTARTING"
    STOPPED = "STOPPED"
    FAILED_FATAL = "FAILED_FATAL"


class FatalWorkerError(Exception):
    """Exception indicating an unrecoverable failure that should not be retried."""
    pass


@dataclass
class WorkerSpec:
    """Specification defining a supervised worker target and its restart policy."""

    name: str
    target: Callable[..., None]
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    is_critical: bool = True
    max_retries: int = 3
    retry_window_s: float = 10.0
    restart_delay_base_s: float = 0.2
    restart_delay_max_s: float = 5.0


@dataclass
class WorkerInfo:
    """Runtime tracking information for a supervised worker."""

    spec: WorkerSpec
    state: WorkerState = WorkerState.IDLE
    thread: Optional[threading.Thread] = None
    restart_timestamps: List[float] = field(default_factory=list)
    total_restarts: int = 0
    last_error: Optional[str] = None
    stop_event: threading.Event = field(default_factory=threading.Event)


class RuntimeSupervisor:
    """
    Supervises background worker threads with bounded restart policies
    and fatal error escalation.
    """

    def __init__(
        self,
        on_fatal_failure: Optional[Callable[[str, str], None]] = None,
    ):
        self._workers: Dict[str, WorkerInfo] = {}
        self._lock = threading.RLock()
        self._running = False
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None
        self._on_fatal_failure = on_fatal_failure
        self._fatal_error: Optional[str] = None

    def register_worker(self, spec: WorkerSpec) -> None:
        """Register a worker specification to be supervised."""
        with self._lock:
            if spec.name in self._workers:
                raise ValueError(f"Worker '{spec.name}' is already registered")
            self._workers[spec.name] = WorkerInfo(spec=spec)

    def start(self) -> None:
        """Start all registered workers and the supervision monitor thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()

            for info in self._workers.values():
                self._start_worker_thread(info)

            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="mdrap-supervisor-monitor",
            )
            self._monitor_thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        """Stop all workers and join their threads within the timeout."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

            for info in self._workers.values():
                info.stop_event.set()
                info.state = WorkerState.STOPPED

        deadline = time.time() + timeout_s
        # Join worker threads
        for info in list(self._workers.values()):
            if info.thread and info.thread.is_alive():
                remaining = max(0.01, deadline - time.time())
                info.thread.join(timeout=remaining)

        if self._monitor_thread and self._monitor_thread.is_alive():
            remaining = max(0.01, deadline - time.time())
            self._monitor_thread.join(timeout=remaining)

    def is_healthy(self) -> bool:
        """Return True if all critical workers are running or safely restarting within budget."""
        with self._lock:
            if self._fatal_error is not None:
                return False
            for info in self._workers.values():
                if info.spec.is_critical:
                    if info.state in (WorkerState.FAILED_FATAL,):
                        return False
            return True

    def get_status(self) -> Dict[str, Any]:
        """Return runtime status of all supervised workers."""
        with self._lock:
            workers_summary = {}
            for name, info in self._workers.items():
                workers_summary[name] = {
                    "state": info.state.value,
                    "is_critical": info.spec.is_critical,
                    "total_restarts": info.total_restarts,
                    "last_error": info.last_error,
                    "alive": bool(info.thread and info.thread.is_alive()),
                }
            return {
                "running": self._running,
                "healthy": self.is_healthy(),
                "fatal_error": self._fatal_error,
                "workers": workers_summary,
            }

    def _start_worker_thread(self, info: WorkerInfo) -> None:
        """Launch a worker thread wrapped in exception capture."""
        info.stop_event.clear()
        info.state = WorkerState.RUNNING

        def runner():
            try:
                info.spec.target(info.stop_event, *info.spec.args, **info.spec.kwargs)
            except FatalWorkerError as fatal_exc:
                info.last_error = f"FATAL: {fatal_exc}"
                info.state = WorkerState.FAILED_FATAL
                self._handle_fatal(info.spec.name, str(fatal_exc))
            except Exception as exc:
                info.last_error = f"{type(exc).__name__}: {exc}"
                info.state = WorkerState.CRASHED
                logger.warning(
                    "[supervisor] Worker '%s' crashed with error: %s",
                    info.spec.name,
                    info.last_error,
                )
            else:
                info.state = WorkerState.STOPPED

        t = threading.Thread(
            target=runner,
            daemon=True,
            name=f"mdrap-worker-{info.spec.name}",
        )
        info.thread = t
        t.start()

    def _monitor_loop(self) -> None:
        """Continuous supervision loop detecting crashed threads and executing restart policy."""
        while not self._stop_event.wait(0.05):
            now = time.time()
            to_restart: list[tuple[WorkerInfo, float]] = []

            with self._lock:
                if not self._running:
                    break

                for name, info in list(self._workers.items()):
                    if info.state in (
                        WorkerState.FAILED_FATAL,
                        WorkerState.RESTARTING,
                        WorkerState.STOPPED,
                    ):
                        continue

                    # If thread is dead or state is CRASHED but was not requested to stop
                    if (
                        info.state == WorkerState.CRASHED
                        or (info.thread and not info.thread.is_alive())
                    ) and not info.stop_event.is_set():
                        # Clean old restart timestamps outside window
                        window_start = now - info.spec.retry_window_s
                        info.restart_timestamps = [
                            ts for ts in info.restart_timestamps if ts > window_start
                        ]

                        # Check restart budget
                        if len(info.restart_timestamps) >= info.spec.max_retries:
                            info.state = WorkerState.FAILED_FATAL
                            err_msg = (
                                f"Worker '{name}' exceeded max retries "
                                f"({info.spec.max_retries} in {info.spec.retry_window_s}s). Last error: {info.last_error}"
                            )
                            logger.error("[supervisor] %s", err_msg)
                            self._handle_fatal(name, err_msg)
                            continue

                        # Execute bounded exponential backoff
                        retry_idx = len(info.restart_timestamps)
                        delay = min(
                            info.spec.restart_delay_base_s * (2 ** retry_idx),
                            info.spec.restart_delay_max_s,
                        )
                        info.restart_timestamps.append(now)
                        info.total_restarts += 1
                        info.state = WorkerState.RESTARTING

                        logger.info(
                            "[supervisor] Restarting worker '%s' (attempt %d/%d) in %.2fs",
                            name,
                            retry_idx + 1,
                            info.spec.max_retries,
                            delay,
                        )
                        to_restart.append((info, delay))

            for target_info, delay in to_restart:
                def do_restart(inf=target_info):
                    with self._lock:
                        if not self._stop_event.is_set() and inf.state == WorkerState.RESTARTING:
                            self._start_worker_thread(inf)

                timer = threading.Timer(delay, do_restart)
                timer.daemon = True
                timer.start()

    def _handle_fatal(self, worker_name: str, reason: str) -> None:
        """Escalate fatal worker failure to supervisor and optional callback."""
        info = self._workers.get(worker_name)
        if info and info.spec.is_critical:
            self._fatal_error = f"Critical worker '{worker_name}' failed: {reason}"
            if self._on_fatal_failure:
                try:
                    self._on_fatal_failure(worker_name, reason)
                except Exception as exc:
                    logger.exception("[supervisor] on_fatal_failure callback failed: %s", exc)
