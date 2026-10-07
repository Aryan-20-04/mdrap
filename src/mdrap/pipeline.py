"""Pipeline Orchestration and Event Stream Execution Engine.

This module coordinates the end-to-end life cycle of market data events in MDRAP:
  `raw ingress -> security check -> archive -> normalize -> quality -> reconcile -> telemetry -> storage`

Architectural Foundations (MDRAP Spec §14 & §26):
  1. In-Process Pipeline:
     Normalization, quality evaluation, and reconciliation run in the caller's process.
     Persistence uses a bounded background writer by default (or a synchronous writer when
     configured). The in-memory queue is not a durability boundary; returned events can be lost
     if the process exits before storage or dead-letter persistence completes.
  2. Zero-Allocation Hot-Path Constants:
     Database lineage tracking requires JSON arrays of validations and transformations for
     every event. Re-serializing identical lists via `json.dumps()` on every tick consumes
     significant CPU cycles. `_VALIDATIONS_RUN_JSON` and `_TRANSFORMATIONS_JSON` are precomputed
     once at module import, eliminating heap allocations during per-event processing.
  3. Deterministic Garbage Collection:
     Default Python generational GC thresholds (700, 10, 10) trigger erratic GC sweeps during
     high-frequency tick bursts, causing latency spikes. Tuning the threshold via
     `gc.set_threshold(100_000, 10, 10)` defers GC passes until explicit `flush()` invocations.
  4. Optional Raw Archival & Quarantine:
     When an archive is configured, raw events are archived before normalization. If
     normalization fails, a quarantined event is created and queued for persistence.
"""

from __future__ import annotations

from contextlib import contextmanager
import gc
import hashlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
import warnings
from typing import Any, Callable, TYPE_CHECKING

from .clock import Clock, SystemClock
from .gateway import SchemaError, ingest, normalize
from .metrics import RunMetrics
from .models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from .quality import QualityEngine
from .reconciliation import Reconciler, ReliabilityTracker
from .storage import Store

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .archive import RawArchive
    from .analytics import MarketAnalytics
    from .bbo import BBOEngine
    from .watchdog import SourceWatchdog
    from .security import SecurityManager
    from .protocols import AppendStorageSink, StorageBackend, QualityEvaluator

__stability__ = "stable"

# ---------------------------------------------------------------------------
# Pipeline Orchestration & Memory Tuning Constants (Spec §14 & §26)
# ---------------------------------------------------------------------------
DEFAULT_BATCH_SIZE: int = 2000
BATCH_SIZE: int = DEFAULT_BATCH_SIZE  # Backward-compatible module alias
DEFAULT_FLUSH_INTERVAL_S: float = 1.0
MAX_QUARANTINE_PAYLOAD_BYTES: int = 65536  # 64 KiB safety collar against storage DoS
DEFAULT_GC_GEN0_THRESHOLD: int = 50_000
DEFAULT_GC_GEN1_THRESHOLD: int = 10
DEFAULT_GC_GEN2_THRESHOLD: int = 10
INV_NS_PER_SECOND: float = (
    1e-9  # Inverse nanoseconds multiplier for zero-division latency calculation
)
# Timing sampling: only measure perf_counter_ns on 1-in-(mask+1) events when enabled.
# 0 = measure every event (default, no sampling).  0x3F = 1-in-64.  0xFF = 1-in-256.
# Controlled via MDRAP_TIMING_SAMPLE_MASK env var or Pipeline(timing_sample_mask=...).
DEFAULT_TIMING_SAMPLE_MASK: int = 0


class WriterFailure(RuntimeError):
    """Raised when storage and its dead-letter fallback both fail."""


class JournalWriteError(RuntimeError):
    """Raised when writing to the write-ahead journal fails under fail_closed policy."""


# Precomputed immutable JSON strings: eliminates per-event serialization overhead
_VALIDATIONS_RUN_JSON = json.dumps(
    [
        "schema",
        "sequence_gap",
        "duplicate",
        "ordering",
        "staleness",
        "price_sanity",
        "quote_consistency",
    ]
)
_TRANSFORMATIONS_JSON = {
    False: json.dumps(["ingest", "normalize", "quality_evaluate"]),
    True: json.dumps(["ingest", "normalize", "quality_evaluate", "reconcile"]),
}


_CACHED_CODE_VERSION: str | None = None


def _code_version() -> str:
    """Retrieve Git commit SHA once from MDRAP directory and cache globally for lineage records."""
    global _CACHED_CODE_VERSION
    if _CACHED_CODE_VERSION is not None:
        return _CACHED_CODE_VERSION
    repo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            _CACHED_CODE_VERSION = out.stdout.strip()
            return _CACHED_CODE_VERSION
    except Exception:
        pass
    _CACHED_CODE_VERSION = "unversioned"
    return _CACHED_CODE_VERSION


@contextmanager
def tuned_gc():
    """Context manager for elevated gen-0 GC threshold during hot paths, restored on exit."""
    old = gc.get_threshold()
    try:
        gc.set_threshold(
            DEFAULT_GC_GEN0_THRESHOLD,
            DEFAULT_GC_GEN1_THRESHOLD,
            DEFAULT_GC_GEN2_THRESHOLD,
        )
        yield
    finally:
        gc.set_threshold(*old)


def _safe_payload_json(payload: Any) -> str:
    """Truncate payloads > 64 KiB with sha256 metadata to prevent storage DoS."""
    s = json.dumps(payload, default=str)
    if len(s) > MAX_QUARANTINE_PAYLOAD_BYTES:
        h = hashlib.sha256(s.encode("utf-8")).hexdigest()
        return json.dumps(
            {
                "truncated": True,
                "sha256": h,
                "orig_bytes": len(s),
                "prefix": s[:MAX_QUARANTINE_PAYLOAD_BYTES],
            }
        )
    return s


class Pipeline:
    """Synchronous market data pipeline orchestrator."""

    def __init__(
        self,
        store: Store | StorageBackend | AppendStorageSink,
        quality: QualityEngine | QualityEvaluator | None = None,
        reliability: ReliabilityTracker | None = None,
        archive: RawArchive | None = None,
        analytics: MarketAnalytics | None = None,
        bbo: BBOEngine | None = None,
        watchdog: SourceWatchdog | None = None,
        security: SecurityManager | None = None,
        flush_interval_s: float = DEFAULT_FLUSH_INTERVAL_S,
        async_writer: bool | None = None,
        timing_sample_mask: int | None = None,
        pre_evaluate_hook: Callable[[CanonicalEvent], CanonicalEvent | None]
        | None = None,
        post_evaluate_hook: Callable[[CanonicalEvent], CanonicalEvent | None]
        | None = None,
        journal: bool | str | None = None,
        durability_policy: str = "fail_closed",
        clock: Clock | None = None,
    ):
        warnings.warn(
            "Pipeline is deprecated in MDRAP v3.0.0; use IngestLog, Engine, and SQLiteProjection instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.store = store
        self.clock = clock if clock is not None else SystemClock()
        self.durability_policy: str = (
            durability_policy
            if durability_policy is not None
            else os.environ.get("MDRAP_DURABILITY_POLICY", "fail_closed")
        ).lower()
        if self.durability_policy not in ("fail_closed", "fail_open_loudly"):
            raise ValueError(f"Invalid durability_policy: {self.durability_policy}")
        self.is_degraded: bool = False
        self.journal_failures: int = 0
        self.pre_evaluate_hook = pre_evaluate_hook
        self.post_evaluate_hook = post_evaluate_hook
        if quality is not None:
            self.quality = quality
        else:
            try:
                from .fastpath import FastQualityEngine, is_available

                self.quality = (
                    FastQualityEngine(clock=self.clock) if is_available() else QualityEngine(clock=self.clock)
                )
            except Exception as exc:
                logger.warning(
                    "Fast quality engine initialization failed; using Python engine: %s",
                    exc,
                )
                self.quality = QualityEngine(clock=self.clock)
        self.reliability = reliability or ReliabilityTracker()
        self.reconciler = Reconciler(self.reliability)
        self.metrics = RunMetrics()
        self.code_version = _code_version()
        self.archive = archive
        self.analytics = analytics
        self.bbo = bbo
        self.watchdog = watchdog
        self.security = security
        self.flush_interval_s = flush_interval_s
        self._last_flush_ts = time.time()

        self._canonical_batch: list[CanonicalEvent] = []
        self._quarantine_batch: list[tuple] = []
        self._lineage_batch: list[tuple] = []
        self._pending_raw_payloads: dict[str, Any] = {}

        # Timing sampling: skip perf_counter_ns on most events when mask > 0
        if timing_sample_mask is not None:
            self._timing_sample_mask: int = timing_sample_mask
        else:
            env_mask = os.environ.get("MDRAP_TIMING_SAMPLE_MASK", "")
            self._timing_sample_mask = (
                int(env_mask, 0) if env_mask else DEFAULT_TIMING_SAMPLE_MASK
            )
        self._event_counter: int = 0

        if "MDRAP_ASYNC_WRITER" in os.environ:
            warnings.warn(
                "Environment variable 'MDRAP_ASYNC_WRITER' is deprecated in MDRAP v3.0.0. "
                "Configure durability and write policy explicitly via IngestLog / Engine / SQLiteProjection configuration.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._async_writer_enabled = (
            async_writer
            if async_writer is not None
            else os.environ.get("MDRAP_ASYNC_WRITER", "1").lower()
            in ("1", "true", "yes")
        )
        self._last_writer_exc: Exception | None = None
        self._writer_failure: WriterFailure | None = None
        if self._async_writer_enabled:
            self._write_queue: queue.Queue = queue.Queue(maxsize=128)
            self._writer_stop = threading.Event()
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="mdrap-writer"
            )
            self._writer_thread.start()

        # Journal-first durability (Spec §14, §26; Audit C1)
        if "MDRAP_DISABLE_JOURNAL" in os.environ:
            warnings.warn(
                "Environment variable 'MDRAP_DISABLE_JOURNAL' is deprecated in MDRAP v3.0.0. "
                "IngestLog WAL is the mandatory durability boundary; use explicit configuration.",
                DeprecationWarning,
                stacklevel=2,
            )
        if journal is not None:
            self._journal_enabled = bool(journal)
            self._journal_path = journal if isinstance(journal, str) else None
        else:
            self._journal_enabled = os.environ.get(
                "MDRAP_DISABLE_JOURNAL", "0"
            ).lower() not in ("1", "true")
            self._journal_path = None

        self._journal_file = None
        store_path = getattr(self.store, "path", None) or getattr(
            self.store, "db_path", None
        )
        if (
            self._journal_enabled
            and store_path
            and store_path != ":memory:"
            and not str(store_path).startswith("file:")
        ):
            if not self._journal_path:
                self._journal_path = f"{store_path}.journal"
            try:
                os.makedirs(
                    os.path.dirname(os.path.abspath(self._journal_path)), exist_ok=True
                )
                self._journal_file = open(self._journal_path, "a", encoding="utf-8")
            except Exception as exc:
                logger.warning(
                    "[pipeline] Could not initialize durability journal at %s: %s",
                    self._journal_path,
                    exc,
                )
                self._journal_file = None
        if hasattr(self.store, "_on_close"):
            self.store._on_close.append(self.close)

    def _journal_append(self, entry_type: str, payload: Any) -> None:
        """Write record to durable journal before returning acknowledgement (Audit C1)."""
        if self._journal_file is None:
            return
        try:
            line = json.dumps({"type": entry_type, "payload": payload}, default=str)
            self._journal_file.write(line + "\n")
            self._journal_file.flush()
        except Exception as exc:
            self.journal_failures += 1
            self.is_degraded = True
            logger.error(
                "[pipeline] Failed to write journal record (policy=%s): %s",
                self.durability_policy,
                exc,
            )
            if self.durability_policy == "fail_closed":
                raise JournalWriteError(f"Journal write failed: {exc}") from exc

    def _ensure_writer_alive(self):
        """Supervisor check: ensure the asynchronous background writer thread is running."""
        if not self._async_writer_enabled or self._writer_stop.is_set():
            return
        if self._writer_thread is None or not self._writer_thread.is_alive():
            logger.warning(
                "[pipeline] Storage writer thread was dead; supervisor reviving it"
            )
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="mdrap-writer"
            )
            self._writer_thread.start()

    def _writer_loop(self):
        """Dedicated background writer loop executing atomic batches off the tick loop."""
        while not self._writer_stop.is_set():
            try:
                timeout_s = min(0.1, max(0.01, self.flush_interval_s))
                try:
                    item = self._write_queue.get(timeout=timeout_s)
                except queue.Empty:
                    now = time.time()
                    if (
                        self._canonical_batch
                        or self._quarantine_batch
                        or self._lineage_batch
                    ) and (now - self._last_flush_ts >= self.flush_interval_s):
                        self.flush(wait=False)
                    continue
                if item is None:
                    self._write_queue.task_done()
                    break
                canon, quar, lin, health = item
                t_flush_start = time.perf_counter()
                try:
                    if hasattr(self.store, "write_batches_atomic"):
                        self.store.write_batches_atomic(canon, quar, lin, health)
                    else:
                        self.store.write_canonical_batch(canon)
                        self.store.write_quarantine_batch(quar)
                        self.store.write_lineage_batch(lin)
                        self.store.upsert_source_health(health)
                        self.store.commit()
                    if self.metrics:
                        self.metrics.record_flush(time.perf_counter() - t_flush_start)
                except Exception as exc:
                    self._last_writer_exc = exc
                    try:
                        self._spill_dead_letter(canon, quar, lin)
                    except Exception as spill_exc:
                        self._mark_writer_failure(canon, quar, lin, exc, spill_exc)
                    else:
                        logger.error(
                            "Async storage writer error; batch spilled to dead letter: %s",
                            exc,
                        )
                finally:
                    self._write_queue.task_done()
            except Exception as thread_exc:
                logger.error(
                    "[pipeline] Unexpected error in storage writer thread: %s",
                    thread_exc,
                )

    def _enqueue_canonical(self, event: CanonicalEvent) -> None:
        self._canonical_batch.append(event)

    def _enqueue_quarantine(self, row: tuple) -> None:
        self._quarantine_batch.append(row)

    def _enqueue_lineage(self, row: tuple) -> None:
        self._lineage_batch.append(row)

    @property
    def degraded(self) -> bool:
        return self._writer_failure is not None

    def _mark_writer_failure(
        self,
        canon: list,
        quar: list,
        lin: list,
        storage_exc: Exception,
        spill_exc: Exception,
    ) -> WriterFailure:
        failure = WriterFailure(
            "Storage write and dead-letter spill both failed "
            f"(storage={type(storage_exc).__name__}, spill={type(spill_exc).__name__})"
        )
        self._writer_failure = failure
        self.metrics.writer_failures += 1
        event_ids = {str(getattr(event, "event_id", "")) for event in canon}
        event_ids.update(str(row[0]) for row in quar if row)
        event_ids.update(str(row[0]) for row in lin if row)
        self.metrics.writer_events_at_risk += len(event_ids - {""})
        logger.critical("[pipeline] %s", failure, exc_info=spill_exc)
        return failure

    def _raise_if_writer_failed(self) -> None:
        if self._writer_failure is not None:
            raise self._writer_failure

    def _create_quarantined_event(
        self,
        raw: RawEvent,
        reason_msg: str,
        lineage_desc: str,
        reason_code: Reason | str = Reason.SCHEMA_VIOLATION,
        instrument_fallback: str = "MALFORMED",
        record_lineage: bool = True,
    ) -> CanonicalEvent:
        """Helper to create and record a fully evidentiary quarantine event (Invariant A6)."""
        if not raw.raw_id:
            raw = ingest(raw)
        instrument = (
            str(raw.payload.get("instrument", instrument_fallback))
            if isinstance(raw.payload, dict)
            else instrument_fallback
        )
        r_val = (
            reason_code.value if isinstance(reason_code, Reason) else str(reason_code)
        )
        fake = CanonicalEvent(
            event_id=raw.raw_id or f"q_{raw.source}_{time.time_ns()}",
            instrument_id=instrument,
            event_type=EventType.UNKNOWN,
            exchange_timestamp=raw.receive_timestamp if raw.receive_timestamp else 0.0,
            receive_timestamp=raw.receive_timestamp,
            processing_timestamp=time.time(),
            source=raw.source,
            sequence_number=None,
            quality_status=QualityStatus.INVALID,
            reasons=[r_val],
            raw_id=raw.raw_id,
        )
        q_row = (
            fake.event_id,
            fake.instrument_id,
            fake.source,
            fake.quality_status.value,
            json.dumps([reason_msg]),
            _safe_payload_json(raw.payload),
            raw.receive_timestamp,
        )
        self._enqueue_quarantine(q_row)
        self._journal_append("quarantine", q_row)
        if record_lineage:
            lin_row = (
                fake.event_id,
                fake.instrument_id,
                json.dumps([fake.raw_id]),
                fake.raw_id,
                _TRANSFORMATIONS_JSON[False],
                _VALIDATIONS_RUN_JSON,
                0,
                lineage_desc,
                fake.source,
                self.code_version,
                fake.processing_timestamp,
            )
            self._enqueue_lineage(lin_row)
            self._journal_append("lineage", lin_row)
        if self.reliability:
            self.reliability.observe(fake)
        if self.watchdog:
            self.watchdog.observe(fake)
        self.metrics.quality_counts["INVALID"] = (
            self.metrics.quality_counts.get("INVALID", 0) + 1
        )
        self.metrics.processed += 1
        self._maybe_flush()
        return fake

    def _apply_hook(
        self,
        hook: Callable[[CanonicalEvent], CanonicalEvent | None],
        event: CanonicalEvent,
        raw: RawEvent,
        stage: str,
    ) -> tuple[CanonicalEvent | None, CanonicalEvent | None]:
        """Apply a hook and convert every drop or failure into a quarantine outcome."""
        try:
            result = hook(event)
        except Exception as exc:
            self._pending_raw_payloads.pop(str(raw.raw_id), None)
            self.metrics.hook_errors += 1
            return None, self._create_quarantined_event(
                raw,
                f"{stage} hook raised {type(exc).__name__}",
                f"quarantined ({stage.lower()} hook error)",
                Reason.HOOK_ERROR,
            )

        if result is None:
            self._pending_raw_payloads.pop(str(raw.raw_id), None)
            self.metrics.hook_dropped += 1
            return None, self._create_quarantined_event(
                raw,
                f"{stage} hook dropped event",
                f"quarantined ({stage.lower()} hook dropped event)",
                Reason.HOOK_DROPPED,
            )

        if not isinstance(result, CanonicalEvent) or result.raw_id != event.raw_id:
            self._pending_raw_payloads.pop(str(raw.raw_id), None)
            self.metrics.hook_errors += 1
            return None, self._create_quarantined_event(
                raw,
                f"{stage} hook returned an invalid event or changed raw_id",
                f"quarantined ({stage.lower()} hook error)",
                Reason.HOOK_ERROR,
            )

        return result, None

    def process_one(
        self, raw: RawEvent, source_label: str | None = None
    ) -> CanonicalEvent | None:
        self._raise_if_writer_failed()
        # Timing sampling: only instrument 1-in-(mask+1) events when mask > 0
        self._event_counter += 1
        _do_timing = (self._event_counter & self._timing_sample_mask) == 0
        t_start_ns = time.perf_counter_ns() if _do_timing else 0
        # Ingest: assign deterministic raw_id and receive_timestamp before archiving and security checks
        raw = ingest(raw)
        # Write-ahead: archive raw event BEFORE any processing or security decisions (P1)
        if self.archive:
            self.archive.write(raw)

        # Security Guard: Rate Limiting & Input Sanitization (Spec §19)
        if self.security:
            if not self.security.rate_limiter.allow(raw.source):
                self.security._rate_limited_count += 1
                return self._create_quarantined_event(
                    raw,
                    "Security: Rate limit exceeded",
                    "quarantined (security: rate limit exceeded)",
                    Reason.RATE_LIMITED.value,
                )
            valid, err_msg = self.security.sanitizer.sanitize(raw.payload)
            if not valid:
                return self._create_quarantined_event(
                    raw,
                    f"Security sanitization: {err_msg}",
                    f"quarantined (security sanitization: {err_msg})",
                    Reason.MALFORMED.value,
                )
            # Cryptographic HMAC verification (P2)
            if isinstance(raw.payload, dict) and (
                self.security.hmac_required(raw.source) or "signature" in raw.payload
            ):
                sig = raw.payload.get("signature")
                if not sig or not self.security.verify_payload(
                    raw.source, raw.payload, sig
                ):
                    return self._create_quarantined_event(
                        raw,
                        "Cryptographic HMAC verification failed: missing or tampered signature",
                        "quarantined (security: HMAC verification failed)",
                        Reason.SECURITY_REJECT.value,
                    )

        try:
            event = normalize(raw)
        except SchemaError:
            # Structurally unparseable -- still classify + quarantine, never drop silently.
            return self._create_quarantined_event(
                raw,
                "Schema violation",
                "quarantined (schema error)",
                Reason.SCHEMA_VIOLATION.value,
                record_lineage=True,
            )

        t_norm_ns = time.perf_counter_ns() if _do_timing else 0
        if self.pre_evaluate_hook is not None:
            event, hook_outcome = self._apply_hook(
                self.pre_evaluate_hook, event, raw, "pre-evaluate"
            )
            if hook_outcome is not None:
                self._maybe_flush()
                return hook_outcome

        self._pending_raw_payloads[str(event.raw_id)] = raw.payload

        event = self.quality.evaluate(event)
        t_qual_ns = time.perf_counter_ns() if _do_timing else 0

        if event is not None and self.post_evaluate_hook is not None:
            event, hook_outcome = self._apply_hook(
                self.post_evaluate_hook, event, raw, "post-evaluate"
            )
            if hook_outcome is not None:
                self._maybe_flush()
                return hook_outcome

        res_event = None
        if event is not None:
            res_event = self._dispatch_evaluated(
                event,
                raw_payload=raw.payload,
                t_start_ns=t_start_ns,
                t_norm_ns=t_norm_ns,
                t_qual_ns=t_qual_ns,
            )

        if hasattr(self.quality, "drain_expired"):
            for d_ev in self.quality.drain_expired():
                self._dispatch_evaluated(d_ev)

        self._maybe_flush()
        return res_event

    def _dispatch_evaluated(
        self,
        event: CanonicalEvent,
        raw_payload: Any = None,
        t_start_ns: int = 0,
        t_norm_ns: int = 0,
        t_qual_ns: int = 0,
    ) -> CanonicalEvent:
        """Route evaluated canonical event through reconciler, sinks, and storage batches."""
        if raw_payload is None:
            raw_payload = self._pending_raw_payloads.pop(str(event.raw_id), {})
        else:
            self._pending_raw_payloads.pop(str(event.raw_id), None)

        event.processing_timestamp = time.time()
        t_rec_start_ns = time.perf_counter_ns() if t_start_ns else 0

        decision = self.reconciler.reconcile(event)
        # Apply reconciliation quality annotations to current event (not cached copies)
        if decision and decision.quality_reasons:
            event.reasons.extend(decision.quality_reasons)
        self.reliability.observe(event)

        # Feed analytics engine (V3: OHLCV, spread, volatility)
        if self.analytics:
            self.analytics.observe(event)

        # Feed Consolidated BBO engine
        if self.bbo:
            self.bbo.observe(event)

        # Feed Live Watchdog if configured
        if self.watchdog:
            self.watchdog.observe(event)

        t_rec_end_ns = time.perf_counter_ns() if t_start_ns else 0

        if event.quality_status != QualityStatus.INVALID:
            self._enqueue_canonical(event)
            self._journal_append(
                "canonical",
                event.to_dict() if hasattr(event, "to_dict") else dict(event),
            )

        if event.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID):
            reasons_json = json.dumps(event.reasons) if event.reasons else "[]"
            q_row = (
                event.event_id,
                event.instrument_id,
                event.source,
                event.quality_status.value,
                reasons_json,
                _safe_payload_json(raw_payload),
                event.receive_timestamp,
            )
            self._enqueue_quarantine(q_row)
            self._journal_append("quarantine", q_row)

        raw_id_json = (
            f"[{event.raw_id}]"
            if isinstance(event.raw_id, int)
            else json.dumps([event.raw_id])
        )
        lin_row = (
            event.event_id,
            event.instrument_id,
            raw_id_json,
            event.raw_id,
            _TRANSFORMATIONS_JSON[bool(decision)],
            _VALIDATIONS_RUN_JSON,
            int(bool(decision and decision.disagreement)),
            decision.reason if decision else "single-source / no reconciliation needed",
            decision.chosen_source if decision else event.source,
            self.code_version,
            event.processing_timestamp,
        )
        self._enqueue_lineage(lin_row)
        self._journal_append("lineage", lin_row)

        if self.metrics:
            if t_start_ns:
                t_end_ns = time.perf_counter_ns()
                e2e_latency = event.receive_timestamp - event.exchange_timestamp
                proc_latency = (t_end_ns - t_start_ns) * INV_NS_PER_SECOND
                ingest_latency = (t_norm_ns - t_start_ns) * INV_NS_PER_SECOND
                quality_latency = (t_qual_ns - t_norm_ns) * INV_NS_PER_SECOND
                reconcile_latency = (t_rec_end_ns - t_rec_start_ns) * INV_NS_PER_SECOND
                enqueue_latency = (t_end_ns - t_rec_end_ns) * INV_NS_PER_SECOND

                self.metrics.record(
                    e2e_latency,
                    proc_latency,
                    event.quality_status.value,
                    source=event.source,
                    instrument_id=event.instrument_id,
                    ingest_latency_s=ingest_latency,
                    quality_latency_s=quality_latency,
                    reconcile_latency_s=reconcile_latency,
                    enqueue_latency_s=enqueue_latency,
                )
            else:
                self.metrics.record_count(event.quality_status.value)

        return event

    def process_batch(
        self,
        raw_events: list[RawEvent],
        source_label: str | None = None,
        strict_align: bool = False,
    ) -> list[CanonicalEvent]:
        """
        Process a micro-batch of RawEvents with batched quality evaluation.
        Preserves strictly the per-source/per-instrument arrival order (Invariant A5).
        """
        if not raw_events:
            return []
        self._raise_if_writer_failed()

        results: list[CanonicalEvent | None] = [None] * len(raw_events)
        start_times_ns: list[int] = []
        norm_times_ns: list[int] = []

        # Timing sampling: decide once per batch whether to instrument timing
        self._event_counter += len(raw_events)
        _do_timing = (self._event_counter & self._timing_sample_mask) == 0

        valid_indices: list[int] = []
        valid_events: list[CanonicalEvent] = []

        for idx, raw in enumerate(raw_events):
            t_start = time.perf_counter_ns() if _do_timing else 0
            start_times_ns.append(t_start)
            # Ingest: assign deterministic raw_id and receive_timestamp before archiving and security checks
            raw = ingest(raw)

            # Write-ahead: archive raw event BEFORE any processing or security decisions (P1)
            if self.archive:
                self.archive.write(raw)

            # Security Guard: Rate Limiting & Input Sanitization (Spec §19)
            if self.security:
                if not self.security.rate_limiter.allow(raw.source):
                    self.security._rate_limited_count += 1
                    results[idx] = self._create_quarantined_event(
                        raw,
                        "Security: Rate limit exceeded",
                        "quarantined (security: rate limit exceeded)",
                        Reason.RATE_LIMITED.value,
                    )
                    norm_times_ns.append(time.perf_counter_ns() if _do_timing else 0)
                    continue
                valid, err_msg = self.security.sanitizer.sanitize(raw.payload)
                if not valid:
                    results[idx] = self._create_quarantined_event(
                        raw,
                        f"Security sanitization: {err_msg}",
                        f"quarantined (security sanitization: {err_msg})",
                        Reason.MALFORMED.value,
                    )
                    norm_times_ns.append(time.perf_counter_ns() if _do_timing else 0)
                    continue
                # Cryptographic HMAC verification (P2)
                if isinstance(raw.payload, dict) and (
                    self.security.hmac_required(raw.source)
                    or "signature" in raw.payload
                ):
                    sig = raw.payload.get("signature")
                    if not sig or not self.security.verify_payload(
                        raw.source, raw.payload, sig
                    ):
                        results[idx] = self._create_quarantined_event(
                            raw,
                            "Cryptographic HMAC verification failed: missing or tampered signature",
                            "quarantined (security: HMAC verification failed)",
                            Reason.SECURITY_REJECT.value,
                        )
                        norm_times_ns.append(
                            time.perf_counter_ns() if _do_timing else 0
                        )
                        continue

            try:
                event = normalize(raw)
            except SchemaError:
                results[idx] = self._create_quarantined_event(
                    raw,
                    "Schema violation",
                    "quarantined (schema error)",
                    Reason.SCHEMA_VIOLATION.value,
                    record_lineage=True,
                )
                norm_times_ns.append(time.perf_counter_ns() if _do_timing else 0)
                continue

            norm_times_ns.append(time.perf_counter_ns() if _do_timing else 0)
            valid_indices.append(idx)
            valid_events.append(event)

        # Batch quality evaluation across all valid normalized events
        if self.pre_evaluate_hook is not None:
            pre_filtered_events: list[CanonicalEvent] = []
            pre_filtered_indices: list[int] = []
            for v_idx, v_ev in zip(valid_indices, valid_events):
                hook_ev, hook_outcome = self._apply_hook(
                    self.pre_evaluate_hook,
                    v_ev,
                    raw_events[v_idx],
                    "pre-evaluate",
                )
                if hook_outcome is not None:
                    results[v_idx] = hook_outcome
                elif hook_ev is not None:
                    pre_filtered_events.append(hook_ev)
                    pre_filtered_indices.append(v_idx)
            valid_events = pre_filtered_events
            valid_indices = pre_filtered_indices

        evaluated_pairs: list[tuple[int, CanonicalEvent]] = []
        if valid_events:
            if hasattr(self.quality, "evaluate_batch"):
                try:
                    batch_res = self.quality.evaluate_batch(valid_events)
                except Exception as batch_exc:
                    logger.warning(
                        "[pipeline] evaluate_batch failed, falling back to sequential bisection: %s",
                        batch_exc,
                    )
                    batch_res = []
                    for ev in valid_events:
                        try:
                            batch_res.append(self.quality.evaluate(ev))
                        except Exception as ev_exc:
                            logger.error(
                                "[pipeline] Poison event quarantined during bisection: %s",
                                ev_exc,
                            )
                            ev.quality_status = QualityStatus.INVALID
                            ev.reasons.append(Reason.MALFORMED.value)
                            batch_res.append(ev)
                for valid_idx, ev in zip(valid_indices, batch_res):
                    if ev is not None:
                        if self.post_evaluate_hook is not None:
                            ev, hook_outcome = self._apply_hook(
                                self.post_evaluate_hook,
                                ev,
                                raw_events[valid_idx],
                                "post-evaluate",
                            )
                            if hook_outcome is not None:
                                results[valid_idx] = hook_outcome
                        if ev is not None:
                            evaluated_pairs.append((valid_idx, ev))
            else:
                for valid_idx, ev in zip(valid_indices, valid_events):
                    res = self.quality.evaluate(ev)
                    if res is not None:
                        if self.post_evaluate_hook is not None:
                            res, hook_outcome = self._apply_hook(
                                self.post_evaluate_hook,
                                res,
                                raw_events[valid_idx],
                                "post-evaluate",
                            )
                            if hook_outcome is not None:
                                results[valid_idx] = hook_outcome
                        if res is not None:
                            evaluated_pairs.append((valid_idx, res))
            t_qual_end_ns = time.perf_counter_ns() if _do_timing else 0
        else:
            t_qual_end_ns = 0

        # Downstream sequential reconciliation & persistence, strictly in arrival order (A5)
        for valid_idx, event in evaluated_pairs:
            t_start_ns = start_times_ns[valid_idx]
            t_norm_ns = norm_times_ns[valid_idx]
            raw = raw_events[valid_idx]
            results[valid_idx] = self._dispatch_evaluated(
                event,
                raw_payload=raw.payload,
                t_start_ns=t_start_ns,
                t_norm_ns=t_norm_ns,
                t_qual_ns=t_qual_end_ns,
            )

        if strict_align:
            # Under strict alignment:
            # 1. Background drain events are dispatched to storage but NEVER appended to returned results.
            # 2. Every slot in results must be filled (quarantining any unexpected None).
            if hasattr(self.quality, "drain_expired"):
                for d_ev in self.quality.drain_expired():
                    self._dispatch_evaluated(d_ev)

            for idx in range(len(raw_events)):
                if results[idx] is None:
                    results[idx] = self._create_quarantined_event(
                        raw_events[idx],
                        "Processing error: unhandled event failure",
                        "quarantined (unhandled pipeline failure)",
                        Reason.MALFORMED.value,
                    )
            self._maybe_flush()
            return [ev for ev in results if ev is not None]

        if hasattr(self.quality, "drain_expired"):
            for d_ev in self.quality.drain_expired():
                results.append(self._dispatch_evaluated(d_ev))

        self._maybe_flush()
        return [ev for ev in results if ev is not None]

    def _collect_health_rows(self) -> list[tuple]:
        health_rows = []
        now = time.time()
        for src, st in self.reliability.stats.items():
            health_rows.append(
                (
                    src,
                    st.total,
                    st.invalid,
                    st.suspicious,
                    st.duplicate,
                    st.gap,
                    round(st.ewma_latency_s, 6),
                    st.score,
                    now,
                )
            )
        return health_rows

    def _maybe_flush(self):
        batch_full = (
            len(self._canonical_batch) >= BATCH_SIZE
            or len(self._quarantine_batch) >= BATCH_SIZE
            or len(self._lineage_batch) >= BATCH_SIZE
        )
        if batch_full:
            self.flush(wait=False)
            return

        if self._canonical_batch or self._quarantine_batch or self._lineage_batch:
            now = time.time()
            if now - self._last_flush_ts >= self.flush_interval_s:
                self.flush(wait=False)

    def _spill_dead_letter(self, canon: list, quar: list, lin: list) -> None:
        """Spill unwritten batches to fsync'd JSONL under dead-letter dir on storage failure."""
        dl_dir = get_dead_letter_dir()
        os.makedirs(dl_dir, exist_ok=True)
        fname = os.path.join(dl_dir, f"spill-{time.time_ns()}.jsonl")
        with open(fname, "w", encoding="utf-8") as f:
            for c in canon:
                data = c.to_dict() if hasattr(c, "to_dict") else dict(c)
                f.write(
                    json.dumps(
                        {"type": "canonical", "_kind": "canonical", "data": data},
                        default=str,
                    )
                    + "\n"
                )
            for q in quar:
                f.write(
                    json.dumps(
                        {"type": "quarantine", "_kind": "quarantine", "row": q},
                        default=str,
                    )
                    + "\n"
                )
            for lineage_row in lin:
                f.write(
                    json.dumps(
                        {"type": "lineage", "_kind": "lineage", "row": lineage_row},
                        default=str,
                    )
                    + "\n"
                )
            f.flush()
            os.fsync(f.fileno())

    def flush(self, wait: bool = True):
        self._raise_if_writer_failed()
        if self._async_writer_enabled:
            self._ensure_writer_alive()
        if self._canonical_batch or self._quarantine_batch or self._lineage_batch:
            self._last_flush_ts = time.time()
            canon, quar, lin = (
                self._canonical_batch,
                self._quarantine_batch,
                self._lineage_batch,
            )
            self._canonical_batch, self._quarantine_batch, self._lineage_batch = (
                [],
                [],
                [],
            )
            health_rows = self._collect_health_rows()

            if self._async_writer_enabled:
                try:
                    self._write_queue.put((canon, quar, lin, health_rows), timeout=1.0)
                except queue.Full:
                    try:
                        self._spill_dead_letter(canon, quar, lin)
                    except Exception as spill_exc:
                        failure = self._mark_writer_failure(
                            canon, quar, lin, queue.Full(), spill_exc
                        )
                        raise failure from spill_exc
                    logger.warning(
                        "Async storage queue full: spilled batch to dead letter"
                    )
            else:
                self._sync_flush(canon, quar, lin, health_rows)

        if wait and self._async_writer_enabled and hasattr(self, "_write_queue"):
            with self._write_queue.all_tasks_done:
                deadline = time.time() + 5.0
                while self._write_queue.unfinished_tasks:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        logger.warning(
                            "[pipeline] Timed out waiting for write queue to drain (%d tasks remaining)",
                            self._write_queue.unfinished_tasks,
                        )
                        break
                    self._write_queue.all_tasks_done.wait(timeout=remaining)
            if self._writer_failure is not None:
                raise self._writer_failure
            if self._last_writer_exc is not None:
                exc = self._last_writer_exc
                self._last_writer_exc = None
                raise exc

    def _sync_flush(self, canon: list, quar: list, lin: list, health_rows: list):
        t_flush_start = time.perf_counter()
        try:
            if hasattr(self.store, "write_batches_atomic"):
                self.store.write_batches_atomic(canon, quar, lin, health_rows)
            else:
                self.store.write_canonical_batch(canon)
                self.store.write_quarantine_batch(quar)
                self.store.write_lineage_batch(lin)
                self.store.upsert_source_health(health_rows)
                self.store.commit()
            if self.metrics:
                self.metrics.record_flush(time.perf_counter() - t_flush_start)
        except Exception as storage_exc:
            try:
                self._spill_dead_letter(canon, quar, lin)
            except Exception as spill_exc:
                failure = self._mark_writer_failure(
                    canon, quar, lin, storage_exc, spill_exc
                )
                raise failure from spill_exc
            raise

    def finish(self, allow_conflicts: bool = False):
        finish_error: BaseException | None = None
        try:
            if hasattr(self.quality, "drain_expired"):
                for d_ev in self.quality.drain_expired(force=True):
                    self._dispatch_evaluated(d_ev)
            self.flush(wait=True)
        except BaseException as exc:
            finish_error = exc
        finally:
            if (
                self._async_writer_enabled
                and hasattr(self, "_writer_thread")
                and self._writer_thread.is_alive()
            ):
                self._writer_stop.set()
                try:
                    self._write_queue.put(None, timeout=1.0)
                except Exception:
                    pass
                self._writer_thread.join(timeout=10.0)
        if finish_error is not None:
            raise finish_error
        if self.bbo:
            self.store.write_bbo_batch(list(self.bbo.all_bbos().values()))
            self.store.commit()
        if hasattr(self.store, "conflicts") and self.metrics:
            self.metrics.storage_conflicts = self.store.conflicts
        self.metrics.finish()
        if not allow_conflicts and not getattr(self, "allow_storage_conflicts", False):
            conflicts = getattr(self.store, "conflicts", 0)
            if conflicts > 0:
                from .storage import StorageConflictError

                raise StorageConflictError(
                    f"Storage conflicts detected: {conflicts} records dropped due to primary key collision"
                )
        self._checkpoint_journal()
        self.close()

    def _checkpoint_journal(self) -> None:
        """Truncate journal only after verified storage commit has succeeded (Spec §14)."""
        if (
            getattr(self, "_journal_file", None) is not None
            and self._journal_path
            and os.path.exists(self._journal_path)
        ):
            if (
                self._async_writer_enabled
                and hasattr(self, "_write_queue")
                and self._write_queue.unfinished_tasks > 0
            ):
                logger.warning(
                    "[pipeline] Skipping journal checkpoint: write queue has %d pending tasks",
                    self._write_queue.unfinished_tasks,
                )
                return
            if self.journal_failures == 0:
                try:
                    self._journal_file.flush()
                    self._journal_file.seek(0)
                    self._journal_file.truncate(0)
                except Exception as exc:
                    logger.warning("[pipeline] Could not checkpoint journal: %s", exc)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.close()
        else:
            self.finish()

    def close(self) -> None:
        """Close pipeline, drain writer, and release journal resources without destroying uncommitted data."""
        if getattr(self, "_journal_file", None) is not None:
            try:
                self._journal_file.flush()
                self._journal_file.close()
            except Exception as exc:
                logger.debug("[pipeline] Error closing journal: %s", exc)
            finally:
                self._journal_file = None

    def __del__(self) -> None:
        if getattr(self, "_journal_file", None) is not None:
            try:
                self._journal_file.close()
            except Exception:
                pass


def get_dead_letter_dir() -> str:
    """Return absolute path to dead-letter directory, respecting MDRAP_DEAD_LETTER_DIR."""
    env_dir = os.environ.get("MDRAP_DEAD_LETTER_DIR")
    if env_dir:
        return os.path.abspath(env_dir)
    # Default to repo root data/deadletter regardless of current working directory
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(base_dir, "data", "deadletter")


def replay_dead_letter_spills(store, dead_letter_dir: str | None = None) -> dict:
    """Replay spilled dead-letter JSONL files back into the persistent Store."""
    dl_dir = dead_letter_dir or get_dead_letter_dir()
    if not os.path.exists(dl_dir):
        return {
            "files_processed": 0,
            "canonical_replayed": 0,
            "quarantine_replayed": 0,
            "lineage_replayed": 0,
        }

    files = sorted(
        [
            f
            for f in os.listdir(dl_dir)
            if f.startswith("spill-") and f.endswith(".jsonl")
        ]
    )
    stats = {
        "files_processed": 0,
        "canonical_replayed": 0,
        "quarantine_replayed": 0,
        "lineage_replayed": 0,
    }

    for fname in files:
        fpath = os.path.join(dl_dir, fname)
        canon_batch = []
        quar_batch = []
        lin_batch = []
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"Invalid dead-letter JSON in {fpath}: {exc}"
                    ) from exc
                try:
                    kind = obj.get("_kind") or obj.get("type")
                    if kind == "canonical":
                        d = obj.get("data", obj)
                        from .models import CanonicalEvent

                        canon_batch.append(CanonicalEvent.from_dict(d))
                    elif kind == "quarantine":
                        row = obj.get("row")
                        if row:
                            quar_batch.append(tuple(row))
                    elif kind == "lineage":
                        row = obj.get("row")
                        if row:
                            lin_batch.append(tuple(row))
                    else:
                        from .models import CanonicalEvent

                        canon_batch.append(CanonicalEvent.from_dict(obj))
                except Exception as exc:
                    logger.warning(
                        "[pipeline] Could not parse dead-letter item in %s: %s (skipping line)",
                        fpath,
                        exc,
                    )
                    continue

        if canon_batch:
            store.write_canonical_batch(canon_batch)
            stats["canonical_replayed"] += len(canon_batch)
        if quar_batch:
            store.write_quarantine_batch(quar_batch)
            stats["quarantine_replayed"] += len(quar_batch)
        if lin_batch:
            store.write_lineage_batch(lin_batch)
            stats["lineage_replayed"] += len(lin_batch)
        store.commit()

        stats["files_processed"] += 1
        replayed_path = fpath + ".replayed"
        if os.path.exists(replayed_path):
            raise FileExistsError(
                f"Cannot mark replayed spill; destination already exists: {replayed_path}"
            )
        os.rename(fpath, replayed_path)

    return stats
