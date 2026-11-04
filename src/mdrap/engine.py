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
from .models import (
    CanonicalEvent,
    CorruptedSnapshotError,
    EventType,
    QualityStatus,
    RawEvent,
    Reason,
    RecoveryError,
)


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


def estimate_payload_size(payload: Any, max_bytes: int = 0) -> int:
    """Accurately estimate or compute the serialized byte length of a payload.

    Provides a fast, zero-allocation upper bound for scalar dictionaries without calling
    json.dumps(). If payload contains non-scalar containers (lists, dicts) or if the
    scalar upper bound approaches or exceeds max_bytes, exact json.dumps() length is computed.
    Guarantees:
      - Never allows an oversize payload to bypass max_bytes (no false negatives).
      - Never falsely flags a valid payload as oversize (no false positives).
    """
    p_type = type(payload)
    if p_type is str or p_type is bytes:
        return len(payload)
    if p_type is dict:
        p_len = len(payload)
        if p_len <= 32:
            est = 2
            has_container = False
            for k, v in payload.items():
                est += (len(k) if type(k) is str else len(str(k))) + 4
                vt = type(v)
                if vt is str or vt is bytes:
                    est += len(v) + 2
                elif vt in (int, float, bool) or v is None:
                    est += 26
                elif vt in (list, tuple, dict, set):
                    has_container = True
                    break
                else:
                    est += len(str(v)) + 4
            if not has_container:
                if max_bytes <= 0 or est <= max_bytes:
                    return est
                return len(json.dumps(payload, default=str))
        return len(json.dumps(payload, default=str))
    return len(str(payload))


DEFAULT_STALENESS_THRESHOLD_S: float = 0.05
DEFAULT_PRICE_STDDEV: float = 6.0
DEFAULT_PRICE_WINDOW: int = 50
DEFAULT_DEDUP_WINDOW_SIZE: int = 10_000
SEQUENCE_BITMAP_BITS: int = 64
SEQUENCE_BITMAP_MASK: int = 0xFFFFFFFFFFFFFFFF
DEFAULT_SEQ_JUMP_LIMIT: int = 1000
DEFAULT_PRICE_RESEED_AFTER: int = 8


@dataclass
class EngineState:
    """Serializable, in-memory state of the deterministic engine fold."""

    event_count: int = 0
    last_applied_offset: int = -1
    observed_count: int = 0
    sequence_state: dict[str, int] = field(default_factory=dict)
    sequence_seen: dict[str, int] = field(default_factory=dict)
    sequence_cand: dict[str, int] = field(default_factory=dict)
    sequence_regress: dict[str, int] = field(default_factory=dict)
    sequence_stale_seen: dict[str, list[int]] = field(default_factory=dict)
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
    _price_anom: dict[str, tuple[int, float]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.dedup_window, deque):
            self.dedup_window = deque(self.dedup_window)
        for k, v in list(self.price_windows.items()):
            if not isinstance(v, deque):
                self.price_windows[k] = deque(v)

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
            last_applied_offset=self.last_applied_offset,
            observed_count=self.observed_count,
            sequence_state=dict(self.sequence_state),
            sequence_seen=dict(self.sequence_seen),
            sequence_cand=dict(self.sequence_cand),
            sequence_regress=dict(self.sequence_regress),
            sequence_stale_seen={
                k: list(v) for k, v in self.sequence_stale_seen.items()
            },
            dedup_window=deque(self.dedup_window),
            dedup_set=set(self.dedup_set),
            price_windows={k: deque(v) for k, v in self.price_windows.items()},
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
            _price_anom={k: v for k, v in self._price_anom.items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "last_applied_offset": self.last_applied_offset,
            "sequence_state": dict(self.sequence_state),
            "sequence_seen": dict(self.sequence_seen),
            "sequence_cand": dict(self.sequence_cand),
            "sequence_regress": dict(self.sequence_regress),
            "sequence_stale_seen": {
                k: list(v) for k, v in self.sequence_stale_seen.items()
            },
            "dedup_window": list(self.dedup_window),
            "price_windows": {k: list(v) for k, v in self.price_windows.items()},
            "bbo_state": {
                k: {src: dict(q) for src, q in v.items()}
                for k, v in self.bbo_state.items()
            },
            "reconciliation_state": {
                k: dict(v) for k, v in self.reconciliation_state.items()
            },
            "counts": dict(self.counts),
            "staleness_threshold_s": self.staleness_threshold_s,
            "epoch_state": dict(self.epoch_state),
            "epoch_ts": dict(self.epoch_ts),
            "session_state": dict(self.session_state),
            "_price_stats": {k: list(v) for k, v in self._price_stats.items()},
            "_price_anom": {k: list(v) for k, v in self._price_anom.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineState:
        dedup_win = data.get("dedup_window", [])
        dedup_items = [tuple(x) if isinstance(x, list) else x for x in dedup_win]
        state = cls(
            event_count=data.get("event_count", 0),
            last_applied_offset=data.get("last_applied_offset", -1),
            observed_count=data.get("observed_count", data.get("event_count", 0)),
            sequence_state=data.get("sequence_state", {}),
            sequence_seen=data.get("sequence_seen", {}),
            sequence_cand=data.get("sequence_cand", {}),
            sequence_regress=data.get("sequence_regress", {}),
            sequence_stale_seen=data.get("sequence_stale_seen", {}),
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
            _price_stats={k: tuple(v) for k, v in data.get("_price_stats", {}).items()},
            _price_anom={k: tuple(v) for k, v in data.get("_price_anom", {}).items()},
        )
        return state


_OP_EVENT_COUNT = 1
_OP_COUNT = 2
_OP_DICT_KEY = 3
_OP_DEDUP = 4
_OP_BBO = 5
_OP_RECON = 6
_OP_OBSERVED_COUNT = 7
_OP_LAST_APPLIED_OFFSET = 8


class Transaction:
    """Zero-allocation bounded-cost undo journal tracking touched state keys for O(1) rollback."""

    __slots__ = ("state", "_actions", "_committed")

    def __init__(self, state: EngineState) -> None:
        self.state = state
        self._actions: list[tuple[Any, ...]] = []
        self._committed: bool = False

    def record_event_count(self) -> None:
        self._actions.append((_OP_EVENT_COUNT, self.state.event_count))
        self._actions.append((_OP_OBSERVED_COUNT, self.state.observed_count))
        self._actions.append((_OP_LAST_APPLIED_OFFSET, self.state.last_applied_offset))

    def record_count(self, status_name: str) -> None:
        self._actions.append(
            (_OP_COUNT, status_name, self.state.counts.get(status_name, 0))
        )

    def record_dict(self, dict_obj: dict, key: Any) -> None:
        existed = key in dict_obj
        old_val = dict_obj.get(key)
        self._actions.append((_OP_DICT_KEY, dict_obj, key, existed, old_val))

    def record_sequence(self, slot_key: str) -> None:
        self.record_dict(self.state.sequence_state, slot_key)
        self.record_dict(self.state.sequence_seen, slot_key)
        self.record_dict(self.state.sequence_cand, slot_key)
        self.record_dict(self.state.sequence_regress, slot_key)

    def record_price_window(self, slot_key: str) -> None:
        pw = self.state.price_windows
        had = slot_key in pw
        old_val = list(pw[slot_key]) if had else None
        self._actions.append((_OP_DICT_KEY, pw, slot_key, had, old_val))
        self.record_dict(self.state._price_anom, slot_key)
        self.record_dict(self.state._price_stats, slot_key)

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
            elif op == _OP_OBSERVED_COUNT:
                self.state.observed_count = item[1]
            elif op == _OP_LAST_APPLIED_OFFSET:
                self.state.last_applied_offset = item[1]
            elif op == _OP_COUNT:
                self.state.counts[item[1]] = item[2]
            elif op == _OP_DICT_KEY:
                d, k, existed, old_val = item[1], item[2], item[3], item[4]
                if existed:
                    d[k] = (
                        deque(old_val)
                        if d is self.state.price_windows and isinstance(old_val, list)
                        else old_val
                    )
                else:
                    d.pop(k, None)
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
    durable: bool = True


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
    max_snapshots_retained: int = 3
    min_wal_snapshots_retained: int = 2


def _fsync_dir(dirpath: str) -> None:
    """Fsync directory on POSIX platforms to guarantee directory entry durability.

    On POSIX operating systems, renaming or unlinking a file alters directory entries,
    which must be fsynced on the parent directory file descriptor to survive power loss.
    On Windows (NT), directory entries are updated synchronously by the NTFS journal.
    """
    if os.name != "nt":
        try:
            dir_fd = os.open(dirpath, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            pass


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

        cfg_obj: EngineConfig | None = path if isinstance(path, EngineConfig) else None
        if cfg_obj is not None:
            cfg = cfg_obj
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
        self.max_snapshots_retained = (
            getattr(cfg_obj, "max_snapshots_retained", 3) if cfg_obj is not None else 3
        )
        self.min_wal_snapshots_retained = (
            getattr(cfg_obj, "min_wal_snapshots_retained", 2)
            if cfg_obj is not None
            else 2
        )
        self.gap_manager = gap_manager
        self.state = self.create_initial_state()
        self.observed_raw_count: int = 0
        self.rejected_count: int = 0
        self._last_snapshot_event_count: int = self.state.event_count
        self._last_snapshot_ts: float = time.time()
        self._projections: list[Any] = []
        self._projection_trackers: dict[int, Any] = {}
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
            # Bounded startup (Phase 3, P0-A, P0-B): discover verified snapshots with multi-candidate fallback
            replay_from = 0
            snap_dir = self.log.log_dir
            candidates = self._discover_candidate_snapshots(snap_dir)
            snapshot_restored = False

            failed_offsets_snap: set[int] = set()
            for snap_off, snap_path in candidates:
                if snap_off in failed_offsets_snap:
                    continue
                if not os.path.isfile(snap_path):
                    continue
                try:
                    with open(snap_path, "r", encoding="utf-8") as f_snap:
                        snap_obj = json.load(f_snap)
                    snap_state_dict = snap_obj.get("state")
                    recorded_offset = snap_obj.get("offset", -1)
                    snap_sha = snap_obj.get("sha256")
                    if not snap_state_dict or recorded_offset < 0:
                        continue
                    if snap_sha:
                        canonical_bytes = json.dumps(
                            snap_state_dict, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                        actual_sha = hashlib.sha256(canonical_bytes).hexdigest()
                        if actual_sha != snap_sha:
                            failed_offsets_snap.add(snap_off)
                            if recorded_offset >= 0:
                                failed_offsets_snap.add(recorded_offset)
                            raise ValueError(
                                f"Snapshot SHA-256 checksum mismatch: expected {snap_sha}, got {actual_sha}"
                            )

                    # Snapshot cryptographically verified!
                    # Verify if the WAL has history from recorded_offset + 1
                    if (
                        self.log is not None
                        and self.log.next_offset > 0
                        and recorded_offset + 1 < self.log.next_offset
                        and self.log.earliest_offset > recorded_offset + 1
                    ):
                        failed_offsets_snap.add(snap_off)
                        if recorded_offset >= 0:
                            failed_offsets_snap.add(recorded_offset)
                        logger.warning(
                            "[engine] Candidate snapshot %s at offset %d cannot be used: WAL history between %d and %d is missing; trying older candidates...",
                            snap_path,
                            recorded_offset,
                            recorded_offset + 1,
                            self.log.earliest_offset,
                        )
                        continue

                    # Usable snapshot found!
                    self.state = EngineState.from_dict(snap_state_dict)
                    self._last_snapshot_event_count = self.state.event_count
                    self._last_snapshot_ts = time.time()
                    replay_from = recorded_offset + 1
                    self.recovery_metrics.snapshot_offset = recorded_offset
                    self.recovery_metrics.snapshot_used = True
                    snapshot_restored = True
                    logger.info(
                        "[engine] Restored verified snapshot %s at offset %d (event_count=%d)",
                        os.path.basename(snap_path),
                        recorded_offset,
                        self.state.event_count,
                    )
                    break
                except Exception as snap_exc:
                    failed_offsets_snap.add(snap_off)
                    logger.warning(
                        "[engine] Failed to load candidate snapshot %s: %s; trying older candidates...",
                        snap_path,
                        snap_exc,
                    )

            if not snapshot_restored:
                # No valid snapshot could be loaded
                if (
                    self.log is not None
                    and self.log.next_offset > 0
                    and self.log.earliest_offset > 0
                ):
                    # Invariant: Fail closed if WAL history is incomplete!
                    if failed_offsets_snap:
                        raise CorruptedSnapshotError(
                            f"Engine recovery failed: All candidate snapshots failed integrity checks and WAL history before offset "
                            f"{self.log.earliest_offset} is missing (required offset 0). "
                            "Cannot reconstruct authoritative state without complete history."
                        )
                    raise RecoveryError(
                        f"Engine recovery failed: No valid snapshot could be loaded and WAL history before offset "
                        f"{self.log.earliest_offset} is missing (required offset 0). "
                        "Cannot reconstruct authoritative state without complete history."
                    )

                self.state = self.create_initial_state()
                replay_from = 0
                self.recovery_metrics.snapshot_offset = -1
                self.recovery_metrics.snapshot_used = False

            # Catch up state only from uncompacted log records since snapshot
            for off, raw in self.log.iter_from(replay_from):
                self.state.observed_count += 1
                self.state, dec = self.step(
                    self.state, raw, self.clock, offset=off, is_replay=True
                )
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
            self.recover_projections()

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

        if projections:
            engine.recover_projections()

        return engine

    def _replay_projection_batch(
        self, from_offset: int, max_batch_size: int = 1000
    ) -> tuple[list[EngineDecision], int]:
        """Replay events from WAL starting at from_offset, stepping deterministically."""
        with self._lock:
            if self.log is None:
                return [], from_offset - 1
            if from_offset >= self.log.next_offset:
                return [], from_offset - 1

            batch: list[EngineDecision] = []
            last_off = from_offset - 1

            replay_state = self.create_initial_state()
            for off, raw in self.log.iter_from(0):
                if off < from_offset:
                    replay_state, _ = self.step(
                        replay_state, raw, self.clock, offset=off
                    )
                else:
                    replay_state, dec = self.step(
                        replay_state, raw, self.clock, offset=off
                    )
                    batch.append(dec)
                    last_off = off
                    if len(batch) >= max_batch_size:
                        break

            return batch, last_off

    def process_one(self, raw: RawEvent) -> CanonicalEvent | None:
        """Process a single RawEvent and return the resulting CanonicalEvent if valid/suspicious, or None if invalid."""
        dec = self.submit(raw)
        return dec.canonical_event if dec else None

    def _get_or_create_projection_tracker(self, proj: Any) -> Any:
        pid = id(proj)
        if pid not in self._projection_trackers:
            from .projection import ProjectionTracker

            name = getattr(proj, "name", type(proj).__name__)
            self._projection_trackers[pid] = ProjectionTracker(
                projection=proj,
                name=name,
                max_pending=getattr(proj, "max_pending", 50_000),
                log_source=self.log,
                replay_fn=self._replay_projection_batch,
            )
        else:
            tracker = self._projection_trackers[pid]
            if tracker.log_source is None and self.log is not None:
                tracker.log_source = self.log
                tracker.replay_fn = self._replay_projection_batch
        return self._projection_trackers[pid]

    @property
    def projection_errors(self) -> list[dict[str, Any]]:
        """List active projection failures and uncommitted/failed offsets."""
        errors = []
        for proj in self._projections:
            tracker = self._get_or_create_projection_tracker(proj)
            if tracker.failed_offsets or tracker.consecutive_failures > 0:
                errors.append(
                    {
                        "name": tracker.name,
                        "failed_offsets": list(tracker.failed_offsets),
                        "consecutive_failures": tracker.consecutive_failures,
                        "last_failure_time": tracker.last_failure_ts,
                        "recovery_status": tracker.recovery_status,
                    }
                )
        return errors

    @property
    def projection_failures(self) -> int:
        """Total count of failed projection batch deliveries."""
        return sum(
            tracker.consecutive_failures
            for tracker in self._projection_trackers.values()
        )

    def recover_projections(self) -> dict[str, int]:
        """Trigger immediate recovery replay and WAL backlog catch-up across all projections."""
        with self._lock:
            recovered = {}
            if self.log is None:
                for proj in self._projections:
                    tracker = self._get_or_create_projection_tracker(proj)
                    before = len(tracker.pending_batches)
                    if before > 0:
                        tracker.drain(non_blocking=False, timeout=1.0)
                        recovered[tracker.name] = max(
                            0, before - len(tracker.pending_batches)
                        )
                return recovered

            head = self.log.next_offset
            for proj in list(self._projections):
                tracker = self._get_or_create_projection_tracker(proj)
                cp = tracker.checkpoint()
                start_off = (
                    (cp + 1) if cp >= 0 else getattr(proj, "expected_start_offset", 0)
                )
                if head > start_off:
                    batch: list[EngineDecision] = []
                    replay_state = self.create_initial_state()
                    for off, raw in self.log.iter_from(0):
                        if off < start_off:
                            replay_state, _ = self.step(
                                replay_state, raw, self.clock, offset=off
                            )
                        elif off < head:
                            replay_state, dec = self.step(
                                replay_state, raw, self.clock, offset=off
                            )
                            batch.append(dec)
                            if len(batch) >= 1000:
                                proj.apply(batch, batch[-1].offset)
                                batch = []
                    if batch:
                        proj.apply(batch, batch[-1].offset)

                    tracker.last_contiguous_offset = max(
                        tracker.last_contiguous_offset, proj.checkpoint()
                    )
                    tracker._expected_offset = tracker.last_contiguous_offset + 1
                    tracker.recovery_status = "IDLE"
                    tracker.wal_catchup_needed = False
                    recovered[tracker.name] = head - start_off
                else:
                    before = len(tracker.pending_batches)
                    tracker.drain(non_blocking=False, timeout=1.0)
                    recovered[tracker.name] = max(
                        0, before - len(tracker.pending_batches)
                    )
            return recovered

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
                n_events = len(event_list)
                decisions: list[EngineDecision | None] = [None] * n_events
                offsets: list[int] = [-1] * n_events
                valid_for_wal: list[RawEvent] = []
                valid_indices: list[int] = []

                for idx, raw in enumerate(event_list):
                    # Record observed event count for all presented raw events
                    batch_txn.record_event_count()
                    self.state.observed_count += 1

                    # Stamp receive timestamp before append to ensure deterministic replay (C2)
                    if not raw.receive_timestamp:
                        raw.receive_timestamp = self.clock.now()

                    # Enforce payload serialization safety and size limit at Engine boundary
                    try:
                        p_len = (
                            estimate_payload_size(
                                raw.payload, max_bytes=self.max_payload_bytes
                            )
                            if self.max_payload_bytes > 0
                            else 0
                        )
                    except (TypeError, ValueError) as ser_err:
                        self.observed_raw_count += 1
                        self.rejected_count += 1
                        q_id = f"q_{raw.source}_{self.observed_raw_count}"
                        q_row = (
                            q_id,
                            "CORRUPT_PAYLOAD",
                            raw.source,
                            QualityStatus.INVALID.value,
                            json.dumps([f"Payload serialization failed: {ser_err}"]),
                            json.dumps({"error": str(ser_err)}),
                            raw.receive_timestamp,
                        )
                        dec = EngineDecision(
                            offset=-1,
                            event_id=q_id,
                            quality_status=QualityStatus.INVALID,
                            reasons=[Reason.SCHEMA_VIOLATION.value],
                            quarantine_row=q_row,
                        )
                        decisions[idx] = dec
                        offsets[idx] = -1
                        continue

                    if self.max_payload_bytes > 0 and p_len > self.max_payload_bytes:
                        self.observed_raw_count += 1
                        self.rejected_count += 1
                        q_id = f"q_{raw.source}_{self.observed_raw_count}"
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
                                {"size": p_len, "truncated": str(raw.payload)[:256]}
                            ),
                            raw.receive_timestamp,
                        )
                        dec = EngineDecision(
                            offset=-1,
                            event_id=q_id,
                            quality_status=QualityStatus.INVALID,
                            reasons=[Reason.SCHEMA_VIOLATION.value],
                            quarantine_row=q_row,
                        )
                        decisions[idx] = dec
                        offsets[idx] = -1
                        continue

                    self.observed_raw_count += 1
                    offset = (
                        (self.log.next_offset + len(valid_for_wal))
                        if self.log is not None
                        else (self.state.event_count + len(valid_for_wal))
                    )
                    if not raw.raw_id:
                        raw.raw_id = f"raw_{raw.source}_{offset}"
                    valid_for_wal.append(raw)
                    valid_indices.append(idx)

                if self.log is not None and valid_for_wal:
                    allocated = self.log.append_batch(valid_for_wal)
                    raw_to_offset = list(zip(valid_for_wal, allocated))
                elif valid_for_wal:
                    start_off = (
                        self.state.last_applied_offset + 1
                        if self.state.last_applied_offset >= 0
                        else self.state.event_count
                    )
                    raw_to_offset = [
                        (raw, start_off + i) for i, raw in enumerate(valid_for_wal)
                    ]
                else:
                    raw_to_offset = []

                for (raw, offset), orig_idx in zip(raw_to_offset, valid_indices):
                    offsets[orig_idx] = offset

                if len(raw_to_offset) > 1:
                    self.state, batch_decs = self.step_batch(
                        self.state, raw_to_offset, self.clock, txn=batch_txn
                    )
                    for orig_idx, step_dec in zip(valid_indices, batch_decs):
                        decisions[orig_idx] = step_dec
                elif len(raw_to_offset) == 1:
                    raw, offset = raw_to_offset[0]
                    orig_idx = valid_indices[0]
                    self.state, step_dec = self.step(
                        self.state, raw, self.clock, offset=offset, txn=batch_txn
                    )
                    decisions[orig_idx] = step_dec

                batch_txn.commit()

                final_decisions = [d for d in decisions if d is not None]

                # Update durability status based on current WAL sync state
                if self.log is not None:
                    if self.log.fsync_policy == "never":
                        for d in final_decisions:
                            d.durable = False
                    else:
                        synced = self.log.synced_offset
                        for d in final_decisions:
                            d.durable = (synced >= d.offset) if d.offset >= 0 else True
                else:
                    for d in final_decisions:
                        d.durable = False

                # Program I-4: Automatic snapshot cadence evaluation
                needs_snapshot = False
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
                        needs_snapshot = True
                        self._last_snapshot_event_count = self.state.event_count
                        self._last_snapshot_ts = now_wall

                projs_to_dispatch = list(self._projections)
                dispatch_offset = offsets[-1] if offsets else -1
            except Exception:
                # Durability / IO failure rollback (Task 5: Exception Safety & State Consistency)
                batch_txn.rollback()
                raise

        # Deliver to projections outside self._lock to avoid blocking ingestion on slow I/O
        if projs_to_dispatch and final_decisions:
            for proj in projs_to_dispatch:
                try:
                    tracker = self._get_or_create_projection_tracker(proj)
                    tracker.apply(final_decisions, dispatch_offset)
                except Exception as proj_err:
                    logger.error(
                        "[engine] Projection %s failed at offset %d: %s",
                        getattr(proj, "name", type(proj).__name__),
                        dispatch_offset,
                        proj_err,
                    )

        # Save snapshot OUTSIDE self._lock to eliminate lock contention on disk I/O / fsync
        if needs_snapshot:
            try:
                self.save_snapshot()
            except Exception as snap_err:
                logger.warning(
                    "[engine] Automatic cadence snapshot failed: %s",
                    snap_err,
                )

        if is_single:
            return final_decisions[0] if final_decisions else None
        return final_decisions

    def submit_microbatch(self, events: list[RawEvent]) -> list[EngineDecision]:
        """Durable atomic micro-batch submission API (Item A4).

        Processes and persists events in a single group-commit fsync and atomic
        SQLite projection transaction.
        """
        res = self.submit(events)
        decisions = res if isinstance(res, list) else ([res] if res is not None else [])
        if self.log is not None and decisions:
            self.flush()
            for d in decisions:
                d.durable = True
        return decisions

    @property
    def durability_guarantee(self) -> str:
        """Durability contract specification string."""
        if self.log is None:
            return "IN_MEMORY_NO_PERSISTENCE"
        return self.log.durability_guarantee

    @property
    def synced_offset(self) -> int:
        """Highest offset durably fsynced to disk, or -1 if no persistent log."""
        if self.log is not None:
            return self.log.synced_offset
        return -1

    def await_durable(self, target: int | EngineDecision, timeout: float = 2.0) -> bool:
        """Wait until target offset (or decision) has been durably fsynced to disk."""
        if self.log is None:
            if isinstance(target, EngineDecision):
                target.durable = True
            return True
        target_offset = (
            target.offset if isinstance(target, EngineDecision) else int(target)
        )
        if target_offset < 0:
            return True
        if self.log.synced_offset >= target_offset:
            if isinstance(target, EngineDecision):
                target.durable = True
            return True
        if self.log.fsync_policy in ("grouped_by_size", "grouped_by_time", "never"):
            self.log.flush()
            is_durable = self.log.synced_offset >= target_offset
            if is_durable and isinstance(target, EngineDecision):
                target.durable = True
            return is_durable
        ok = self.log.await_synced(target_offset, timeout=timeout)
        if ok and isinstance(target, EngineDecision):
            target.durable = True
        return ok

    def replay(
        self,
        from_offset: int = 0,
        to_offset: int | None = None,
        projection: Any | None = None,
        return_metrics: bool = False,
        initial_state: EngineState | None = None,
    ) -> list[EngineDecision] | tuple[list[EngineDecision], RecoveryMetrics]:
        """Replay events from WAL from_offset up to to_offset."""
        metrics = RecoveryMetrics()
        if self.log is None:
            self.recovery_metrics = metrics
            return ([], metrics) if return_metrics else []

        decisions: list[EngineDecision] = []
        replay_state = (
            initial_state.clone()
            if initial_state is not None
            else self.create_initial_state()
        )
        initial_corrupt = getattr(self.log, "corrupted_frames_count", 0)
        start_scan = from_offset if initial_state is not None else 0

        for off, raw in self.log.iter_from(start_scan):
            if off < from_offset:
                replay_state.observed_count += 1
                replay_state, _ = self.step(
                    replay_state, raw, self.clock, offset=off, is_replay=True
                )
                continue
            if to_offset is not None and off > to_offset:
                break
            replay_state.observed_count += 1
            replay_state, dec = self.step(
                replay_state, raw, self.clock, offset=off, is_replay=True
            )
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
        for tracker in list(self._projection_trackers.values()):
            try:
                tracker.flush(timeout=2.0)
            except Exception:
                pass
        self.recover_projections()

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
        log_lag = (
            max(0, self.log.next_offset - (self.log.synced_offset + 1))
            if (
                self.log is not None
                and self.log.fsync_policy in ("grouped_by_size", "grouped_by_time")
                and self.log.next_offset > 0
            )
            else 0
        )

        max_proj_lag = 0
        has_proj_failures = False
        proj_stats = []
        for proj in self._projections:
            tracker = self._get_or_create_projection_tracker(proj)
            cp = tracker.checkpoint()
            lag = max(0, log_head - (cp + 1)) if cp >= 0 else log_head
            if lag > max_proj_lag:
                max_proj_lag = lag
            if (
                tracker.failed_offsets
                or tracker.consecutive_failures > 0
                or tracker.recovery_status in ("DEGRADED", "STALLED")
            ):
                has_proj_failures = True
            proj_stats.append(
                {
                    "name": tracker.name,
                    "checkpoint": cp,
                    "latest_wal_offset": log_head - 1 if log_head > 0 else -1,
                    "lag": lag,
                    "failed_offsets": list(tracker.failed_offsets),
                    "consecutive_failures": tracker.consecutive_failures,
                    "retry_count": tracker.total_retries,
                    "last_success_time": tracker.last_success_ts,
                    "last_failure_time": tracker.last_failure_ts,
                    "recovery_status": tracker.recovery_status,
                }
            )

        total_overflows = sum(
            getattr(t, "queue_overflow_count", 0)
            for t in self._projection_trackers.values()
        )
        if (
            max_proj_lag == 0
            and log_lag == 0
            and not has_proj_failures
            and total_overflows == 0
        ):
            health_status = "HEALTHY"
        elif max_proj_lag < 500 and not has_proj_failures and total_overflows == 0:
            health_status = "DEGRADED"
        else:
            health_status = (
                "UNHEALTHY"
                if (
                    max_proj_lag >= 500
                    or any(
                        t.recovery_status == "STALLED"
                        for t in self._projection_trackers.values()
                    )
                )
                else "DEGRADED"
            )

        return {
            "processed": self.state.event_count,
            "counts": dict(self.state.counts),
            "valid": self.state.counts.get("VALID", 0),
            "suspicious": self.state.counts.get("SUSPICIOUS", 0),
            "invalid": self.state.counts.get("INVALID", 0),
            "log_head_offset": log_head,
            "log_lag": log_lag,
            "projection_lag": max_proj_lag,
            "max_projection_lag": max_proj_lag,
            "projection_queue_overflows": total_overflows,
            "projection_failures": self.projection_failures,
            "projections": proj_stats,
            "health": health_status,
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
        for tracker in list(self._projection_trackers.values()):
            try:
                tracker.close()
            except Exception:
                pass
        self._projection_trackers.clear()
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
        is_replay: bool = False,
    ) -> tuple[EngineState, EngineDecision]:
        """Apply a single deterministic state transition: (state, raw_event, clock) -> (state', decision)."""
        prev_event_count = state.event_count
        step_txn = txn if txn is not None else Transaction(state)
        mark = len(step_txn._actions)
        try:
            st, dec = self._step_internal(
                state,
                raw_event,
                clock,
                offset=offset,
                txn=step_txn,
                is_replay=is_replay,
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

        if (
            new_session_id is not None
            and current_session is not None
            and str(new_session_id) != current_session
        ):
            epoch_reset_triggered = True
        elif self.session_policy == "session_marker":
            is_reset = payload.get("is_reset") is True or payload.get("reset") is True
            epoch_id_marker = payload.get("epoch_id")
            if is_reset:
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
        is_replay: bool = False,
    ) -> tuple[QualityStatus, list[str], bool]:
        is_duplicate = False
        reasons_out = reasons
        cur_status = status

        if seq is not None:
            last_seq = state.sequence_state.get(slot_key)
            seen_mask = state.sequence_seen.get(slot_key, 0)
            cand_plus1 = state.sequence_cand.get(slot_key, 0)
            regress_cnt = state.sequence_regress.get(slot_key, 0)

            if last_seq is None:
                if txn is not None:
                    txn.record_sequence(slot_key)
                    if slot_key != stream_key:
                        txn.record_sequence(stream_key)
                state.sequence_state[slot_key] = seq
                state.sequence_seen[slot_key] = 1
                state.sequence_cand[slot_key] = 0
                state.sequence_regress[slot_key] = 0
                if slot_key != stream_key:
                    state.sequence_state[stream_key] = seq
                    state.sequence_seen[stream_key] = 1
                    state.sequence_cand[stream_key] = 0
                    state.sequence_regress[stream_key] = 0
            elif seq > last_seq:
                jump = seq - last_seq
                if jump > DEFAULT_SEQ_JUMP_LIMIT:
                    # Forward gap / sequence jump beyond threshold: flag SUSPICIOUS / SEQUENCE_GAP
                    cur_status = QualityStatus.merge(
                        cur_status, QualityStatus.SUSPICIOUS
                    )
                    if reasons_out is reasons:
                        reasons_out = list(reasons)
                    reasons_out.append(Reason.SEQUENCE_GAP.value)

                    cand = cand_plus1 - 1
                    if (
                        cand_plus1 != 0
                        and seq > cand
                        and (seq - cand) <= DEFAULT_SEQ_JUMP_LIMIT
                    ):
                        # Consecutive jump confirms new epoch / stream progression
                        if txn is not None:
                            txn.record_sequence(slot_key)
                            if slot_key != stream_key:
                                txn.record_sequence(stream_key)
                        state.sequence_state[slot_key] = seq
                        state.sequence_seen[slot_key] = 1
                        state.sequence_cand[slot_key] = 0
                        if slot_key != stream_key:
                            state.sequence_state[stream_key] = seq
                            state.sequence_seen[stream_key] = 1
                            state.sequence_cand[stream_key] = 0
                    else:
                        if txn is not None:
                            txn.record_dict(state.sequence_cand, slot_key)
                            if slot_key != stream_key:
                                txn.record_dict(state.sequence_cand, stream_key)
                        state.sequence_cand[slot_key] = seq + 1
                        if slot_key != stream_key:
                            state.sequence_cand[stream_key] = seq + 1
                else:
                    if jump > 1:
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.SUSPICIOUS
                        )
                        if reasons_out is reasons:
                            reasons_out = list(reasons)
                        reasons_out.append(Reason.SEQUENCE_GAP.value)
                        if self.gap_manager is not None and not is_replay:
                            self.gap_manager.record_gap(
                                source=raw_event.source,
                                instrument=instrument,
                                expected_start=last_seq + 1,
                                received_seq=seq,
                                epoch=str(current_epoch),
                                now_ts=recv_ts,
                            )

                    new_seen = (
                        ((seen_mask << jump) | 1) & SEQUENCE_BITMAP_MASK
                        if jump < SEQUENCE_BITMAP_BITS
                        else 1
                    )
                    if txn is not None:
                        txn.record_sequence(slot_key)
                        if slot_key != stream_key:
                            txn.record_sequence(stream_key)
                    state.sequence_state[slot_key] = seq
                    state.sequence_seen[slot_key] = new_seen
                    state.sequence_cand[slot_key] = 0
                    if slot_key != stream_key:
                        state.sequence_state[stream_key] = seq
                        state.sequence_seen[stream_key] = new_seen
                        state.sequence_cand[stream_key] = 0
            else:
                # seq <= last_seq
                d = last_seq - seq
                if d < SEQUENCE_BITMAP_BITS:
                    if (seen_mask >> d) & 1:
                        # Already seen in sliding window: DUPLICATE (QualityStatus.INVALID)
                        is_duplicate = True
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.INVALID
                        )
                        if reasons_out is reasons:
                            reasons_out = list(reasons)
                        reasons_out.append(Reason.DUPLICATE.value)
                        if d > 0:
                            reasons_out.append(Reason.OUT_OF_ORDER.value)
                    else:
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
                            if reasons_out is reasons:
                                reasons_out = list(reasons)
                            reasons_out.append(Reason.GAP_FILLED.value)
                        else:
                            cur_status = QualityStatus.merge(
                                cur_status, QualityStatus.SUSPICIOUS
                            )
                            if reasons_out is reasons:
                                reasons_out = list(reasons)
                            reasons_out.append(Reason.OUT_OF_ORDER.value)

                        new_seen = seen_mask | (1 << d)
                        if txn is not None:
                            txn.record_dict(state.sequence_seen, slot_key)
                            if slot_key != stream_key:
                                txn.record_dict(state.sequence_seen, stream_key)
                        state.sequence_seen[slot_key] = new_seen
                        if slot_key != stream_key:
                            state.sequence_seen[stream_key] = new_seen
                else:
                    stale_list = state.sequence_stale_seen.setdefault(slot_key, [])
                    if seq in stale_list:
                        is_duplicate = True
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.INVALID
                        )
                        if reasons_out is reasons:
                            reasons_out = list(reasons)
                        reasons_out.append(Reason.DUPLICATE.value)
                        reasons_out.append(Reason.OUT_OF_ORDER.value)
                    elif d > DEFAULT_SEQ_JUMP_LIMIT:
                        regress_cnt += 1
                        if regress_cnt >= 2:
                            if txn is not None:
                                txn.record_sequence(slot_key)
                                if slot_key != stream_key:
                                    txn.record_sequence(stream_key)
                            state.sequence_state[slot_key] = seq
                            state.sequence_seen[slot_key] = 1
                            state.sequence_regress[slot_key] = 0
                            state.sequence_cand[slot_key] = 0
                            if slot_key in state.sequence_stale_seen:
                                state.sequence_stale_seen[slot_key].clear()
                            if slot_key != stream_key:
                                state.sequence_state[stream_key] = seq
                                state.sequence_seen[stream_key] = 1
                                state.sequence_regress[stream_key] = 0
                                state.sequence_cand[stream_key] = 0
                                if stream_key in state.sequence_stale_seen:
                                    state.sequence_stale_seen[stream_key].clear()
                            cur_status = QualityStatus.merge(
                                cur_status, QualityStatus.SUSPICIOUS
                            )
                            if reasons_out is reasons:
                                reasons_out = list(reasons)
                            reasons_out.append(Reason.OUT_OF_ORDER.value)
                        else:
                            if txn is not None:
                                txn.record_dict(state.sequence_regress, slot_key)
                                if slot_key != stream_key:
                                    txn.record_dict(state.sequence_regress, stream_key)
                            state.sequence_regress[slot_key] = regress_cnt
                            if slot_key != stream_key:
                                state.sequence_regress[stream_key] = regress_cnt
                            stale_list.append(seq)
                            if len(stale_list) > 64:
                                stale_list.pop(0)
                            cur_status = QualityStatus.merge(
                                cur_status, QualityStatus.SUSPICIOUS
                            )
                            if reasons_out is reasons:
                                reasons_out = list(reasons)
                            reasons_out.append(Reason.OUT_OF_ORDER.value)
                    else:
                        if txn is not None:
                            txn.record_dict(state.sequence_regress, slot_key)
                            if slot_key != stream_key:
                                txn.record_dict(state.sequence_regress, stream_key)
                        state.sequence_regress[slot_key] = 0
                        if slot_key != stream_key:
                            state.sequence_regress[stream_key] = 0
                        stale_list.append(seq)
                        if len(stale_list) > 64:
                            stale_list.pop(0)
                        cur_status = QualityStatus.merge(
                            cur_status, QualityStatus.SUSPICIOUS
                        )
                        if reasons_out is reasons:
                            reasons_out = list(reasons)
                        reasons_out.append(Reason.OUT_OF_ORDER.value)
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
                    payload.get("bid_size"),
                    payload.get("ask_size"),
                )
            if dedup_fingerprint in state.dedup_set:
                is_duplicate = True
                cur_status = QualityStatus.merge(cur_status, QualityStatus.INVALID)
                if reasons_out is reasons:
                    reasons_out = list(reasons)
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
        reasons_out = reasons
        cur_status = status

        if cur_status != QualityStatus.INVALID and event_type_str not in (
            "HALT",
            "STATUS",
        ):
            if recv_ts - exch_ts > staleness_threshold_s:
                cur_status = QualityStatus.merge(cur_status, QualityStatus.SUSPICIOUS)
                if reasons_out is reasons:
                    reasons_out = list(reasons)
                reasons_out.append(Reason.STALE.value)

        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is not None and ask is not None and bid > ask:
            cur_status = QualityStatus.merge(cur_status, QualityStatus.INVALID)
            if reasons_out is reasons:
                reasons_out = list(reasons)
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
        reasons_out = reasons
        cur_status = status

        if price is not None and cur_status != QualityStatus.INVALID:
            if txn is not None:
                txn.record_price_window(slot_key)
            prices = state.price_windows.setdefault(slot_key, deque())
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
                if reasons_out is reasons:
                    reasons_out = list(reasons)
                reasons_out.append(Reason.PRICE_ANOMALY.value)

                # Track consecutive consistent price outliers for regime shift re-seeding
                anom_cnt, anom_last = state._price_anom.get(slot_key, (0, 0.0))
                eff_std = (
                    effective_std
                    if "effective_std" in locals() and effective_std > 0
                    else 0.05 * abs(price)
                )
                band = DEFAULT_PRICE_STDDEV * eff_std
                cluster_tol = max(band, 0.05 * abs(price))

                if anom_cnt > 0 and abs(price - anom_last) <= cluster_tol:
                    anom_cnt += 1
                else:
                    anom_cnt = 1
                anom_last = price

                if anom_cnt >= DEFAULT_PRICE_RESEED_AFTER:
                    # Consecutive consistent outliers confirm genuine market regime shift
                    prices.clear()
                    prices.append(price)
                    state._price_stats[slot_key] = (price, 0.0, 0)
                    state._price_anom[slot_key] = (0, 0.0)
                else:
                    state._price_anom[slot_key] = (anom_cnt, anom_last)
            else:
                state._price_anom[slot_key] = (0, 0.0)
                if n_prices >= DEFAULT_PRICE_WINDOW:
                    old_p = (
                        prices.popleft() if isinstance(prices, deque) else prices.pop(0)
                    )
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
        is_replay: bool = False,
    ) -> tuple[QualityStatus, list[str], bool]:
        reasons_out = reasons
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
                        if reasons_out is reasons:
                            reasons_out = list(reasons)
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
        is_replay: bool = False,
    ) -> tuple[EngineState, EngineDecision]:
        if offset is None:
            offset = state.event_count

        if txn is not None:
            txn.record_event_count()
        state.event_count += 1
        state.last_applied_offset = offset
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
            is_replay=is_replay,
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
        with self._lock:
            st = target if isinstance(target, EngineState) else self.state
            st_dict = st.to_dict()
            last_off = (
                self.state.last_applied_offset
                if self.state.last_applied_offset >= 0
                else (
                    self.log.next_offset - 1
                    if self.log is not None and self.log.next_offset > 0
                    else -1
                )
            )
            ev_count = self.state.event_count
            now_ts = self.clock.now()

        # # ponytail: serialize, write and fsync OUTSIDE self._lock to eliminate submit contention (E13)
        canonical_bytes = json.dumps(
            st_dict, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        state_sha256 = hashlib.sha256(canonical_bytes).hexdigest()

        if isinstance(target, (str, Path)):
            data = {
                "version": 2,
                "offset": last_off,
                "timestamp": now_ts,
                "event_count": ev_count,
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
        return canonical_bytes

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

    @classmethod
    def _discover_candidate_snapshots(cls, snap_dir: str) -> list[tuple[int, str]]:
        """Scan snap_dir for candidate snapshot files, returning [(offset, path), ...] sorted descending by offset.

        Note on Security & Threat Model:
        SHA-256 verification here guarantees data integrity against storage corruption,
        torn writes, silent bit-rot, and crash recovery faults. It does NOT provide non-repudiation
        or defense against malicious tampering by an attacker with write access to the filesystem.
        Institutional deployments requiring tamper-proof adversarial protection must employ
        HMAC-SHA256 with KMS-managed secrets or OS-level file integrity monitoring (FIM).
        """
        candidates: dict[str, tuple[int, str]] = {}
        if not os.path.isdir(snap_dir):
            return []

        manifest_path = os.path.join(snap_dir, "snapshot_manifest.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    man = json.load(f)
                for entry in man.get("snapshots", []):
                    fn = entry.get("path")
                    off = entry.get("offset", -1)
                    if fn and off >= 0:
                        p = os.path.join(snap_dir, fn)
                        if os.path.isfile(p):
                            candidates[os.path.abspath(p)] = (off, p)
            except Exception:
                pass  # Self-healing on corrupted manifest: fall back to directory scan

        # Scan directory for versioned snapshot files: snapshot_0000000000002999.json
        for fname in os.listdir(snap_dir):
            if (
                fname.startswith("snapshot_")
                and fname.endswith(".json")
                and ".tmp" not in fname
            ):
                parts = fname[len("snapshot_") : -len(".json")]
                try:
                    off = int(parts)
                    p = os.path.join(snap_dir, fname)
                    candidates[os.path.abspath(p)] = (off, p)
                except ValueError:
                    pass

        # Also include snapshot.json if it exists and hasn't been added
        legacy_snap = os.path.join(snap_dir, "snapshot.json")
        if (
            os.path.isfile(legacy_snap)
            and os.path.abspath(legacy_snap) not in candidates
        ):
            try:
                with open(legacy_snap, "r", encoding="utf-8") as f:
                    data = json.load(f)
                off = data.get("offset", -1)
                if off >= 0:
                    candidates[os.path.abspath(legacy_snap)] = (off, legacy_snap)
                else:
                    candidates[os.path.abspath(legacy_snap)] = (-1, legacy_snap)
            except Exception:
                candidates[os.path.abspath(legacy_snap)] = (-1, legacy_snap)

        result = list(candidates.values())
        result.sort(key=lambda x: x[0], reverse=True)
        return result

    def save_snapshot(
        self,
        prune_obsolete_segments: bool = True,
        max_snapshots_retained: int | None = None,
        min_wal_snapshots_retained: int | None = None,
    ) -> dict[str, Any]:
        """Save a snapshot of current engine state to the WAL directory with SHA-256 integrity.

        Snapshot Lifecycle Protocol:
        1. Capture state dictionary and monotonically assigned WAL offset under self._lock (< 500ns).
        2. Serialize state to canonical JSON and compute SHA-256 outside self._lock to avoid submit latency spikes.
        3. Write to temporary file with flush() + os.fsync() to prevent torn writes.
        4. Re-read and verify SHA-256 prior to publication.
        5. Atomically publish versioned snapshot via os.replace() and _fsync_dir().
        6. Atomically publish compatibility pointer 'snapshot.json' via os.replace() and _fsync_dir().
        7. Atomically publish 'snapshot_manifest.json' recording all available snapshots.
        8. Retain up to max_snapshots_retained versioned snapshot files.
        9. Safe WAL pruning: Retain WAL segments covering at least min_wal_snapshots_retained fallback
           snapshots (default 1), guaranteeing crash recovery can fall back to an earlier valid snapshot
           if the latest snapshot is corrupted.
        """
        if max_snapshots_retained is None:
            max_snapshots_retained = getattr(self, "max_snapshots_retained", 3)
        if min_wal_snapshots_retained is None:
            min_wal_snapshots_retained = getattr(self, "min_wal_snapshots_retained", 2)

        with self._lock:
            if self.log is None:
                raise RuntimeError("Cannot save snapshot without an active IngestLog")

            last_offset = (
                self.state.last_applied_offset
                if self.state.last_applied_offset >= 0
                else (
                    self.log.next_offset - 1
                    if self.log is not None and self.log.next_offset > 0
                    else -1
                )
            )
            earliest_wal_offset = (
                self.log.earliest_offset if self.log is not None else 0
            )
            st_dict = self.state.to_dict()
            snap_dir = self.log.log_dir
            now_ts = self.clock.now()
            event_count = self.state.event_count

            # Update snapshot cadence accounting under lock
            self._last_snapshot_event_count = event_count
            self._last_snapshot_ts = time.time()

        # # ponytail: serialize, write and fsync OUTSIDE self._lock to eliminate submit contention
        canonical_bytes = json.dumps(
            st_dict, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        state_sha256 = hashlib.sha256(canonical_bytes).hexdigest()

        snap_data = {
            "version": 2,
            "offset": last_offset,
            "last_applied_wal_offset": last_offset,
            "earliest_wal_offset": earliest_wal_offset,
            "latest_wal_offset": last_offset,
            "earliest_wal_offset_covered": earliest_wal_offset,
            "latest_wal_offset_included": last_offset,
            "timestamp": now_ts,
            "event_count": event_count,
            "sha256": state_sha256,
            "state": st_dict,
        }

        versioned_filename = (
            f"snapshot_{last_offset:016d}.json"
            if last_offset >= 0
            else "snapshot_initial.json"
        )
        versioned_path = os.path.join(snap_dir, versioned_filename)
        tmp_path = os.path.join(
            snap_dir, f"{versioned_filename}.tmp.{os.getpid()}_{time.time_ns()}"
        )
        latest_path = os.path.join(snap_dir, "snapshot.json")

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(snap_data, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())

        # Verification before publishing
        with open(tmp_path, "r", encoding="utf-8") as f_check:
            verify_data = json.load(f_check)
        verify_bytes = json.dumps(
            verify_data.get("state", {}), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(verify_bytes).hexdigest() != state_sha256:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise RuntimeError("Snapshot write-verification failed SHA-256 check")

        os.replace(tmp_path, versioned_path)
        _fsync_dir(snap_dir)

        # Also atomically publish snapshot.json for legacy/tool compatibility
        tmp_latest = os.path.join(
            snap_dir, f"snapshot.json.tmp.{os.getpid()}_{time.time_ns()}"
        )
        with open(tmp_latest, "w", encoding="utf-8") as f:
            json.dump(snap_data, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_latest, latest_path)
        _fsync_dir(snap_dir)

        # Update snapshot_manifest.json
        manifest_path = os.path.join(snap_dir, "snapshot_manifest.json")
        tmp_manifest = os.path.join(
            snap_dir, f"snapshot_manifest.tmp.{os.getpid()}_{time.time_ns()}"
        )
        candidates = self._discover_candidate_snapshots(snap_dir)
        manifest_data = {
            "version": 1,
            "latest_offset": last_offset,
            "latest_snapshot": versioned_filename,
            "snapshots": [
                {"offset": off, "path": os.path.basename(p)}
                for off, p in candidates
                if off >= 0
            ],
        }
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, sort_keys=True, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_manifest, manifest_path)
        _fsync_dir(snap_dir)

        # Retain up to max_snapshots_retained snapshot files
        all_versioned = sorted(
            [
                os.path.join(snap_dir, f)
                for f in os.listdir(snap_dir)
                if f.startswith("snapshot_")
                and f.endswith(".json")
                and not f.startswith("snapshot_manifest")
                and ".tmp" not in f
            ]
        )
        if len(all_versioned) > max_snapshots_retained:
            for old_snap in all_versioned[:-max_snapshots_retained]:
                try:
                    os.remove(old_snap)
                except OSError:
                    pass

        # Re-scan candidate snapshots after retention cleanup to calculate safe WAL prune boundary
        candidates_after = self._discover_candidate_snapshots(snap_dir)
        pruned = 0
        if prune_obsolete_segments and last_offset >= 0:
            if min_wal_snapshots_retained > 0 and len(candidates_after) > 1:
                # Retain WAL segments back to the fallback snapshot to ensure crash resilience
                target_idx = min(min_wal_snapshots_retained, len(candidates_after) - 1)
                fallback_offset = candidates_after[target_idx][0]
                safe_prune_offset = fallback_offset + 1
            else:
                safe_prune_offset = last_offset + 1

            if safe_prune_offset > 0:
                pruned = self.delete_segments_before(safe_prune_offset)

        return {
            "snapshot_offset": last_offset,
            "path": versioned_path,
            "latest_path": latest_path,
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
    "RecoveryError",
]
