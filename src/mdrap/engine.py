"""Deterministic Core Market Data Engine for MDRAP.

Implements pure, I/O-free state transitions:
    state, decision = engine.step(state, raw_event, clock)

Supports deterministic snapshot/restore for state persistence, replay,
and verifiable fault-injection recovery.
"""

from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass, field
import hashlib
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
    t = type(val)
    if t is float:
        return math.isfinite(val)
    if t is int:
        return val.bit_length() <= 1024
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
    dedup_window: Any = field(default_factory=deque)
    dedup_set: set[str] = field(default_factory=set)
    price_windows: dict[str, list[float]] = field(default_factory=dict)
    bbo_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    reconciliation_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(
        default_factory=lambda: {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
    )
    staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S
    epoch_state: dict[str, int] = field(default_factory=dict)
    epoch_ts: dict[str, float] = field(default_factory=dict)
    session_state: dict[str, str] = field(default_factory=dict)
    _price_stats: dict[str, tuple[float, float, int]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.dedup_window, deque):
            self.dedup_window = deque(self.dedup_window)

    @property
    def total_events(self) -> int:
        return self.event_count

    @property
    def global_sequence(self) -> int:
        return max(self.sequence_state.values(), default=self.event_count)

    def clone(self) -> EngineState:
        """Fast clone for transactional isolation and zero-allocation rollback."""
        return EngineState(
            event_count=self.event_count,
            sequence_state=dict(self.sequence_state),
            dedup_window=deque(self.dedup_window),
            dedup_set=set(self.dedup_set),
            price_windows={k: list(v) for k, v in self.price_windows.items()},
            bbo_state={
                k: {src: dict(q) for src, q in v.items()}
                for k, v in self.bbo_state.items()
            },
            reconciliation_state={
                k: dict(v) for k, v in self.reconciliation_state.items()
            },
            counts=dict(self.counts),
            staleness_threshold_s=self.staleness_threshold_s,
            epoch_state=dict(self.epoch_state),
            epoch_ts=dict(self.epoch_ts),
            session_state=dict(self.session_state),
            _price_stats={k: v for k, v in self._price_stats.items()},
        )

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
            "epoch_state": dict(self.epoch_state),
            "epoch_ts": dict(self.epoch_ts),
            "session_state": dict(self.session_state),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineState:
        dedup_win = data.get("dedup_window", [])
        dedup_items = [tuple(x) if isinstance(x, list) else x for x in dedup_win]
        state = cls(
            event_count=data.get("event_count", 0),
            sequence_state=data.get("sequence_state", {}),
            dedup_window=deque(dedup_items),
            dedup_set=set(dedup_items),
            price_windows=data.get("price_windows", {}),
            bbo_state=data.get("bbo_state", {}),
            reconciliation_state=data.get("reconciliation_state", {}),
            counts=data.get("counts", {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}),
            staleness_threshold_s=data.get(
                "staleness_threshold_s", DEFAULT_STALENESS_THRESHOLD_S
            ),
            epoch_state=data.get("epoch_state", {}),
            epoch_ts=data.get("epoch_ts", {}),
            session_state=data.get("session_state", {}),
        )
        return state


_OP_EVENT_COUNT = 1
_OP_COUNT = 2
_OP_DICT_KEY = 3
_OP_DEDUP = 4
_OP_BBO = 5
_OP_RECON = 6


class Transaction:
    """Zero-allocation bounded-cost undo journal tracking touched state keys for O(1) rollback."""

    __slots__ = ("state", "_actions", "_committed")

    def __init__(self, state: EngineState) -> None:
        self.state = state
        self._actions: list[tuple[Any, ...]] = []
        self._committed: bool = False

    def record_event_count(self) -> None:
        self._actions.append((_OP_EVENT_COUNT, self.state.event_count))

    def record_count(self, status_name: str) -> None:
        self._actions.append(
            (_OP_COUNT, status_name, self.state.counts.get(status_name, 0))
        )

    def record_sequence(self, slot_key: str) -> None:
        existed = slot_key in self.state.sequence_state
        old_val = self.state.sequence_state.get(slot_key)
        self._actions.append(
            (_OP_DICT_KEY, self.state.sequence_state, slot_key, existed, old_val)
        )

    def record_price_window(self, slot_key: str) -> None:
        pw = self.state.price_windows
        had = slot_key in pw
        old_val = list(pw[slot_key]) if had else None
        self._actions.append((_OP_DICT_KEY, pw, slot_key, had, old_val))

    def record_bbo(self, instrument: str, source: str) -> None:
        bbo = self.state.bbo_state
        had_inst = instrument in bbo
        had_src = had_inst and source in bbo[instrument]
        old_quote = dict(bbo[instrument][source]) if had_src else None
        self._actions.append(
            (_OP_BBO, instrument, source, had_inst, had_src, old_quote)
        )

    def record_reconciliation(self, instrument: str, source: str) -> None:
        recon = self.state.reconciliation_state
        had_inst = instrument in recon
        had_src = had_inst and source in recon[instrument]
        old_val = recon[instrument][source] if had_src else None
        self._actions.append(
            (_OP_RECON, instrument, source, had_inst, had_src, old_val)
        )

    def record_dedup(self, added: str | None, evicted: str | None) -> None:
        self._actions.append((_OP_DEDUP, added, evicted))

    def record_epoch(self, stream_key: str) -> None:
        had_ep = stream_key in self.state.epoch_state
        old_ep = self.state.epoch_state.get(stream_key)
        self._actions.append(
            (_OP_DICT_KEY, self.state.epoch_state, stream_key, had_ep, old_ep)
        )
        had_ts = stream_key in self.state.epoch_ts
        old_ts = self.state.epoch_ts.get(stream_key)
        self._actions.append(
            (_OP_DICT_KEY, self.state.epoch_ts, stream_key, had_ts, old_ts)
        )
        had_sess = stream_key in self.state.session_state
        old_sess = self.state.session_state.get(stream_key)
        self._actions.append(
            (_OP_DICT_KEY, self.state.session_state, stream_key, had_sess, old_sess)
        )

    def rollback_to(self, mark: int) -> None:
        """Execute undo actions recorded from mark onward in reverse order."""
        if self._committed:
            return
        while len(self._actions) > mark:
            item = self._actions.pop()
            op = item[0]
            if op == _OP_EVENT_COUNT:
                self.state.event_count = item[1]
            elif op == _OP_COUNT:
                self.state.counts[item[1]] = item[2]
            elif op == _OP_DICT_KEY:
                d, k, existed, old_val = item[1], item[2], item[3], item[4]
                if existed:
                    d[k] = old_val
                else:
                    d.pop(k, None)
                if d is self.state.price_windows:
                    self.state._price_stats.pop(k, None)
            elif op == _OP_BBO:
                inst, src, had_inst, had_src, old_quote = (
                    item[1],
                    item[2],
                    item[3],
                    item[4],
                    item[5],
                )
                if not had_inst:
                    self.state.bbo_state.pop(inst, None)
                elif not had_src:
                    if inst in self.state.bbo_state:
                        self.state.bbo_state[inst].pop(src, None)
                else:
                    self.state.bbo_state[inst][src] = old_quote
            elif op == _OP_RECON:
                inst, src, had_inst, had_src, old_val = (
                    item[1],
                    item[2],
                    item[3],
                    item[4],
                    item[5],
                )
                if not had_inst:
                    self.state.reconciliation_state.pop(inst, None)
                elif not had_src:
                    if inst in self.state.reconciliation_state:
                        self.state.reconciliation_state[inst].pop(src, None)
                else:
                    self.state.reconciliation_state[inst][src] = old_val
            elif op == _OP_DEDUP:
                added, evicted = item[1], item[2]
                if added is not None:
                    if self.state.dedup_window:
                        self.state.dedup_window.pop()
                    self.state.dedup_set.discard(added)
                if evicted is not None:
                    self.state.dedup_window.appendleft(evicted)
                    self.state.dedup_set.add(evicted)

    def rollback(self) -> None:
        """Execute all undo actions in reverse order."""
        self.rollback_to(0)

    def commit(self) -> None:
        """Commit transaction and discard all journal actions in O(1)."""
        self._committed = True
        self._actions.clear()


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
class RecoveryMetrics:
    """Telemetry capturing replay recovery outcomes and skipped/corrupted frame accounting."""

    replayed_records: int = 0
    valid_records: int = 0
    quarantined_records: int = 0
    corrupted_frames: int = 0
    skipped_offsets: int = 0
    snapshot_offset: int = -1
    snapshot_used: bool = False
    recovery_duration_ms: float = 0.0


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
    wal_dir: str | None = None
    snapshot_interval: int = 10_000
    session_policy: str = "strict"  # strict, session_marker, auto_detect
    snapshot_cadence_events: int = 50_000
    snapshot_cadence_seconds: float = 300.0
    gap_manager: Any | None = None
    on_gap: Any | None = None


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
        session_policy: str = "strict",
        snapshot_cadence_events: int = 50_000,
        snapshot_cadence_seconds: float = 300.0,
        gap_manager: Any | None = None,
        on_gap: Any | None = None,
        **kwargs: Any,
    ) -> None:
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

        valid_legacy_kwargs = {"durability", "quality", "async_writer", "journal"}
        for k in kwargs:
            if k not in valid_legacy_kwargs:
                raise TypeError(
                    f"Engine.__init__() got an unexpected keyword argument '{k}'"
                )
            else:
                warnings.warn(
                    f"Parameter '{k}' is deprecated in Engine; use explicit IngestLog and Projection configuration.",
                    DeprecationWarning,
                    stacklevel=2,
                )

        if isinstance(path, EngineConfig):
            cfg = path
            path = cfg.wal_dir or cfg.db_path
            staleness_threshold_s = cfg.staleness_threshold_s
            clock = cfg.clock
            db_path = cfg.db_path
            fsync_policy = cfg.fsync_policy
            max_segment_bytes = cfg.max_segment_bytes
            max_payload_bytes = cfg.max_payload_bytes
            security = cfg.security
            session_policy = cfg.session_policy
            snapshot_cadence_events = cfg.snapshot_cadence_events
            snapshot_cadence_seconds = cfg.snapshot_cadence_seconds
            gap_manager = cfg.gap_manager
            on_gap = cfg.on_gap

        if gap_manager is None and on_gap is not None:
            from .gap import GapManager

            gap_manager = GapManager(on_gap_hook=on_gap)

        self.staleness_threshold_s = staleness_threshold_s
        self.clock = clock if clock is not None else SystemClock()
        self.max_payload_bytes = max_payload_bytes
        self.security = security
        self.session_policy = session_policy
        self.snapshot_cadence_events = snapshot_cadence_events
        self.snapshot_cadence_seconds = snapshot_cadence_seconds
        self.gap_manager = gap_manager
        self.state = self.create_initial_state()
        self._last_snapshot_event_count: int = self.state.event_count
        self._last_snapshot_ts: float = time.time()
        self._projections: list[Any] = []
        self._default_sqlite_proj: Any = None
        self.log: Any = None
        self._lock = threading.RLock()
        self.recovery_metrics: RecoveryMetrics = RecoveryMetrics()

        if path is not None:
            from .ingestlog import IngestLog

            t_start_recovery = time.perf_counter()
            self.log = IngestLog(
                str(path),
                fsync_policy=fsync_policy,
                max_segment_bytes=max_segment_bytes,
            )
            # Bounded startup (Phase 3 & Program I-4): check for snapshot with SHA-256 verification
            replay_from = 0
            snapshot_file = os.path.join(self.log.log_dir, "snapshot.json")
            if os.path.exists(snapshot_file):
                try:
                    with open(snapshot_file, "r", encoding="utf-8") as f_snap:
                        snap_obj = json.load(f_snap)
                    snap_state_dict = snap_obj.get("state")
                    snap_offset = snap_obj.get("offset", -1)
                    snap_sha = snap_obj.get("sha256")
                    if snap_state_dict and snap_offset >= 0:
                        if snap_sha:
                            canonical_bytes = json.dumps(
                                snap_state_dict, sort_keys=True, separators=(",", ":")
                            ).encode("utf-8")
                            actual_sha = hashlib.sha256(canonical_bytes).hexdigest()
                            if actual_sha != snap_sha:
                                raise ValueError(
                                    f"Snapshot SHA-256 checksum mismatch: expected {snap_sha}, got {actual_sha}"
                                )
                        self.state = EngineState.from_dict(snap_state_dict)
                        self._last_snapshot_event_count = self.state.event_count
                        self._last_snapshot_ts = time.time()
                        replay_from = snap_offset + 1
                        self.recovery_metrics.snapshot_offset = snap_offset
                        self.recovery_metrics.snapshot_used = True
                        logger.info(
                            "[engine] Restored snapshot at offset %d (event_count=%d)",
                            snap_offset,
                            self.state.event_count,
                        )
                except Exception as snap_exc:
                    logger.warning(
                        "[engine] Failed to load snapshot %s: %s; replaying from start",
                        snapshot_file,
                        snap_exc,
                    )
                    self.state = self.create_initial_state()
                    replay_from = 0
                    self.recovery_metrics.snapshot_offset = -1
                    self.recovery_metrics.snapshot_used = False

            # Catch up state only from uncompacted log records since snapshot
            for off, raw in self.log.iter_from(replay_from):
                self.state, dec = self.step(self.state, raw, self.clock, offset=off)
                self.recovery_metrics.replayed_records += 1
                if dec.quality_status == QualityStatus.INVALID:
                    self.recovery_metrics.quarantined_records += 1
                else:
                    self.recovery_metrics.valid_records += 1

            self.recovery_metrics.recovery_duration_ms = (
                time.perf_counter() - t_start_recovery
            ) * 1000.0

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
        session_policy = "strict"
        snapshot_cadence_events = 50_000
        snapshot_cadence_seconds = 300.0
        gap_manager = None
        on_gap = None

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
                session_policy = config.get("session_policy", session_policy)
                snapshot_cadence_events = config.get(
                    "snapshot_cadence_events", snapshot_cadence_events
                )
                snapshot_cadence_seconds = config.get(
                    "snapshot_cadence_seconds", snapshot_cadence_seconds
                )
                gap_manager = config.get("gap_manager", gap_manager)
                on_gap = config.get("on_gap", on_gap)
            else:
                staleness = getattr(config, "staleness_threshold_s", staleness)
                clock = getattr(config, "clock", clock)
                fsync_policy = getattr(config, "fsync_policy", fsync_policy)
                max_segment_bytes = getattr(
                    config, "max_segment_bytes", max_segment_bytes
                )
                max_payload_bytes = getattr(
                    config, "max_payload_bytes", max_payload_bytes
                )
                db_path = getattr(config, "db_path", db_path)
                security = getattr(config, "security", security)
                projections = getattr(config, "projections", [])
                session_policy = getattr(config, "session_policy", session_policy)
                snapshot_cadence_events = getattr(
                    config, "snapshot_cadence_events", snapshot_cadence_events
                )
                snapshot_cadence_seconds = getattr(
                    config, "snapshot_cadence_seconds", snapshot_cadence_seconds
                )
                gap_manager = getattr(config, "gap_manager", gap_manager)
                on_gap = getattr(config, "on_gap", on_gap)

        engine = cls(
            path=path,
            staleness_threshold_s=staleness,
            clock=clock,
            db_path=db_path,
            fsync_policy=fsync_policy,
            max_segment_bytes=max_segment_bytes,
            max_payload_bytes=max_payload_bytes,
            security=security,
            session_policy=session_policy,
            snapshot_cadence_events=snapshot_cadence_events,
            snapshot_cadence_seconds=snapshot_cadence_seconds,
            gap_manager=gap_manager,
            on_gap=on_gap,
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
                        replay_state, _ = self.step(
                            replay_state, raw, self.clock, offset=off
                        )
                    else:
                        replay_state, dec = self.step(
                            replay_state, raw, self.clock, offset=off
                        )
                        batch.append(dec)
                        if len(batch) >= 2000:
                            projection.apply(batch, batch[-1].offset)
                            batch = []
                if batch:
                    projection.apply(batch, batch[-1].offset)

    def rebuild_projection(self, projection: Any) -> int:
        """Reconstruct projection from offset 0 using IngestLog WAL as single source of truth."""
        with self._lock:
            if self.log is None:
                return 0
            if projection not in self._projections:
                self._projections.append(projection)
            batch = []
            replay_state = self.create_initial_state()
            for off, raw in self.log.iter_from(0):
                replay_state, dec = self.step(replay_state, raw, self.clock, offset=off)
                batch.append(dec)
                if len(batch) >= 2000:
                    projection.apply(batch, batch[-1].offset)
                    batch = []
            if batch:
                projection.apply(batch, batch[-1].offset)
            return self.log.next_offset

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
            batch_txn = Transaction(self.state)
            try:
                decisions: list[EngineDecision] = []
                offsets: list[int] = []
                valid_for_wal: list[RawEvent] = []
                raw_to_offset: list[tuple[RawEvent, int]] = []

                for raw in event_list:
                    # Stamp receive timestamp before append to ensure deterministic replay (C2)
                    if not raw.receive_timestamp:
                        raw.receive_timestamp = self.clock.now()

                    # Determine offset before append so deterministic raw_id is stamped and persisted in WAL (N1)
                    offset = (
                        (self.log.next_offset + len(valid_for_wal))
                        if self.log is not None
                        else (self.state.event_count + len(decisions))
                    )
                    if not raw.raw_id:
                        raw.raw_id = f"raw_{raw.source}_{offset}"

                    # Enforce payload size limit at Engine boundary to protect WAL from stalling (N4)
                    if self.max_payload_bytes > 0:
                        payload = raw.payload
                        p_len = (
                            len(payload)
                            if isinstance(payload, (str, bytes))
                            else (
                                len(json.dumps(payload, default=str))
                                if isinstance(payload, dict)
                                else len(str(payload))
                            )
                        )
                        if p_len > self.max_payload_bytes:
                            q_id = f"q_{raw.source}_{offset}"
                            q_row = (
                                q_id,
                                "OVERSIZE",
                                raw.source,
                                QualityStatus.INVALID.value,
                                json.dumps(
                                    [
                                        f"Payload size {p_len} bytes exceeds limit {self.max_payload_bytes} bytes"
                                    ]
                                ),
                                json.dumps(
                                    {"size": p_len, "truncated": str(payload)[:256]}
                                ),
                                raw.receive_timestamp,
                            )
                            batch_txn.record_event_count()
                            batch_txn.record_count("INVALID")
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
                    expected_offsets = [off for _, off in raw_to_offset]
                    if allocated != expected_offsets:
                        raise RuntimeError(
                            f"WAL allocated offsets diverged: expected {expected_offsets}, got {allocated}"
                        )

                if len(raw_to_offset) > 1:
                    for raw, offset in raw_to_offset:
                        offsets.append(offset)
                    self.state, batch_decs = self.step_batch(
                        self.state, raw_to_offset, self.clock, txn=batch_txn
                    )
                    decisions.extend(batch_decs)
                else:
                    for raw, offset in raw_to_offset:
                        offsets.append(offset)
                        self.state, dec = self.step(
                            self.state, raw, self.clock, offset=offset, txn=batch_txn
                        )
                        decisions.append(dec)

                # Dispatch to subscribed projections atomically
                if self._projections and decisions:
                    last_offset = offsets[-1]
                    for proj in self._projections:
                        proj.apply(decisions, last_offset)

                batch_txn.commit()

                # Program I-4: Automatic snapshot cadence evaluation
                if self.log is not None:
                    now_wall = time.time()
                    evs_since = self.state.event_count - self._last_snapshot_event_count
                    time_since = now_wall - self._last_snapshot_ts
                    trigger_cadence = (
                        self.snapshot_cadence_events > 0
                        and evs_since >= self.snapshot_cadence_events
                    ) or (
                        self.snapshot_cadence_seconds > 0
                        and time_since >= self.snapshot_cadence_seconds
                    )
                    if trigger_cadence:
                        try:
                            self.save_snapshot()
                            self._last_snapshot_event_count = self.state.event_count
                            self._last_snapshot_ts = now_wall
                        except Exception as snap_err:
                            logger.warning(
                                "[engine] Automatic cadence snapshot failed: %s",
                                snap_err,
                            )

                if is_single:
                    return decisions[0]
                return decisions
            except Exception:
                # Durability / IO failure rollback (Task 5: Exception Safety & State Consistency)
                batch_txn.rollback()
                raise

    def submit_microbatch(self, events: list[RawEvent]) -> list[EngineDecision]:
        """Durable atomic micro-batch submission API (Item A4).

        Processes and persists events in a single group-commit fsync and atomic
        SQLite projection transaction.
        """
        res = self.submit(events)
        return res if isinstance(res, list) else ([res] if res is not None else [])

    def replay(
        self,
        from_offset: int = 0,
        to_offset: int | None = None,
        projection: Any | None = None,
        return_metrics: bool = False,
    ) -> list[EngineDecision] | tuple[list[EngineDecision], RecoveryMetrics]:
        """Replay events from WAL from_offset up to to_offset."""
        metrics = RecoveryMetrics()
        if self.log is None:
            self.recovery_metrics = metrics
            return ([], metrics) if return_metrics else []

        decisions: list[EngineDecision] = []
        replay_state = self.create_initial_state()
        initial_corrupt = getattr(self.log, "corrupted_frames_count", 0)

        for off, raw in self.log.iter_from(0):
            if off < from_offset:
                replay_state, _ = self.step(replay_state, raw, self.clock, offset=off)
                continue
            if to_offset is not None and off > to_offset:
                break
            replay_state, dec = self.step(replay_state, raw, self.clock, offset=off)
            decisions.append(dec)
            metrics.replayed_records += 1
            if dec.quality_status == QualityStatus.INVALID:
                metrics.quarantined_records += 1
            else:
                metrics.valid_records += 1

        curr_corrupt = getattr(self.log, "corrupted_frames_count", 0)
        metrics.corrupted_frames = curr_corrupt - initial_corrupt
        self.recovery_metrics = metrics

        if projection is not None and decisions:
            projection.apply(decisions, decisions[-1].offset)

        if return_metrics:
            return decisions, metrics
        return decisions

    def replay_with_metrics(
        self,
        from_offset: int = 0,
        to_offset: int | None = None,
        projection: Any | None = None,
    ) -> tuple[list[EngineDecision], RecoveryMetrics]:
        """Explicit typed variant of replay() returning decisions and RecoveryMetrics."""
        res = self.replay(
            from_offset=from_offset,
            to_offset=to_offset,
            projection=projection,
            return_metrics=True,
        )
        return res  # type: ignore[return-value]

    def process(self, raw: RawEvent) -> CanonicalEvent | None:
        """Process a single raw event through the validation pipeline."""
        dec = self.submit(raw)
        return dec.canonical_event if isinstance(dec, EngineDecision) else None

    def process_batch(self, raw_events: list[RawEvent]) -> list[CanonicalEvent]:
        """Process a batch of raw events through the engine, returning strictly aligned canonical events."""
        decisions = self.submit(raw_events)
        if isinstance(decisions, list):
            return [
                d.canonical_event for d in decisions if d.canonical_event is not None
            ]
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
        log_head = (
            self.log.next_offset if self.log is not None else self.state.event_count
        )
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

    def stats(self) -> dict[str, Any]:
        """Alias for metrics()."""
        return self.metrics()

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
        txn: Transaction | None = None,
    ) -> tuple[EngineState, EngineDecision]:
        """Apply a single deterministic state transition: (state, raw_event, clock) -> (state', decision)."""
        prev_event_count = state.event_count
        step_txn = txn if txn is not None else Transaction(state)
        mark = len(step_txn._actions)
        try:
            st, dec = self._step_internal(
                state, raw_event, clock, offset=offset, txn=step_txn
            )
            if txn is None:
                step_txn.commit()
            return st, dec
        except Exception as exc:
            # Restore state from clean undo journal to prevent partial mutation state-inconsistency (N3)
            step_txn.rollback_to(mark)
            if txn is not None:
                txn.record_event_count()
                txn.record_count("INVALID")
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
                json.dumps(
                    [f"Unhandled engine exception: {type(exc).__name__}: {exc}"]
                ),
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

    def step_batch(
        self,
        state: EngineState,
        events: list[tuple[RawEvent, int]] | list[RawEvent],
        clock: Clock,
        txn: Transaction | None = None,
    ) -> tuple[EngineState, list[EngineDecision]]:
        """Apply a batch of deterministic state transitions with aligned offsets."""
        decisions: list[EngineDecision] = []
        for item in events:
            if isinstance(item, tuple):
                raw, offset = item
            else:
                raw, offset = item, None
            state, dec = self.step(state, raw, clock, offset=offset, txn=txn)
            decisions.append(dec)
        return state, decisions

    def _stage_security_envelope(
        self,
        payload: dict,
        raw_event: RawEvent,
        offset: int,
        recv_ts: float,
    ) -> tuple[bool, EngineDecision | None]:
        if self.security is None:
            return True, None

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
            return False, EngineDecision(
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
                    json.dumps(
                        [
                            "Cryptographic HMAC verification failed: missing or tampered signature"
                        ]
                    ),
                    json.dumps(payload, default=str),
                    recv_ts,
                )
                return False, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SECURITY_REJECT.value],
                    quarantine_row=q_row,
                )

        return True, None

    def _stage_schema_validation(
        self,
        payload: dict,
        raw_event: RawEvent,
        offset: int,
        recv_ts: float,
    ) -> tuple[bool, str, str, int | None, float, EngineDecision | None]:
        instrument = payload.get("instrument")
        raw_event_type = payload.get("event_type")

        # Check for backpressure drop / tombstone
        if (
            payload.get("drop_reason") == "BACKPRESSURE_DROP"
            or raw_event_type == "TOMBSTONE_DROPPED"
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
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.BACKPRESSURE_DROP.value],
                    quarantine_row=q_row,
                ),
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
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )

        if raw_event_type is None:
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Missing required event_type field"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )

        if isinstance(raw_event_type, EventType):
            event_type_str = raw_event_type.value
        elif isinstance(raw_event_type, str):
            event_type_str = raw_event_type.strip().upper()
        else:
            event_type_str = ""

        if event_type_str not in ("TRADE", "QUOTE", "HALT", "STATUS"):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps([f"Unknown or invalid event_type: {raw_event_type!r}"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )

        # Structural validation per event type
        if event_type_str == "TRADE" and payload.get("price") is None:
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["TRADE event requires price"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )
        elif (
            event_type_str == "QUOTE"
            and payload.get("bid") is None
            and payload.get("ask") is None
        ):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["QUOTE event requires at least one of bid or ask"]),
                json.dumps(payload, default=str),
                recv_ts,
            )
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )
        elif event_type_str in ("HALT", "STATUS"):
            pass

        seq = payload.get("sequence")
        if seq is not None:
            if (
                not isinstance(seq, int)
                or isinstance(seq, bool)
                or not (-9223372036854775808 <= seq <= 9223372036854775807)
            ):
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
                return (
                    False,
                    "",
                    "",
                    None,
                    0.0,
                    EngineDecision(
                        offset=offset,
                        event_id=event_id,
                        quality_status=QualityStatus.INVALID,
                        reasons=[Reason.SCHEMA_VIOLATION.value],
                        quarantine_row=q_row,
                    ),
                )

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
            return (
                False,
                "",
                "",
                None,
                0.0,
                EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    quarantine_row=q_row,
                ),
            )

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
                return (
                    False,
                    "",
                    "",
                    None,
                    0.0,
                    EngineDecision(
                        offset=offset,
                        event_id=event_id,
                        quality_status=QualityStatus.INVALID,
                        reasons=[Reason.SCHEMA_VIOLATION.value],
                        quarantine_row=q_row,
                    ),
                )

        return True, instrument, event_type_str, seq, float(exch_ts), None

    def _stage_session_and_epoch(
        self,
        state: EngineState,
        payload: dict,
        raw_event: RawEvent,
        instrument: str,
        seq: int | None,
        exch_ts: float,
        txn: Transaction | None,
    ) -> tuple[int, str, str, str | None, bool]:
        stream_key = f"{raw_event.source}:{instrument}"
        current_epoch = state.epoch_state.get(stream_key, 0)
        current_session = state.session_state.get(stream_key)
        last_epoch_ts = state.epoch_ts.get(stream_key)

        epoch_reset_triggered = False
        new_session_id = getattr(raw_event, "session_id", None) or payload.get(
            "session_id"
        )

        if self.session_policy == "session_marker":
            is_reset = payload.get("is_reset") is True or payload.get("reset") is True
            epoch_id_marker = payload.get("epoch_id")
            if is_reset:
                epoch_reset_triggered = True
            elif (
                new_session_id is not None
                and current_session is not None
                and str(new_session_id) != current_session
            ):
                epoch_reset_triggered = True
            elif epoch_id_marker is not None and epoch_id_marker != current_epoch:
                epoch_reset_triggered = True
        elif self.session_policy == "auto_detect":
            active_slot = (
                f"{stream_key}:{current_epoch}" if current_epoch > 0 else stream_key
            )
            last_s = state.sequence_state.get(active_slot)
            if (
                seq is not None
                and last_s is not None
                and (last_s - seq >= 50 or (last_s > 10 and seq <= 10))
                and (last_epoch_ts is not None and exch_ts - last_epoch_ts >= 60.0)
            ):
                epoch_reset_triggered = True

        if epoch_reset_triggered:
            if txn is not None:
                txn.record_epoch(stream_key)
            current_epoch += 1
            state.epoch_state[stream_key] = current_epoch
            if new_session_id is not None:
                state.session_state[stream_key] = str(new_session_id)
        elif new_session_id is not None and current_session is None:
            if txn is not None:
                had_sess = stream_key in state.session_state
                old_sess = state.session_state.get(stream_key)
                txn._actions.append(
                    (_OP_DICT_KEY, state.session_state, stream_key, had_sess, old_sess)
                )
            state.session_state[stream_key] = str(new_session_id)

        if last_epoch_ts != exch_ts:
            if txn is not None and not epoch_reset_triggered:
                had_ts = stream_key in state.epoch_ts
                old_ts = state.epoch_ts.get(stream_key)
                txn._actions.append(
                    (_OP_DICT_KEY, state.epoch_ts, stream_key, had_ts, old_ts)
                )
            state.epoch_ts[stream_key] = exch_ts

        slot_key = f"{stream_key}:{current_epoch}" if current_epoch > 0 else stream_key
        return (
            current_epoch,
            stream_key,
            slot_key,
            str(new_session_id) if new_session_id is not None else None,
            epoch_reset_triggered,
        )

    def _stage_sequence_and_dedup(
        self,
        state: EngineState,
        payload: dict,
        raw_event: RawEvent,
        instrument: str,
        event_type_str: str,
        seq: int | None,
        exch_ts: float,
        recv_ts: float,
        current_epoch: int,
        stream_key: str,
        slot_key: str,
        status: QualityStatus,
        reasons: list[str],
        txn: Transaction | None,
    ) -> tuple[QualityStatus, list[str], bool]:
        is_duplicate = False
        reasons_out = list(reasons)
        cur_status = status

        if seq is not None:
            last_seq = state.sequence_state.get(slot_key)
            if last_seq is not None:
                if seq == last_seq:
                    is_duplicate = True
                    cur_status = QualityStatus.merge(cur_status, QualityStatus.INVALID)
                    reasons_out.append(Reason.DUPLICATE.value)
                elif seq < last_seq:
                    filled = None
                    if self.gap_manager is not None:
                        filled = self.gap_manager.try_fill(
                            source=raw_event.source,
                            instrument=instrument,
                            seq=seq,
                            epoch=str(current_epoch),
                            now_ts=recv_ts,
                        )
                    if filled is not None:
                        reasons_out.append(Reason.GAP_FILLED.value)
                    else:
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.SUSPICIOUS
                        )
                        reasons_out.append(Reason.OUT_OF_ORDER.value)
                elif seq > last_seq + 1:
                    cur_status = QualityStatus.merge(
                        cur_status, QualityStatus.SUSPICIOUS
                    )
                    reasons_out.append(Reason.SEQUENCE_GAP.value)
                    if self.gap_manager is not None:
                        self.gap_manager.record_gap(
                            source=raw_event.source,
                            instrument=instrument,
                            expected_start=last_seq + 1,
                            received_seq=seq,
                            epoch=str(current_epoch),
                            now_ts=recv_ts,
                        )
            if txn is not None:
                txn.record_sequence(slot_key)
                if slot_key != stream_key:
                    txn.record_sequence(stream_key)
            state.sequence_state[slot_key] = max(
                last_seq if last_seq is not None else -1, seq
            )
            if slot_key != stream_key:
                state.sequence_state[stream_key] = seq
        else:
            biz_id = (
                payload.get("trade_id")
                or payload.get("msg_id")
                or payload.get("message_id")
                or payload.get("exec_id")
                or payload.get("order_id")
                or payload.get("id")
            )
            if biz_id is not None:
                dedup_fingerprint = (
                    raw_event.source,
                    instrument,
                    event_type_str,
                    f"id={biz_id}",
                )
            else:
                dedup_fingerprint = (
                    raw_event.source,
                    instrument,
                    event_type_str,
                    exch_ts,
                    payload.get("price"),
                    payload.get("quantity"),
                    payload.get("bid"),
                    payload.get("ask"),
                )
            if dedup_fingerprint in state.dedup_set:
                is_duplicate = True
                cur_status = QualityStatus.merge(cur_status, QualityStatus.INVALID)
                reasons_out.append(Reason.DUPLICATE.value)
            else:
                state.dedup_set.add(dedup_fingerprint)
                state.dedup_window.append(dedup_fingerprint)
                evicted = None
                if len(state.dedup_window) > DEFAULT_DEDUP_WINDOW_SIZE:
                    evicted = state.dedup_window.popleft()
                    state.dedup_set.discard(evicted)
                if txn is not None:
                    txn.record_dedup(dedup_fingerprint, evicted)

        return cur_status, reasons_out, is_duplicate

    def _stage_quote_and_staleness(
        self,
        payload: dict,
        event_type_str: str,
        exch_ts: float,
        recv_ts: float,
        staleness_threshold_s: float,
        status: QualityStatus,
        reasons: list[str],
    ) -> tuple[QualityStatus, list[str]]:
        reasons_out = list(reasons)
        cur_status = status

        if cur_status != QualityStatus.INVALID and event_type_str not in (
            "HALT",
            "STATUS",
        ):
            if recv_ts - exch_ts > staleness_threshold_s:
                cur_status = QualityStatus.merge(cur_status, QualityStatus.SUSPICIOUS)
                reasons_out.append(Reason.STALE.value)

        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is not None and ask is not None and bid > ask:
            cur_status = QualityStatus.merge(cur_status, QualityStatus.INVALID)
            reasons_out.append(Reason.CROSSED_QUOTE.value)

        return cur_status, reasons_out

    def _stage_price_corridor(
        self,
        state: EngineState,
        payload: dict,
        slot_key: str,
        price: float | None,
        status: QualityStatus,
        reasons: list[str],
        txn: Transaction | None,
    ) -> tuple[QualityStatus, list[str]]:
        reasons_out = list(reasons)
        cur_status = status

        if price is not None and cur_status != QualityStatus.INVALID:
            if txn is not None:
                txn.record_price_window(slot_key)
            prices = state.price_windows.setdefault(slot_key, [])
            n_prices = len(prices)
            price_anomaly = False

            stats = state._price_stats.get(slot_key)
            if stats is None and n_prices >= 3:
                mean = sum(prices) / n_prices
                m2 = sum((p - mean) ** 2 for p in prices)
                stats = (mean, m2, 0)
                state._price_stats[slot_key] = stats

            if stats is not None and n_prices >= 20:
                mean, m2, _ = stats
                variance = m2 / n_prices
                std = math.sqrt(variance) if variance > 0.0 else 0.0
                sigma_floor = mean * 0.0001
                effective_std = max(std, sigma_floor)
                if effective_std > 0 and abs(price - mean) > (
                    DEFAULT_PRICE_STDDEV * effective_std
                ):
                    price_anomaly = True
            elif stats is not None and n_prices >= 3:
                mean = stats[0]
                if mean > 0 and abs(price - mean) / mean > 0.10:
                    price_anomaly = True
            elif n_prices >= 20:
                mean = sum(prices) / n_prices
                variance = sum((p - mean) ** 2 for p in prices) / n_prices
                std = math.sqrt(variance)
                sigma_floor = mean * 0.0001
                effective_std = max(std, sigma_floor)
                if effective_std > 0 and abs(price - mean) > (
                    DEFAULT_PRICE_STDDEV * effective_std
                ):
                    price_anomaly = True
            elif n_prices >= 3:
                mean = sum(prices) / n_prices
                if mean > 0 and abs(price - mean) / mean > 0.10:
                    price_anomaly = True

            if price_anomaly:
                cur_status = QualityStatus.merge(cur_status, QualityStatus.SUSPICIOUS)
                reasons_out.append(Reason.PRICE_ANOMALY.value)
            else:
                if n_prices >= DEFAULT_PRICE_WINDOW:
                    old_p = prices[0]
                    prices.pop(0)
                    prices.append(price)
                    if stats is not None:
                        mean, m2, upd_cnt = stats
                        d = price - old_p
                        nm = mean + d / DEFAULT_PRICE_WINDOW
                        m2 = m2 + d * ((price - nm) + (old_p - mean))
                        if m2 < 0.0:
                            m2 = 0.0
                        upd_cnt += 1
                        if upd_cnt >= 200:
                            nm = sum(prices) / DEFAULT_PRICE_WINDOW
                            m2 = sum((p - nm) ** 2 for p in prices)
                            upd_cnt = 0
                        state._price_stats[slot_key] = (nm, m2, upd_cnt)
                else:
                    prices.append(price)
                    new_n = len(prices)
                    if stats is not None:
                        mean, m2, upd_cnt = stats
                        delta = price - mean
                        nm = mean + delta / new_n
                        m2 = m2 + delta * (price - nm)
                        if m2 < 0.0:
                            m2 = 0.0
                        state._price_stats[slot_key] = (nm, m2, upd_cnt + 1)
                    elif new_n >= 3:
                        nm = sum(prices) / new_n
                        m2 = sum((p - nm) ** 2 for p in prices)
                        state._price_stats[slot_key] = (nm, m2, 0)

        return cur_status, reasons_out

    def _stage_bbo_and_reconciliation(
        self,
        state: EngineState,
        payload: dict,
        raw_event: RawEvent,
        instrument: str,
        price: float | None,
        exch_ts: float,
        status: QualityStatus,
        reasons: list[str],
        txn: Transaction | None,
    ) -> tuple[QualityStatus, list[str], bool]:
        reasons_out = list(reasons)
        cur_status = status
        disagreement = False

        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is not None or ask is not None:
            if txn is not None:
                txn.record_bbo(instrument, raw_event.source)
            source_quotes = state.bbo_state.setdefault(instrument, {})
            current_src = source_quotes.setdefault(raw_event.source, {})
            if bid is not None:
                current_src["bid"] = float(bid)
            if ask is not None:
                current_src["ask"] = float(ask)

        if price is not None and cur_status != QualityStatus.INVALID:
            if txn is not None:
                txn.record_reconciliation(instrument, raw_event.source)
            recent_inst = state.reconciliation_state.setdefault(instrument, {})
            recent_inst[raw_event.source] = (price, exch_ts)
            if len(recent_inst) >= 2:
                p_sum = 0.0
                p_count = 0
                for p, ts in recent_inst.values():
                    if abs(exch_ts - ts) <= 0.25:
                        p_sum += p
                        p_count += 1
                if p_count >= 2:
                    mean_p = p_sum / p_count
                    if mean_p > 0:
                        for p, ts in recent_inst.values():
                            if (
                                abs(exch_ts - ts) <= 0.25
                                and abs(p - mean_p) / mean_p > 0.005
                            ):
                                disagreement = True
                                break
                    if disagreement:
                        reasons_out.append(Reason.CROSS_FEED_DISAGREEMENT.value)
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.SUSPICIOUS
                        )

        return cur_status, reasons_out, disagreement

    def _step_internal(
        self,
        state: EngineState,
        raw_event: RawEvent,
        clock: Clock,
        offset: int | None = None,
        txn: Transaction | None = None,
    ) -> tuple[EngineState, EngineDecision]:
        if offset is None:
            offset = state.event_count

        if txn is not None:
            txn.record_event_count()
        state.event_count += 1
        now_ts = clock.now()
        recv_ts = raw_event.receive_timestamp if raw_event.receive_timestamp else now_ts

        def _bump_invalid() -> None:
            if txn is not None:
                txn.record_count("INVALID")
            state.counts["INVALID"] += 1

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
            _bump_invalid()
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=[Reason.SCHEMA_VIOLATION.value],
                quarantine_row=q_row,
            )

        # Stage 1: Security & Cryptographic Envelope
        is_sec_valid, sec_dec = self._stage_security_envelope(
            payload, raw_event, offset, recv_ts
        )
        if not is_sec_valid and sec_dec is not None:
            _bump_invalid()
            return state, sec_dec

        # Stage 2: Schema Field & Structural Validation
        is_schema_valid, instrument, event_type_str, seq, exch_ts, schema_dec = (
            self._stage_schema_validation(payload, raw_event, offset, recv_ts)
        )
        if not is_schema_valid and schema_dec is not None:
            _bump_invalid()
            return state, schema_dec

        # Derive event_id
        if raw_event.raw_id:
            event_id = str(raw_event.raw_id)
        elif seq is not None:
            event_id = f"evt_{raw_event.source}_{instrument}_{seq}"
        else:
            event_id = f"evt_{raw_event.source}_{instrument}_{offset}"

        status = QualityStatus.VALID
        reasons: list[str] = []

        # Stage 3: Session and Sequence Epochs
        current_epoch, stream_key, slot_key, new_session_id, epoch_reset = (
            self._stage_session_and_epoch(
                state, payload, raw_event, instrument, seq, exch_ts, txn
            )
        )
        if epoch_reset:
            reasons.append(Reason.EPOCH_RESET.value)

        # Stage 4: Sequence Ordering & Deduplication
        status, reasons, is_duplicate = self._stage_sequence_and_dedup(
            state,
            payload,
            raw_event,
            instrument,
            event_type_str,
            seq,
            exch_ts,
            recv_ts,
            current_epoch,
            stream_key,
            slot_key,
            status,
            reasons,
            txn,
        )

        # Stage 5: Staleness & Quote Book Consistency
        status, reasons = self._stage_quote_and_staleness(
            payload,
            event_type_str,
            exch_ts,
            recv_ts,
            state.staleness_threshold_s,
            status,
            reasons,
        )

        # Stage 6: Statistical Price Corridor Evaluation
        price = payload.get("price")
        status, reasons = self._stage_price_corridor(
            state, payload, slot_key, price, status, reasons, txn
        )

        # Stage 7: BBO Tracking & Cross-Feed Reconciliation
        status, reasons, disagreement = self._stage_bbo_and_reconciliation(
            state,
            payload,
            raw_event,
            instrument,
            price,
            exch_ts,
            status,
            reasons,
            txn,
        )

        # Stage 8: Monotonic Counter & Packaging
        if txn is not None:
            txn.record_count(status.name)
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
            bid_price=payload.get("bid"),
            ask_price=payload.get("ask"),
            bid_size=payload.get("bid_size"),
            ask_size=payload.get("ask_size"),
            quality_status=status,
            reasons=reasons,
            raw_id=raw_event.raw_id,
            session_id=(
                str(new_session_id)
                if new_session_id is not None
                else (f"epoch_{current_epoch}" if current_epoch > 0 else None)
            ),
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

    def snapshot(self, target: Any = None) -> bytes | str:
        """Create a deterministic byte snapshot or save to path with SHA-256 checksum."""
        st = target if isinstance(target, EngineState) else self.state
        st_dict = st.to_dict()
        canonical_bytes = json.dumps(
            st_dict, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        state_sha256 = hashlib.sha256(canonical_bytes).hexdigest()

        if isinstance(target, (str, Path)):
            with self._lock:
                data = {
                    "version": 2,
                    "offset": self.state.event_count - 1,
                    "timestamp": self.clock.now(),
                    "event_count": self.state.event_count,
                    "sha256": state_sha256,
                    "state": st_dict,
                }
                tmp_target = f"{target}.tmp.{os.getpid()}_{time.time_ns()}"
                with open(tmp_target, "w", encoding="utf-8") as f:
                    json.dump(data, f, sort_keys=True, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_target, target)
                return str(target)
        return json.dumps(st_dict, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    @classmethod
    def restore(
        cls,
        snapshot_source: bytes | str | Path,
        config: Any | None = None,
    ) -> Any:
        """Restore state from snapshot bytes or restore an Engine from a snapshot file path."""
        if isinstance(snapshot_source, (str, Path)):
            with open(snapshot_source, "r", encoding="utf-8") as f:
                data = json.load(f)
            st_dict = data.get("state", data)
            expected_sha = data.get("sha256")
            if expected_sha:
                canonical_bytes = json.dumps(
                    st_dict, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                actual_sha = hashlib.sha256(canonical_bytes).hexdigest()
                if actual_sha != expected_sha:
                    raise ValueError(
                        f"Snapshot SHA-256 checksum mismatch: expected {expected_sha}, got {actual_sha}"
                    )
            st = EngineState.from_dict(st_dict)
            wal_path = getattr(config, "wal_dir", None) if config else None
            engine = cls.open(
                wal_path or os.path.dirname(snapshot_source), config=config
            )
            engine.state = st
            return engine
        data = json.loads(snapshot_source.decode("utf-8"))
        st_dict = data.get("state", data)
        return EngineState.from_dict(st_dict)

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
        """Save a snapshot of current engine state to the WAL directory with SHA-256 integrity."""
        with self._lock:
            if self.log is None:
                raise RuntimeError("Cannot save snapshot without an active IngestLog")

            last_offset = self.state.event_count - 1
            st_dict = self.state.to_dict()
            canonical_bytes = json.dumps(
                st_dict, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            state_sha256 = hashlib.sha256(canonical_bytes).hexdigest()

            snap_data = {
                "version": 2,
                "offset": last_offset,
                "timestamp": self.clock.now(),
                "event_count": self.state.event_count,
                "sha256": state_sha256,
                "state": st_dict,
            }
            snap_dir = self.log.log_dir
            tmp_path = os.path.join(
                snap_dir, f"snapshot.json.tmp.{os.getpid()}_{time.time_ns()}"
            )
            final_path = os.path.join(snap_dir, "snapshot.json")

            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(snap_data, f, sort_keys=True, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, final_path)

            if os.name != "nt":
                try:
                    dir_fd = os.open(snap_dir, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except Exception:
                    pass

            pruned = 0
            if prune_obsolete_segments and last_offset >= 0:
                pruned = self.delete_segments_before(last_offset)

            return {
                "snapshot_offset": last_offset,
                "path": final_path,
                "segments_pruned": pruned,
                "sha256": state_sha256,
            }


DeterministicEngine = Engine

__all__ = [
    "Engine",
    "DeterministicEngine",
    "EngineConfig",
    "EngineState",
    "EngineDecision",
    "RecoveryMetrics",
    "Transaction",
]
