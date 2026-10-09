"""Canonical Runtime Architecture & Lifecycle Manager for MDRAP (Spec §18 & Phase 2).

Provides the unified, supervised lifecycle state machine for all production execution paths:
    UNINITIALIZED -> INITIALIZING -> READY -> RUNNING -> DRAINING -> STOPPED (or FAILED)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .engine import Engine
from .models import RawEvent, CanonicalEvent
from .storage import PersistenceMode, Store

logger = logging.getLogger("mdrap.runtime")

__stability__ = "stable"


class RuntimeState(str, Enum):
    """Lifecycle state machine for MDRAP production runtime."""

    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass
class RuntimeConfig:
    """Configuration for canonical production runtime."""

    db_path: str = "data/mdrap.db"
    wal_path: str | None = None
    persistence_mode: str = PersistenceMode.PRODUCTION_DURABLE.value
    staleness_threshold_s: float = 2.0
    drain_timeout_s: float = 5.0
    fsync_policy: str = "always"
    max_segment_bytes: int = 10 * 1024 * 1024
    max_payload_bytes: int = 1024 * 1024
    security: Any | None = None
    custom_projections: list[Any] = field(default_factory=list)


class Runtime:
    """Canonical production runtime orchestrator managing engine, lifecycle, and resources."""

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self._state: RuntimeState = RuntimeState.UNINITIALIZED
        self._lock = threading.RLock()
        self.engine: Engine | None = None
        self.store: Store | None = None
        self.init_error: str | None = None
        self._stop_event = threading.Event()
        self._start_time: float = 0.0
        self._stop_time: float = 0.0
        self._drain_success: bool = True
        self._supervised_tasks: list[Any] = []
        self._on_shutdown_callbacks: list[Callable[[], None]] = []

    @property
    def state(self) -> RuntimeState:
        with self._lock:
            return self._state

    def is_ready(self) -> bool:
        with self._lock:
            return self._state in (RuntimeState.READY, RuntimeState.RUNNING)

    def is_alive(self) -> bool:
        with self._lock:
            return self._state not in (RuntimeState.UNINITIALIZED, RuntimeState.FAILED, RuntimeState.STOPPED)

    @property
    def drain_success(self) -> bool:
        with self._lock:
            return self._drain_success

    def is_degraded(self) -> bool:
        with self._lock:
            if self._state == RuntimeState.FAILED:
                return True
            if self.engine is not None:
                m = self.engine.metrics()
                return m.get("health_status") != "HEALTHY"
            return False

    def register_shutdown_callback(self, cb: Callable[[], None]) -> None:
        """Register a cleanup callback executed during DRAINING."""
        with self._lock:
            self._on_shutdown_callbacks.append(cb)

    def initialize(self) -> None:
        """Initialize persistence, WAL, and engine. Clean up partially initialized resources on failure."""
        with self._lock:
            if self._state in (RuntimeState.READY, RuntimeState.RUNNING):
                return  # Idempotent

            self._state = RuntimeState.INITIALIZING
            self.init_error = None

            try:
                # 1. Initialize persistent SQLite store
                self.store = Store(
                    self.config.db_path,
                    persistence_mode=self.config.persistence_mode,
                )

                # 2. Determine WAL path
                wal_path = self.config.wal_path
                if not wal_path and self.config.db_path != ":memory:":
                    wal_path = f"{self.config.db_path}.wal"

                # 3. Instantiate Engine
                if self.config.persistence_mode == PersistenceMode.PRODUCTION_DURABLE.value:
                    if not wal_path:
                        raise ValueError("Production durable mode requires a valid wal_path on persistent media")
                    self.engine = Engine.open(
                        wal_path,
                        config={
                            "staleness_threshold_s": self.config.staleness_threshold_s,
                            "fsync_policy": self.config.fsync_policy,
                            "max_segment_bytes": self.config.max_segment_bytes,
                            "max_payload_bytes": self.config.max_payload_bytes,
                            "security": self.config.security,
                        },
                    )
                else:
                    self.engine = Engine(
                        staleness_threshold_s=self.config.staleness_threshold_s,
                        security=self.config.security,
                    )

                # 4. Attach SQLite store projection
                if self.store is not None:
                    self.engine.subscribe(self.store)

                # 5. Attach custom projections
                for proj in self.config.custom_projections:
                    self.engine.subscribe(proj)

                self._state = RuntimeState.READY
                logger.info("[runtime] MDRAP Runtime initialized successfully (state=READY)")

            except Exception as exc:
                self.init_error = f"Runtime initialization failed: {exc}"
                self._state = RuntimeState.FAILED
                logger.critical("[runtime] %s; cleaning up partially initialized resources", self.init_error)
                # Cleanup partially initialized handles
                self._cleanup_partial()
                raise

    def _cleanup_partial(self) -> None:
        """Clean up partially allocated handles on initialization failure."""
        if self.engine is not None:
            try:
                self.engine.close()
            except Exception:
                pass
            self.engine = None
        if self.store is not None:
            try:
                self.store.close()
            except Exception:
                pass
            self.store = None

    def start(self) -> None:
        """Transition runtime from READY to RUNNING."""
        with self._lock:
            if self._state == RuntimeState.UNINITIALIZED:
                self.initialize()
            if self._state == RuntimeState.RUNNING:
                return  # Idempotent
            if self._state != RuntimeState.READY:
                raise RuntimeError(f"Cannot start runtime from state {self._state.value}")

            self._state = RuntimeState.RUNNING
            self._start_time = time.time()
            self._stop_event.clear()
            logger.info("[runtime] MDRAP Runtime running (state=RUNNING)")

    def submit(self, events: RawEvent | list[RawEvent]) -> Any:
        """Submit events to canonical engine."""
        with self._lock:
            if self._state != RuntimeState.RUNNING:
                raise RuntimeError(f"Cannot submit events: runtime state is {self._state.value} (expected RUNNING)")
            if self.engine is None:
                raise RuntimeError("Engine not available")
            return self.engine.submit(events)

    def stop(self, drain_timeout_s: float | None = None) -> bool:
        """Gracefully drain accepted work, execute shutdown callbacks, and close resources."""
        timeout = drain_timeout_s if drain_timeout_s is not None else self.config.drain_timeout_s
        with self._lock:
            if self._state in (RuntimeState.STOPPED, RuntimeState.UNINITIALIZED):
                return True
            if self._state == RuntimeState.DRAINING:
                return self._drain_success

            self._state = RuntimeState.DRAINING
            logger.info("[runtime] Initiating graceful runtime shutdown (timeout=%.1fs)", timeout)

        self._stop_event.set()
        t_deadline = time.time() + timeout
        drain_clean = True

        # 1. Execute registered shutdown callbacks
        for cb in self._on_shutdown_callbacks:
            try:
                cb()
            except Exception as exc:
                logger.warning("[runtime] Shutdown callback error: %s", exc)

        # 2. Flush engine & WAL
        with self._lock:
            if self.engine is not None:
                try:
                    self.engine.flush()
                except Exception as exc:
                    logger.warning("[runtime] Error flushing engine during drain: %s", exc)
                    drain_clean = False

            if time.time() > t_deadline:
                drain_clean = False
                logger.error("[runtime] Shutdown drain deadline exceeded (incomplete drain)")

            # 3. Close engine & store
            self._cleanup_partial()
            self._stop_time = time.time()
            self._drain_success = drain_clean
            self._state = RuntimeState.STOPPED
            logger.info("[runtime] MDRAP Runtime stopped (clean_drain=%s)", drain_clean)

        return drain_clean

    def metrics(self) -> dict[str, Any]:
        """Expose operational telemetry, lifecycle state, and component health."""
        with self._lock:
            st = self._state.value
            eng_metrics = self.engine.metrics() if self.engine is not None else {}
            uptime = (time.time() - self._start_time) if self._start_time > 0 and self._state == RuntimeState.RUNNING else 0.0

            return {
                "state": st,
                "is_ready": self.is_ready(),
                "is_alive": self.is_alive(),
                "is_degraded": self.is_degraded(),
                "uptime_seconds": round(uptime, 2),
                "persistence_mode": self.config.persistence_mode,
                "init_error": self.init_error,
                "drain_success": self._drain_success,
                "engine": eng_metrics,
            }

    def __enter__(self) -> Runtime:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
