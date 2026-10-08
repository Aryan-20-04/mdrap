"""Deterministic Core Market Data Engine for MDRAP.

Implements pure, I/O-free state transitions:
    state, decision = engine.step(state, raw_event, clock)

Supports deterministic snapshot/restore for state persistence, replay,
and verifiable fault-injection recovery.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
import warnings
from typing import Any

from .clock import Clock, SystemClock
from .models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason

logger = logging.getLogger(__name__)

__stability__ = "stable"


def _is_finite_num(val: Any) -> bool:
    """Safe check if value is a finite number, protecting against huge ints that raise OverflowError."""
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return val.bit_length() <= 1024
    if isinstance(val, float):
        return math.isfinite(val)
    return False


DEFAULT_STALENESS_THRESHOLD_S: float = 0.05
DEFAULT_PRICE_STDDEV: float = 6.0
DEFAULT_PRICE_WINDOW: int = 50
DEFAULT_DEDUP_WINDOW_SIZE: int = 10_000


@dataclass
class EngineState:
    """Serializable, in-memory state of the deterministic engine fold."""

    event_count: int = 0
    sequence_state: dict[str, int] = field(default_factory=dict)
    dedup_window: list[str] = field(default_factory=list)
    dedup_set: set[str] = field(default_factory=set)
    price_windows: dict[str, list[float]] = field(default_factory=dict)
    bbo_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    reconciliation_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0})
    staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "sequence_state": dict(self.sequence_state),
            "dedup_window": list(self.dedup_window),
            "price_windows": {k: list(v) for k, v in self.price_windows.items()},
            "bbo_state": copy.deepcopy(self.bbo_state),
            "reconciliation_state": copy.deepcopy(self.reconciliation_state),
            "counts": dict(self.counts),
            "staleness_threshold_s": self.staleness_threshold_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineState:
        state = cls(
            event_count=data.get("event_count", 0),
            sequence_state=data.get("sequence_state", {}),
            dedup_window=data.get("dedup_window", []),
            dedup_set=set(data.get("dedup_window", [])),
            price_windows=data.get("price_windows", {}),
            bbo_state=data.get("bbo_state", {}),
            reconciliation_state=data.get("reconciliation_state", {}),
            counts=data.get("counts", {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}),
            staleness_threshold_s=data.get("staleness_threshold_s", DEFAULT_STALENESS_THRESHOLD_S),
        )
        return state


@dataclass
class EngineDecision:
    """Immutable outcome produced by a single step of the Engine fold."""

    offset: int
    event_id: str
    quality_status: QualityStatus
    reasons: list[str] = field(default_factory=list)
    canonical_event: CanonicalEvent | None = None
    quarantine_row: tuple | None = None
    is_duplicate: bool = False
    disagreement: bool = False


@dataclass
class EngineConfig:
    """Configuration options for Engine.open()."""

    db_path: str | None = None
    staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S
    clock: Clock | None = None
    fsync_policy: str = "always"
    max_segment_bytes: int = 10 * 1024 * 1024
    max_payload_bytes: int = 1024 * 1024  # 1 MB boundary cap (Finding N4)
    security: Any | None = None
    projections: list[Any] = field(default_factory=list)


class Engine:
    """Unified deterministic engine and streaming pipeline facade for MDRAP.

    Provides pure deterministic state transitions:
        state, decision = engine.step(state, raw_event, clock)

    As well as unified log-driven execution:
        engine = Engine.open(wal_path)
        engine.subscribe(projection)
        decision = engine.submit(raw_event)
    """

    def __init__(
        self,
        path: str | Path | None = None,
        staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S,
        clock: Clock | None = None,
        db_path: str | None = None,
        fsync_policy: str = "always",
        max_segment_bytes: int = 10 * 1024 * 1024,
        max_payload_bytes: int = 1024 * 1024,
        security: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # Check legacy environment variables (Finding 6)
        if "MDRAP_ASYNC_WRITER" in os.environ:
            warnings.warn(
                "Environment variable 'MDRAP_ASYNC_WRITER' is deprecated in MDRAP v3.0.0. "
                "Configure durability and write policy explicitly via IngestLog / Engine / SQLiteProjection configuration.",
                DeprecationWarning,
                stacklevel=2,
            )
        if "MDRAP_DISABLE_JOURNAL" in os.environ:
            warnings.warn(
                "Environment variable 'MDRAP_DISABLE_JOURNAL' is deprecated in MDRAP v3.0.0. "
                "IngestLog WAL is the mandatory durability boundary; use explicit configuration.",
                DeprecationWarning,
                stacklevel=2,
            )

        # Disallow silent kwargs forwarding (Requirement 1)
        valid_legacy_kwargs = {"durability", "quality", "async_writer", "journal"}
        for k in kwargs:
            if k not in valid_legacy_kwargs:
                raise TypeError(f"Engine.__init__() got an unexpected keyword argument '{k}'")
            else:
                warnings.warn(
                    f"Parameter '{k}' is deprecated in Engine; use explicit IngestLog and Projection configuration.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        self.staleness_threshold_s = staleness_threshold_s
        self.clock = clock if clock is not None else SystemClock()
        self.max_payload_bytes = max_payload_bytes
        self.security = security
        self.state = self.create_initial_state()
        self._projections: list[Any] = []
        self._default_sqlite_proj: Any = None
        self.log: Any = None
        self._lock = threading.RLock()

        if path is not None:
            from .ingestlog import IngestLog

            self.log = IngestLog(
                str(path),
                fsync_policy=fsync_policy,
                max_segment_bytes=max_segment_bytes,
            )
            # Bounded startup (Phase 3): check for snapshot in WAL directory to avoid O(history) replay
            replay_from = 0
            snapshot_file = os.path.join(self.log.log_dir, "snapshot.json")
            if os.path.exists(snapshot_file):
                try:
                    with open(snapshot_file, "r", encoding="utf-8") as f_snap:
                        snap_obj = json.load(f_snap)
                    snap_state_dict = snap_obj.get("state")
                    snap_offset = snap_obj.get("offset", -1)
                    if snap_state_dict and snap_offset >= 0:
                        self.state = EngineState.from_dict(snap_state_dict)
                        replay_from = snap_offset + 1
                        logger.info(
                            "[engine] Restored snapshot at offset %d (event_count=%d)",
                            snap_offset,
                            self.state.event_count,
                        )
                except Exception as snap_exc:
                    logger.warning("[engine] Failed to load snapshot %s: %s; replaying from start", snapshot_file, snap_exc)

            # Catch up state only from uncompacted log records since snapshot
            for off, raw in self.log.iter_from(replay_from):
                self.state, _ = self.step(self.state, raw, self.clock, offset=off)

        if db_path is not None:
            from .projection import SQLiteProjection

            proj = SQLiteProjection(db_path)
            self.subscribe(proj)
            self._default_sqlite_proj = proj

    @classmethod
    def open(
        cls,
        path: str | Path,
        config: Any | None = None,
    ) -> Engine:
        """Open a high-level Engine instance with durable IngestLog WAL at path."""
        staleness = DEFAULT_STALENESS_THRESHOLD_S
        clock = None
        fsync_policy = "always"
        max_segment_bytes = 10 * 1024 * 1024
        max_payload_bytes = 1024 * 1024
        db_path = None
        security = None
        projections = []

        if config is not None:
            if isinstance(config, dict):
                staleness = config.get("staleness_threshold_s", staleness)
                clock = config.get("clock", clock)
                fsync_policy = config.get("fsync_policy", fsync_policy)
                max_segment_bytes = config.get("max_segment_bytes", max_segment_bytes)
                max_payload_bytes = config.get("max_payload_bytes", max_payload_bytes)
                db_path = config.get("db_path", db_path)
                security = config.get("security", security)
                projections = config.get("projections", [])
            else:
                staleness = getattr(config, "staleness_threshold_s", staleness)
                clock = getattr(config, "clock", clock)
                fsync_policy = getattr(config, "fsync_policy", fsync_policy)
                max_segment_bytes = getattr(config, "max_segment_bytes", max_segment_bytes)
                max_payload_bytes = getattr(config, "max_payload_bytes", max_payload_bytes)
                db_path = getattr(config, "db_path", db_path)
                security = getattr(config, "security", security)
                projections = getattr(config, "projections", [])

        engine = cls(
            path=path,
            staleness_threshold_s=staleness,
            clock=clock,
            db_path=db_path,
            fsync_policy=fsync_policy,
            max_segment_bytes=max_segment_bytes,
            max_payload_bytes=max_payload_bytes,
            security=security,
        )

        for proj in projections:
            engine.subscribe(proj)

        return engine

    def process_one(self, raw: RawEvent) -> CanonicalEvent | None:
        """Process a single RawEvent and return the resulting CanonicalEvent if valid/suspicious, or None if invalid."""
        dec = self.submit(raw)
        return dec.canonical_event if dec else None

    def subscribe(self, projection: Any, from_offset: int = 0) -> None:
        """Register a projection consumer and catch up from log offset."""
        with self._lock:
            if projection not in self._projections:
                self._projections.append(projection)

            start_off = from_offset
            if hasattr(projection, "checkpoint"):
                try:
                    cp = projection.checkpoint()
                    if cp >= 0 and start_off <= cp:
                        start_off = cp + 1
                except Exception:
                    pass

            # Catch up projection from start_off if log exists
            if self.log is not None and self.log.next_offset > start_off:
                batch = []
                replay_state = self.create_initial_state()
                for off, raw in self.log.iter_from(0):
                    if off < start_off:
                        replay_state, _ = self.step(replay_state, raw, self.clock, offset=off)
                    else:
                        replay_state, dec = self.step(replay_state, raw, self.clock, offset=off)
                        batch.append(dec)
                        if len(batch) >= 2000:
                            projection.apply(batch, batch[-1].offset)
                            batch = []
                if batch:
                    projection.apply(batch, batch[-1].offset)

    def submit(
        self, events: RawEvent | list[RawEvent]
    ) -> EngineDecision | list[EngineDecision]:
        """Append raw event(s) to IngestLog, fold through state, dispatch to projections, and return aligned result(s)."""
        if isinstance(events, RawEvent):
            is_single = True
            event_list = [events]
        elif isinstance(events, (list, tuple)):
            is_single = False
            event_list = list(events)
        else:
            raise TypeError(
                f"Expected RawEvent or list[RawEvent], got {type(events).__name__}"
            )

        if not event_list:
            return [] if not is_single else None  # type: ignore[return-value]

        with self._lock:
            decisions: list[EngineDecision] = []
            offsets: list[int] = []
            valid_for_wal: list[RawEvent] = []
            raw_to_offset: list[tuple[RawEvent, int]] = []

            for raw in event_list:
                # Stamp receive timestamp before append to ensure deterministic replay (C2)
                if not raw.receive_timestamp:
                    raw.receive_timestamp = self.clock.now()

                # Determine offset before append so deterministic raw_id is stamped and persisted in WAL (N1)
                offset = (self.log.next_offset + len(valid_for_wal)) if self.log is not None else (self.state.event_count + len(decisions))
                if not raw.raw_id:
                    raw.raw_id = f"raw_{raw.source}_{offset}"

                # Enforce payload size limit at Engine boundary to protect WAL from stalling (N4)
                if self.max_payload_bytes > 0:
                    payload = raw.payload
                    p_len = len(payload) if isinstance(payload, (str, bytes)) else (
                        len(json.dumps(payload, default=str)) if isinstance(payload, dict) else len(str(payload))
                    )
                    if p_len > self.max_payload_bytes:
                        q_id = f"q_{raw.source}_{offset}"
                        q_row = (
                            q_id,
                            "OVERSIZE",
                            raw.source,
                            QualityStatus.INVALID.value,
                            json.dumps([f"Payload size {p_len} bytes exceeds limit {self.max_payload_bytes} bytes"]),
                            json.dumps({"size": p_len, "truncated": str(payload)[:256]}),
                            raw.receive_timestamp,
                        )
                        self.state.event_count += 1
                        self.state.counts["INVALID"] += 1
                        dec = EngineDecision(
                            offset=offset,
                            event_id=q_id,
                            quality_status=QualityStatus.INVALID,
                            reasons=[Reason.SCHEMA_VIOLATION.value],
                            quarantine_row=q_row,
                        )
                        decisions.append(dec)
                        offsets.append(offset)
                        continue

                valid_for_wal.append(raw)
                raw_to_offset.append((raw, offset))

            if self.log is not None and valid_for_wal:
                allocated = self.log.append_batch(valid_for_wal)
                assert allocated == [off for _, off in raw_to_offset], "WAL allocated offsets diverged"

            for raw, offset in raw_to_offset:
                offsets.append(offset)
                self.state, dec = self.step(self.state, raw, self.clock, offset=offset)
                decisions.append(dec)

            # Dispatch to subscribed projections atomically
            if self._projections and decisions:
                last_offset = offsets[-1]
                for proj in self._projections:
                    proj.apply(decisions, last_offset)

            if is_single:
                return decisions[0]
            return decisions

    def replay(
        self,
        from_offset: int = 0,
        to_offset: int | None = None,
        projection: Any | None = None,
    ) -> list[EngineDecision]:
        """Replay events from WAL from_offset up to to_offset."""
        if self.log is None:
            return []

        decisions: list[EngineDecision] = []
        replay_state = self.create_initial_state()

        for off, raw in self.log.iter_from(0):
            if off < from_offset:
                replay_state, _ = self.step(replay_state, raw, self.clock, offset=off)
                continue
            if to_offset is not None and off > to_offset:
                break
            replay_state, dec = self.step(replay_state, raw, self.clock, offset=off)
            decisions.append(dec)

        if projection is not None and decisions:
            projection.apply(decisions, decisions[-1].offset)

        return decisions

    def process(self, raw: RawEvent) -> CanonicalEvent | None:
        """Process a single raw event through the validation pipeline."""
        dec = self.submit(raw)
        return dec.canonical_event if isinstance(dec, EngineDecision) else None

    def process_one(self, raw: RawEvent) -> CanonicalEvent | None:
        """Alias for process()."""
        return self.process(raw)

    def process_batch(self, raw_events: list[RawEvent]) -> list[CanonicalEvent]:
        """Process a batch of raw events through the engine, returning strictly aligned canonical events."""
        decisions = self.submit(raw_events)
        if isinstance(decisions, list):
            return [d.canonical_event for d in decisions if d.canonical_event is not None]
        return []

    def flush(self, wait: bool = True) -> None:
        """Flush pending writes to persistent log and projections."""
        if self.log is not None:
            self.log.flush()

    def query(self, instrument: str, limit: int = 100) -> list[dict]:
        """Query recent canonical ticks for an instrument from attached SQLite projection."""
        if self._default_sqlite_proj is not None:
            with self._default_sqlite_proj._lock:
                cur = self._default_sqlite_proj._conn.cursor()
                cur.execute(
                    "SELECT * FROM canonical_events WHERE instrument_id = ? ORDER BY exchange_timestamp DESC LIMIT ?;",
                    (instrument, limit),
                )
                col_names = [desc[0] for desc in cur.description]
                return [dict(zip(col_names, row)) for row in cur.fetchall()]
        return []

    def metrics(self) -> dict[str, Any]:
        """Return engine state counts, durability lag, and health summary."""
        log_head = self.log.next_offset if self.log is not None else self.state.event_count
        log_lag = 0  # IngestLog fsyncs synchronously per policy

        max_proj_lag = 0
        proj_stats = []
        for proj in self._projections:
            name = getattr(proj, "name", "projection")
            cp = proj.checkpoint() if hasattr(proj, "checkpoint") else -1
            lag = max(0, log_head - (cp + 1)) if cp >= 0 else log_head
            if lag > max_proj_lag:
                max_proj_lag = lag
            proj_stats.append({"name": name, "checkpoint": cp, "lag": lag})

        if max_proj_lag == 0 and log_lag == 0:
            health_status = "HEALTHY"
        elif max_proj_lag < 500:
            health_status = "DEGRADED"
        else:
            health_status = "UNHEALTHY"

        return {
            "processed": self.state.event_count,
            "counts": dict(self.state.counts),
            "valid": self.state.counts.get("VALID", 0),
            "suspicious": self.state.counts.get("SUSPICIOUS", 0),
            "invalid": self.state.counts.get("INVALID", 0),
            "log_head_offset": log_head,
            "log_lag": log_lag,
            "projection_lag": max_proj_lag,
            "projections": proj_stats,
            "health_status": health_status,
        }

    def close(self) -> None:
        """Release WAL and projection resources."""
        if self.log is not None:
            self.log.close()
            self.log = None
        for proj in self._projections:
            if hasattr(proj, "close"):
                try:
                    proj.close()
                except Exception:
                    pass
        self._projections.clear()
        self._default_sqlite_proj = None

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @classmethod
    def verify(cls, wal_path: str | Path) -> dict[str, Any]:
        """Verify integrity of IngestLog segments at wal_path."""
        from .ingestlog import IngestLog

        return IngestLog.verify(str(wal_path))

    @classmethod
    def salvage(cls, wal_path: str | Path, backup: bool = True) -> dict[str, Any]:
        """Salvage corrupted IngestLog segments at wal_path."""
        from .ingestlog import IngestLog

        return IngestLog.salvage(str(wal_path), backup=backup)

    def create_initial_state(self) -> EngineState:
        return EngineState(staleness_threshold_s=self.staleness_threshold_s)

    def step(
        self,
        state: EngineState,
        raw_event: RawEvent,
        clock: Clock,
        offset: int | None = None,
    ) -> tuple[EngineState, EngineDecision]:
        """Apply a single deterministic state transition: (state, raw_event, clock) -> (state', decision)."""
        prev_event_count = state.event_count
        state_backup = copy.deepcopy(state)
        try:
            return self._step_internal(state, raw_event, clock, offset=offset)
        except Exception as exc:
            # Restore state from clean backup to prevent partial mutation state-inconsistency (N3)
            state = state_backup
            state.event_count = prev_event_count + 1
            state.counts["INVALID"] += 1
            # Rate limit warnings to prevent log-flooding attack vectors
            if state.counts["INVALID"] <= 10 or state.counts["INVALID"] % 1000 == 0:
                logger.warning(
                    "[engine] Unhandled exception in step safely quarantined (#%d): %s",
                    state.counts["INVALID"],
                    exc,
                )
            if offset is None:
                offset = prev_event_count
            now_ts = clock.now()
            recv_ts = getattr(raw_event, "receive_timestamp", None) or now_ts
            src = getattr(raw_event, "source", "UNKNOWN")
            event_id = getattr(raw_event, "raw_id", None) or f"q_{src}_{offset}"
            q_row = (
                event_id,
                "MALFORMED",
                src,
                QualityStatus.INVALID.value,
                json.dumps([f"Unhandled engine exception: {type(exc).__name__}: {exc}"]),
                json.dumps(str(getattr(raw_event, "payload", ""))),
                recv_ts,
            )
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.SCHEMA_VIOLATION.value],
                quarantine_row=q_row,
            )

    def _step_internal(
        self,
        state: EngineState,
        raw_event: RawEvent,
        clock: Clock,
        offset: int | None = None,
    ) -> tuple[EngineState, EngineDecision]:
        if offset is None:
            offset = state.event_count

        state.event_count += 1
        now_ts = clock.now()
        recv_ts = raw_event.receive_timestamp if raw_event.receive_timestamp else now_ts

        # 1. Payload validation (Finding 5, Finding 6)
        payload = raw_event.payload
        if not isinstance(payload, dict):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                "MALFORMED",
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Payload is not a dictionary"]),
                json.dumps(str(payload)),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            decision = EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.SCHEMA_VIOLATION.value],
                quarantine_row=q_row,
            )
            return state, decision

        # 1b. Security sanitization and cryptographic HMAC verification
        if self.security is not None:
            is_valid, err_msg = self.security.sanitize_payload(payload)
            if not is_valid:
                event_id = f"q_{raw_event.source}_{offset}"
                q_row = (
                    event_id,
                    "MALFORMED",
                    raw_event.source,
                    QualityStatus.INVALID.value,
                    json.dumps([f"Payload failed security sanitization: {err_msg}"]),
                    json.dumps(str(payload)),
                    recv_ts,
                )
                state.counts["INVALID"] += 1
                return state, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.MALFORMED.value],
                    quarantine_row=q_row,
                )
            if self.security.hmac_required(raw_event.source) or "signature" in payload:
                sig = payload.get("signature")
                if not sig or not self.security.verify_payload(
                    raw_event.source, payload, sig
                ):
                    event_id = f"q_{raw_event.source}_{offset}"
                    q_row = (
                        event_id,
                        str(payload.get("instrument", "UNKNOWN")),
                        raw_event.source,
                        QualityStatus.INVALID.value,
                        json.dumps(["Cryptographic HMAC verification failed: missing or tampered signature"]),
                        json.dumps(payload, default=str),
                        recv_ts,
                    )
                    state.counts["INVALID"] += 1
                    return state, EngineDecision(
                        offset=offset,
                        event_id=event_id,
                        quality_status=QualityStatus.INVALID,
                        reasons=[Reason.SECURITY_REJECT.value],
                        quarantine_row=q_row,
                    )

        # 2. Schema field validation
        instrument = payload.get("instrument")
        event_type_str = payload.get("event_type", "TRADE")
        if (
            payload.get("drop_reason") == "BACKPRESSURE_DROP"
            or event_type_str == "TOMBSTONE_DROPPED"
        ):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument or "UNKNOWN",
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Event dropped due to ingress queue backpressure"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.BACKPRESSURE_DROP.value],
                quarantine_row=q_row,
            )
        if not instrument or not isinstance(instrument, str):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                "UNKNOWN",
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Missing or invalid instrument string"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.SCHEMA_VIOLATION.value],
                quarantine_row=q_row,
            )

        # Sequence validation (signed int64)
        seq = payload.get("sequence")
        if seq is not None:
            if not isinstance(seq, int) or isinstance(seq, bool) or not (-9223372036854775808 <= seq <= 9223372036854775807):
                event_id = f"q_{raw_event.source}_{offset}"
                q_row = (
                    event_id,
                    instrument,
                    raw_event.source,
                    QualityStatus.INVALID.value,
                    json.dumps([f"Sequence number out of range: {seq}"]),
                    json.dumps(payload, default=str),
                    recv_ts,
                )
                state.counts["INVALID"] += 1
                return state, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                )

        # Exchange timestamp validation
        exch_ts = payload.get("exchange_ts")
        if exch_ts is None:
            exch_ts = recv_ts
        elif not _is_finite_num(exch_ts):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps([f"Exchange timestamp not finite: {exch_ts}"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.SCHEMA_VIOLATION.value],
                quarantine_row=q_row,
            )

        # Numeric field finite validation
        for num_field in ("price", "quantity", "bid", "ask", "bid_size", "ask_size"):
            val = payload.get(num_field)
            if val is not None and not _is_finite_num(val):
                event_id = f"q_{raw_event.source}_{offset}"
                q_row = (
                    event_id,
                    instrument,
                    raw_event.source,
                    QualityStatus.INVALID.value,
                    json.dumps([f"Field {num_field} not a finite number: {val}"]),
                    json.dumps(payload, default=str),
                    recv_ts,
                )
                state.counts["INVALID"] += 1
                return state, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                )

        # 3. Deterministic Event Identity (Week 4)
        # Derive durable ID from log offset and source identity
        if raw_event.raw_id:
            event_id = str(raw_event.raw_id)
        elif seq is not None:
            event_id = f"evt_{raw_event.source}_{instrument}_{seq}"
        else:
            event_id = f"evt_{raw_event.source}_{instrument}_{offset}"

        # 4. Quality Rules Evaluation
        status = QualityStatus.VALID
        reasons: list[str] = []
        is_duplicate = False

        slot_key = f"{raw_event.source}:{instrument}"

        # Sequenced deduplication & gap detection
        if seq is not None:
            last_seq = state.sequence_state.get(slot_key)
            if last_seq is not None:
                if seq == last_seq:
                    is_duplicate = True
                    status = QualityStatus.INVALID
                    reasons.append(Reason.DUPLICATE.value)
                elif seq < last_seq:
                    status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.OUT_OF_ORDER.value)
                elif seq > last_seq + 1:
                    status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.SEQUENCE_GAP.value)
            state.sequence_state[slot_key] = max(last_seq if last_seq is not None else -1, seq)
        else:
            # Unsequenced deduplication window (N2)
            # Check for business trade/message ID if present
            biz_id = (
                payload.get("trade_id")
                or payload.get("msg_id")
                or payload.get("message_id")
                or payload.get("exec_id")
                or payload.get("order_id")
                or payload.get("id")
            )
            if biz_id is not None:
                dedup_fingerprint = f"{raw_event.source}:{instrument}:{event_type_str}:id={biz_id}"
            else:
                dedup_fingerprint = f"{raw_event.source}:{instrument}:{event_type_str}:{exch_ts}:{payload.get('price')}:{payload.get('quantity')}:{payload.get('bid')}:{payload.get('ask')}"
            if dedup_fingerprint in state.dedup_set:
                is_duplicate = True
                status = QualityStatus.INVALID
                reasons.append(Reason.DUPLICATE.value)
            else:
                state.dedup_set.add(dedup_fingerprint)
                state.dedup_window.append(dedup_fingerprint)
                if len(state.dedup_window) > DEFAULT_DEDUP_WINDOW_SIZE:
                    evicted = state.dedup_window.pop(0)
                    state.dedup_set.discard(evicted)

        # Staleness check
        if status != QualityStatus.INVALID:
            if recv_ts - exch_ts > state.staleness_threshold_s:
                if status == QualityStatus.VALID:
                    status = QualityStatus.SUSPICIOUS
                reasons.append(Reason.STALE.value)

        # Quote crossed book check
        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is not None and ask is not None and bid > ask:
            status = QualityStatus.INVALID
            reasons.append(Reason.CROSSED_QUOTE.value)

        # Price sanity check (Welford / rolling price corridor)
        price = payload.get("price")
        if price is not None and status != QualityStatus.INVALID:
            prices = state.price_windows.setdefault(slot_key, [])
            if len(prices) >= 20:
                mean = sum(prices) / len(prices)
                variance = sum((p - mean) ** 2 for p in prices) / len(prices)
                std = math.sqrt(variance)
                sigma_floor = mean * 0.0001
                effective_std = max(std, sigma_floor)
                if effective_std > 0 and abs(price - mean) > (DEFAULT_PRICE_STDDEV * effective_std):
                    if status == QualityStatus.VALID:
                        status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.PRICE_ANOMALY.value)
            prices.append(price)
            if len(prices) > DEFAULT_PRICE_WINDOW:
                prices.pop(0)

        # Update BBO state per source
        if bid is not None or ask is not None:
            source_quotes = state.bbo_state.setdefault(instrument, {})
            current_src = source_quotes.setdefault(raw_event.source, {})
            if bid is not None:
                current_src["bid"] = float(bid)
            if ask is not None:
                current_src["ask"] = float(ask)

        # Cross-feed reconciliation
        disagreement = False
        if price is not None:
            recent_inst = state.reconciliation_state.setdefault(instrument, {})
            recent_inst[raw_event.source] = price
            prices_across = list(recent_inst.values())
            if len(prices_across) >= 2:
                spread = max(prices_across) - min(prices_across)
                avg_p = sum(prices_across) / len(prices_across)
                if avg_p > 0 and (spread / avg_p) > 0.01:  # 1% cross-feed disagreement
                    disagreement = True
                    reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
                    if status == QualityStatus.VALID:
                        status = QualityStatus.SUSPICIOUS

        state.counts[status.name] += 1

        if status == QualityStatus.INVALID:
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(reasons),
                json.dumps(payload, default=str),
                recv_ts,
            )
            decision = EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=status,
                reasons=reasons,
                canonical_event=None,
                quarantine_row=q_row,
                is_duplicate=is_duplicate,
                disagreement=disagreement,
            )
            return state, decision

        try:
            ev_type = EventType(event_type_str)
        except ValueError:
            ev_type = EventType.UNKNOWN

        canonical_event = CanonicalEvent(
            event_id=event_id,
            instrument_id=instrument,
            event_type=ev_type,
            exchange_timestamp=float(exch_ts),
            receive_timestamp=float(recv_ts),
            processing_timestamp=float(now_ts),
            source=raw_event.source,
            sequence_number=seq,
            price=price,
            quantity=payload.get("quantity"),
            bid_price=bid,
            ask_price=ask,
            bid_size=payload.get("bid_size"),
            ask_size=payload.get("ask_size"),
            quality_status=status,
            reasons=reasons,
            raw_id=raw_event.raw_id,
        )

        decision = EngineDecision(
            offset=offset,
            event_id=event_id,
            quality_status=status,
            reasons=reasons,
            canonical_event=canonical_event,
            quarantine_row=None,
            is_duplicate=is_duplicate,
            disagreement=disagreement,
        )

        return state, decision

    def snapshot(self, state: EngineState) -> bytes:
        """Create a deterministic byte snapshot of the current state."""
        data = state.to_dict()
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def restore(self, snapshot_bytes: bytes) -> EngineState:
        """Restore state from snapshot bytes."""
        data = json.loads(snapshot_bytes.decode("utf-8"))
        return EngineState.from_dict(data)

    def create_snapshot(self) -> bytes:
        """Create a deterministic byte snapshot of the current engine state."""
        with self._lock:
            return self.snapshot(self.state)

    def restore_snapshot(self, snapshot_bytes: bytes) -> None:
        """Restore engine state from snapshot bytes."""
        with self._lock:
            self.state = self.restore(snapshot_bytes)

    def delete_segments_before(self, offset: int) -> int:
        """Prune segments in the backing IngestLog strictly before offset."""
        with self._lock:
            if self.log is not None and hasattr(self.log, "delete_segments_before"):
                return self.log.delete_segments_before(offset)
            return 0

    def save_snapshot(self, prune_obsolete_segments: bool = True) -> dict[str, Any]:
        """Save a snapshot of current engine state to the WAL directory for bounded O(tail) startup."""
        with self._lock:
            if self.log is None:
                raise RuntimeError("Cannot save snapshot without an active IngestLog")

            last_offset = self.state.event_count - 1
            snap_data = {
                "version": 1,
                "offset": last_offset,
                "timestamp": self.clock.now(),
                "event_count": self.state.event_count,
                "state": self.state.to_dict(),
            }
            snap_dir = self.log.log_dir
            tmp_path = os.path.join(snap_dir, f"snapshot.json.tmp.{time.time_ns()}")
            final_path = os.path.join(snap_dir, "snapshot.json")

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(snap_data, f, sort_keys=True, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, final_path)

            pruned = 0
            if prune_obsolete_segments and last_offset >= 0:
                pruned = self.delete_segments_before(last_offset)

            return {
                "snapshot_offset": last_offset,
                "path": final_path,
                "segments_pruned": pruned,
            }
