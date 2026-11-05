"""Runtime Worker Supervision & Multi-Core Channel Coordination."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import CanonicalEvent, QualityStatus, RawEvent, Reason
from .partition import ChannelIngressSequencer, SpscRingQueue

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
                            info.spec.restart_delay_base_s * (2**retry_idx),
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
                        if (
                            not self._stop_event.is_set()
                            and inf.state == WorkerState.RESTARTING
                        ):
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
                    logger.exception(
                        "[supervisor] on_fatal_failure callback failed: %s", exc
                    )


# Multi-Core Channel Ingress (ADR-006)


def set_core_affinity(core_id: int) -> bool:
    """Set CPU core affinity for the current thread/process across platforms.

    Returns True if successfully set, False if unsupported or failed.
    """
    if core_id < 0:
        return False

    # Linux native affinity
    if hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, {core_id})
            return True
        except Exception as e:
            logger.debug(
                "Failed to set Linux thread affinity to core %d: %s", core_id, e
            )
            return False

    # Windows Win32 affinity
    if sys.platform == "win32":
        try:
            import ctypes

            # SetThreadAffinityMask takes a bitmask of allowed cores
            kernel32 = ctypes.windll.kernel32
            thread_handle = kernel32.GetCurrentThread()
            mask = 1 << (core_id % 64)
            result = kernel32.SetThreadAffinityMask(
                thread_handle, ctypes.c_size_t(mask)
            )
            return bool(result != 0)
        except Exception as e:
            logger.debug(
                "Failed to set Windows thread affinity to core %d: %s", core_id, e
            )
            return False

    return False


@dataclasses.dataclass(frozen=True)
class ChannelWorkerConfig:
    """Configuration for an isolated feed channel worker."""

    channel_id: str
    venue: str = "DEFAULT"
    core_id: Optional[int] = None
    queue_capacity: int = 65536
    shm_enabled: bool = False
    shm_name: Optional[str] = None
    max_jump_limit: int = 1_000_000
    heartbeat_timeout_s: float = 5.0


@dataclasses.dataclass
class ChannelWorkerMetrics:
    """Real-time performance and reliability metrics for a channel worker."""

    channel_id: str
    events_received: int = 0
    events_processed: int = 0
    events_dropped: int = 0
    sequence_gaps: int = 0
    duplicates: int = 0
    queue_depth: int = 0
    last_heartbeat: float = 0.0
    is_healthy: bool = True
    affinity_set: bool = False


class ChannelWorker:
    """Dedicated feed worker executing on an isolated CPU core.

    Maintains a private C11 SPSC queue, per-channel sequencer, and optional SHM publisher.
    """

    def __init__(
        self,
        config: ChannelWorkerConfig,
        event_handler: Optional[Callable[[RawEvent], Any]] = None,
    ) -> None:
        self.config = config
        self.inbound_queue = SpscRingQueue(capacity=config.queue_capacity)
        self.outbound_queue = SpscRingQueue(capacity=config.queue_capacity)
        self.sequencer = ChannelIngressSequencer(
            channel_id=config.channel_id,
            max_jump_limit=config.max_jump_limit,
        )
        self.event_handler = event_handler
        self.metrics = ChannelWorkerMetrics(
            channel_id=config.channel_id,
            last_heartbeat=time.time(),
        )

        self._shm_publisher: Optional[Any] = None
        if config.shm_enabled:
            try:
                from .shm import SHMPublisher

                shm_name = config.shm_name or f"mdrap_{config.channel_id.lower()}_v3"
                self._shm_publisher = SHMPublisher(name=shm_name)
            except Exception as e:
                logger.warning(
                    "Channel %s failed to initialize isolated SHM: %s",
                    config.channel_id,
                    e,
                )

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Launch worker execution thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"mdrap-worker-{self.config.channel_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop worker cleanly and release resources."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        if self._shm_publisher:
            try:
                self._shm_publisher.close()
            except Exception:
                pass
            self._shm_publisher = None

    def submit(self, event: RawEvent) -> bool:
        """Enqueue event to inbound ring queue without blocking.

        Returns False if queue is saturated (backpressure).
        """
        self.metrics.events_received += 1
        success = self.inbound_queue.offer(event)
        if not success:
            self.metrics.events_dropped += 1
        return success

    def _run_loop(self) -> None:
        """Worker hot path loop pinned to configured core."""
        if self.config.core_id is not None:
            self.metrics.affinity_set = set_core_affinity(self.config.core_id)

        spins = 0
        while self._running:
            self.metrics.last_heartbeat = time.time()
            self.metrics.queue_depth = self.inbound_queue.qsize()

            event = self.inbound_queue.poll()
            if event is None:
                spins += 1
                if spins > 10000:
                    time.sleep(0.001)
                    spins = 0
                continue

            spins = 0
            self._process_single_event(event)

    def _process_single_event(self, raw_event: RawEvent) -> None:
        """Process an individual raw market data event."""
        self.metrics.events_processed += 1

        # Check sequence integrity
        status, reason = self.sequencer.verify_sequence(raw_event)
        if reason == Reason.SEQUENCE_GAP.value:
            self.metrics.sequence_gaps += 1
        elif reason in (Reason.DUPLICATE.value, Reason.OUT_OF_ORDER.value):
            self.metrics.duplicates += 1

        # Execute custom event handler or forward to output queue
        processed = raw_event
        if self.event_handler:
            try:
                processed = self.event_handler(raw_event)
            except Exception as e:
                logger.error(
                    "Error in channel %s event handler: %s", self.config.channel_id, e
                )

        # Distribute via isolated SHM if enabled and event is CanonicalEvent
        if self._shm_publisher and isinstance(processed, CanonicalEvent):
            try:
                self._shm_publisher.publish(processed)
            except Exception as e:
                logger.debug(
                    "SHM publish error in channel %s: %s", self.config.channel_id, e
                )

        # Place on outbound queue for downstream aggregation
        self.outbound_queue.offer(processed)


class MultiChannelSupervisor:
    """Supervises multiple isolated channel workers for multicore scaling (ADR-006)."""

    def __init__(self, heartbeat_timeout_s: float = 5.0) -> None:
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.workers: Dict[str, ChannelWorker] = {}
        self._lock = threading.Lock()

    def register_channel(
        self,
        config: ChannelWorkerConfig,
        event_handler: Optional[Callable[[RawEvent], Any]] = None,
    ) -> ChannelWorker:
        """Register and configure a new channel worker."""
        with self._lock:
            if config.channel_id in self.workers:
                raise ValueError(f"Channel {config.channel_id} is already registered")
            worker = ChannelWorker(config=config, event_handler=event_handler)
            self.workers[config.channel_id] = worker
            return worker

    def start_all(self) -> None:
        """Start all registered workers."""
        with self._lock:
            for worker in self.workers.values():
                worker.start()

    def stop_all(self, timeout: float = 2.0) -> None:
        """Stop all workers cleanly."""
        with self._lock:
            for worker in self.workers.values():
                worker.stop(timeout=timeout)

    def submit(self, channel_id: str, event: RawEvent) -> bool:
        """Dispatch event directly to target channel's lock-free ring queue."""
        worker = self.workers.get(channel_id)
        if not worker:
            return False
        return worker.submit(event)

    def poll_completed(self, channel_id: str) -> Any | None:
        """Drain next processed event from a channel's output queue."""
        worker = self.workers.get(channel_id)
        if not worker:
            return None
        return worker.outbound_queue.poll()

    def check_health(self) -> Dict[str, bool]:
        """Verify heartbeat health for each channel worker."""
        now = time.time()
        health_status: Dict[str, bool] = {}
        with self._lock:
            for ch_id, worker in self.workers.items():
                elapsed = now - worker.metrics.last_heartbeat
                healthy = elapsed <= self.heartbeat_timeout_s
                worker.metrics.is_healthy = healthy
                health_status[ch_id] = healthy
        return health_status

    def aggregate_metrics(self) -> Dict[str, Any]:
        """Aggregate real-time metrics across all channel workers."""
        total_received = 0
        total_processed = 0
        total_dropped = 0
        total_gaps = 0
        total_dups = 0
        channel_details = {}

        with self._lock:
            for ch_id, worker in self.workers.items():
                m = worker.metrics
                total_received += m.events_received
                total_processed += m.events_processed
                total_dropped += m.events_dropped
                total_gaps += m.sequence_gaps
                total_dups += m.duplicates
                channel_details[ch_id] = {
                    "events_received": m.events_received,
                    "events_processed": m.events_processed,
                    "events_dropped": m.events_dropped,
                    "queue_depth": worker.inbound_queue.qsize(),
                    "sequence_gaps": m.sequence_gaps,
                    "duplicates": m.duplicates,
                    "is_healthy": m.is_healthy,
                    "affinity_set": m.affinity_set,
                }

        return {
            "total_channels": len(self.workers),
            "total_received": total_received,
            "total_processed": total_processed,
            "total_dropped": total_dropped,
            "total_gaps": total_gaps,
            "total_duplicates": total_dups,
            "channels": channel_details,
        }
