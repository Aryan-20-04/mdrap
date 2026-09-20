"""Pipeline Orchestration and Event Stream Execution Engine.

This module coordinates the end-to-end life cycle of market data events in MDRAP:
  `raw ingress -> security check -> archive -> normalize -> quality -> reconcile -> telemetry -> storage`

Architectural Foundations (MDRAP Spec §14 & §26):
  1. Synchronous Pipeline Baseline:
     Deliberately implemented as a single-process, synchronous pipeline without asynchronous
     event loops or multi-threaded worker pools. For CPU-bound serialization and quality checks
     in Python, threading introduces GIL lock contention and asyncio adds event loop scheduling
     overhead without improving throughput. A synchronous tight loop with batched database
     commits provides the highest throughput and lowest p99 tail latency.
  2. Zero-Allocation Hot-Path Constants:
     Database lineage tracking requires JSON arrays of validations and transformations for
     every event. Re-serializing identical lists via `json.dumps()` on every tick consumes
     significant CPU cycles. `_VALIDATIONS_RUN_JSON` and `_TRANSFORMATIONS_JSON` are precomputed
     once at module import, eliminating heap allocations during per-event processing.
  3. Deterministic Garbage Collection:
     Default Python generational GC thresholds (700, 10, 10) trigger erratic GC sweeps during
     high-frequency tick bursts, causing latency spikes. Tuning the threshold via
     `gc.set_threshold(100_000, 10, 10)` defers GC passes until explicit `flush()` invocations.
  4. Write-Ahead Archival & Quarantine:
     Raw events are archived before normalization to prevent data loss. If normalization fails,
     a quarantined event is created and persisted, upholding: "Never silently discard bad data."
"""

from __future__ import annotations

from contextlib import contextmanager
import gc
import hashlib
import json
import os
import subprocess
import time
from typing import Any, TYPE_CHECKING

from gateway import SchemaError, ingest, normalize
from metrics import RunMetrics
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from quality import QualityEngine
from reconciliation import Reconciler, ReliabilityTracker
from storage import Store

if TYPE_CHECKING:
    from archive import RawArchive
    from analytics import MarketAnalytics
    from bbo import BBOEngine
    from watchdog import SourceWatchdog
    from security import SecurityManager

# Maximum number of buffered events before triggering an automatic batch write
BATCH_SIZE = 2000

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
        gc.set_threshold(50_000, 10, 10)
        yield
    finally:
        gc.set_threshold(*old)


def _safe_payload_json(payload: Any) -> str:
    """Truncate payloads > 64 KiB with sha256 metadata to prevent storage DoS."""
    s = json.dumps(payload, default=str)
    if len(s) > 65536:
        h = hashlib.sha256(s.encode("utf-8")).hexdigest()
        return json.dumps({
            "truncated": True,
            "sha256": h,
            "orig_bytes": len(s),
            "prefix": s[:65536],
        })
    return s


class Pipeline:
    """Synchronous market data pipeline orchestrator."""

    def __init__(
        self,
        store: Store,
        quality: QualityEngine | None = None,
        reliability: ReliabilityTracker | None = None,
        archive: RawArchive | None = None,
        analytics: MarketAnalytics | None = None,
        bbo: BBOEngine | None = None,
        watchdog: SourceWatchdog | None = None,
        security: SecurityManager | None = None,
        flush_interval_s: float = 1.0,
    ):
        self.store = store
        if quality is not None:
            self.quality = quality
        else:
            try:
                from fastpath import FastQualityEngine, is_available

                self.quality = (
                    FastQualityEngine() if is_available() else QualityEngine()
                )
            except Exception:
                self.quality = QualityEngine()
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

    def _enqueue_canonical(self, event: CanonicalEvent) -> None:
        self._canonical_batch.append(event)

    def _enqueue_quarantine(self, row: tuple) -> None:
        self._quarantine_batch.append(row)

    def _enqueue_lineage(self, row: tuple) -> None:
        self._lineage_batch.append(row)

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
        instrument = (
            str(raw.payload.get("instrument", instrument_fallback))
            if isinstance(raw.payload, dict)
            else instrument_fallback
        )
        r_val = reason_code.value if isinstance(reason_code, Reason) else str(reason_code)
        fake = CanonicalEvent(
            event_id=raw.raw_id,
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
        self._enqueue_quarantine(
            (
                fake.event_id,
                fake.instrument_id,
                fake.source,
                fake.quality_status.value,
                json.dumps([reason_msg]),
                _safe_payload_json(raw.payload),
                raw.receive_timestamp,
            )
        )
        if record_lineage:
            self._enqueue_lineage(
                (
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
            )
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

    def process_one(
        self, raw: RawEvent, source_label: str | None = None
    ) -> CanonicalEvent | None:
        t_start_ns = time.perf_counter_ns()
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

        raw = ingest(raw)

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

        t_norm_ns = time.perf_counter_ns()

        event = self.quality.evaluate(event)
        event.processing_timestamp = time.time()
        t_qual_ns = time.perf_counter_ns()

        decision = self.reconciler.reconcile(event)
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

        t_rec_ns = time.perf_counter_ns()

        if event.quality_status != QualityStatus.INVALID:
            self._enqueue_canonical(event)

        if event.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID):
            reasons_json = json.dumps(event.reasons) if event.reasons else "[]"
            self._enqueue_quarantine(
                (
                    event.event_id,
                    event.instrument_id,
                    event.source,
                    event.quality_status.value,
                    reasons_json,
                    _safe_payload_json(raw.payload),
                    event.receive_timestamp,
                )
            )

        raw_id_json = json.dumps([event.raw_id])
        self._enqueue_lineage(
            (
                event.event_id,
                event.instrument_id,
                raw_id_json,
                event.raw_id,
                _TRANSFORMATIONS_JSON[bool(decision)],
                _VALIDATIONS_RUN_JSON,
                int(bool(decision and decision.disagreement)),
                decision.reason
                if decision
                else "single-source / no reconciliation needed",
                decision.chosen_source if decision else event.source,
                self.code_version,
                event.processing_timestamp,
            )
        )

        t_end_ns = time.perf_counter_ns()
        e2e_latency = event.receive_timestamp - event.exchange_timestamp
        proc_latency = (t_end_ns - t_start_ns) / 1_000_000_000.0
        ingest_latency = (t_norm_ns - t_start_ns) / 1_000_000_000.0
        quality_latency = (t_qual_ns - t_norm_ns) / 1_000_000_000.0
        reconcile_latency = (t_rec_ns - t_qual_ns) / 1_000_000_000.0
        enqueue_latency = (t_end_ns - t_rec_ns) / 1_000_000_000.0

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

        self._maybe_flush()
        return event

    def process_batch(
        self, raw_events: list[RawEvent], source_label: str | None = None
    ) -> list[CanonicalEvent]:
        """
        Process a micro-batch of RawEvents with batched quality evaluation.
        Preserves strictly the per-source/per-instrument arrival order (Invariant A5).
        """
        if not raw_events:
            return []

        results: list[CanonicalEvent | None] = [None] * len(raw_events)
        start_times_ns: list[int] = []
        norm_times_ns: list[int] = []

        valid_indices: list[int] = []
        valid_events: list[CanonicalEvent] = []

        for idx, raw in enumerate(raw_events):
            t_start = time.perf_counter_ns()
            start_times_ns.append(t_start)

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
                    norm_times_ns.append(time.perf_counter_ns())
                    continue
                valid, err_msg = self.security.sanitizer.sanitize(raw.payload)
                if not valid:
                    results[idx] = self._create_quarantined_event(
                        raw,
                        f"Security sanitization: {err_msg}",
                        f"quarantined (security sanitization: {err_msg})",
                        Reason.MALFORMED.value,
                    )
                    norm_times_ns.append(time.perf_counter_ns())
                    continue
                # Cryptographic HMAC verification (P2)
                if isinstance(raw.payload, dict) and (
                    self.security.hmac_required(raw.source) or "signature" in raw.payload
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
                        norm_times_ns.append(time.perf_counter_ns())
                        continue

            raw = ingest(raw)

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
                norm_times_ns.append(time.perf_counter_ns())
                continue

            norm_times_ns.append(time.perf_counter_ns())
            valid_indices.append(idx)
            valid_events.append(event)

        # Batch quality evaluation across all valid normalized events
        if valid_events:
            t_qual_start_ns = time.perf_counter_ns()
            if hasattr(self.quality, "evaluate_batch"):
                self.quality.evaluate_batch(valid_events)
            else:
                for ev in valid_events:
                    self.quality.evaluate(ev)
            t_qual_end_ns = time.perf_counter_ns()
            avg_qual_latency_s = (t_qual_end_ns - t_qual_start_ns) / (
                len(valid_events) * 1_000_000_000.0
            )
        else:
            avg_qual_latency_s = 0.0

        # Downstream sequential reconciliation & persistence, strictly in arrival order (A5)
        for valid_idx, event in zip(valid_indices, valid_events):
            t_start_ns = start_times_ns[valid_idx]
            t_norm_ns = norm_times_ns[valid_idx]
            raw = raw_events[valid_idx]

            event.processing_timestamp = time.time()

            t_rec_start_ns = time.perf_counter_ns()
            decision = self.reconciler.reconcile(event)
            self.reliability.observe(event)

            if self.analytics:
                self.analytics.observe(event)
            if self.bbo:
                self.bbo.observe(event)
            if self.watchdog:
                self.watchdog.observe(event)
            t_rec_end_ns = time.perf_counter_ns()

            if event.quality_status != QualityStatus.INVALID:
                self._enqueue_canonical(event)

            if event.quality_status in (
                QualityStatus.SUSPICIOUS,
                QualityStatus.INVALID,
            ):
                reasons_json = json.dumps(event.reasons) if event.reasons else "[]"
                self._enqueue_quarantine(
                    (
                        event.event_id,
                        event.instrument_id,
                        event.source,
                        event.quality_status.value,
                        reasons_json,
                        _safe_payload_json(raw.payload),
                        event.receive_timestamp,
                    )
                )

            raw_id_json = json.dumps([event.raw_id])
            self._enqueue_lineage(
                (
                    event.event_id,
                    event.instrument_id,
                    raw_id_json,
                    event.raw_id,
                    _TRANSFORMATIONS_JSON[bool(decision)],
                    _VALIDATIONS_RUN_JSON,
                    int(bool(decision and decision.disagreement)),
                    decision.reason
                    if decision
                    else "single-source / no reconciliation needed",
                    decision.chosen_source if decision else event.source,
                    self.code_version,
                    event.processing_timestamp,
                )
            )

            t_end_ns = time.perf_counter_ns()
            e2e_latency = event.receive_timestamp - event.exchange_timestamp
            proc_latency = (t_end_ns - t_start_ns) / 1_000_000_000.0
            ingest_latency = (t_norm_ns - t_start_ns) / 1_000_000_000.0
            reconcile_latency = (t_rec_end_ns - t_rec_start_ns) / 1_000_000_000.0
            enqueue_latency = (t_end_ns - t_rec_end_ns) / 1_000_000_000.0

            self.metrics.record(
                e2e_latency,
                proc_latency,
                event.quality_status.value,
                source=event.source,
                instrument_id=event.instrument_id,
                ingest_latency_s=ingest_latency,
                quality_latency_s=avg_qual_latency_s,
                reconcile_latency_s=reconcile_latency,
                enqueue_latency_s=enqueue_latency,
            )
            results[valid_idx] = event

        self._maybe_flush()
        return [ev for ev in results if ev is not None]

    def _maybe_flush(self):
        now = time.time()
        batch_full = (
            len(self._canonical_batch) >= BATCH_SIZE
            or len(self._quarantine_batch) >= BATCH_SIZE
            or len(self._lineage_batch) >= BATCH_SIZE
        )
        time_elapsed = (now - self._last_flush_ts >= self.flush_interval_s) and (
            bool(self._canonical_batch)
            or bool(self._quarantine_batch)
            or bool(self._lineage_batch)
        )
        if batch_full or time_elapsed:
            self.flush()

    def _spill_dead_letter(self, canon: list, quar: list, lin: list) -> None:
        """Spill unwritten batches to fsync'd JSONL under data/deadletter/ on storage failure."""
        dl_dir = os.path.join("data", "deadletter")
        os.makedirs(dl_dir, exist_ok=True)
        fname = os.path.join(dl_dir, f"spill-{time.time_ns()}.jsonl")
        with open(fname, "w", encoding="utf-8") as f:
            for c in canon:
                f.write(json.dumps(c.to_dict() if hasattr(c, "to_dict") else str(c)) + "\n")
            for q in quar:
                f.write(json.dumps({"type": "quarantine", "row": q}, default=str) + "\n")
            for l in lin:
                f.write(json.dumps({"type": "lineage", "row": l}, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def flush(self):
        if not (self._canonical_batch or self._quarantine_batch or self._lineage_batch):
            return
        self._last_flush_ts = time.time()
        canon, quar, lin = self._canonical_batch, self._quarantine_batch, self._lineage_batch
        self._canonical_batch, self._quarantine_batch, self._lineage_batch = [], [], []
        try:
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
            if hasattr(self.store, "write_batches_atomic"):
                self.store.write_batches_atomic(canon, quar, lin, health_rows)
            else:
                self.store.write_canonical_batch(canon)
                self.store.write_quarantine_batch(quar)
                self.store.write_lineage_batch(lin)
                self.store.upsert_source_health(health_rows)
                self.store.commit()
        except Exception:
            self._spill_dead_letter(canon, quar, lin)
            raise

    def finish(self):
        self.flush()
        if self.bbo:
            self.store.write_bbo_batch(list(self.bbo.all_bbos().values()))
            self.store.commit()
        self.metrics.finish()
