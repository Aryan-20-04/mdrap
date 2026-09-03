"""
Pipeline orchestration.

Single-process, synchronous, batch-committing pipeline: this is the V1
baseline from section 14 of the spec (no broker in the loop yet).
Kept synchronous deliberately -- for CPU-bound per-event Python work,
asyncio/threads add overhead without adding throughput; a tight loop
with batched storage writes is the fastest correct baseline, and it's
what later versions (Kafka/Redpanda ingestion, C++ hot path) should be
benchmarked against.
"""
from __future__ import annotations

import gc
import json
import subprocess
import time
from typing import List, Optional

from gateway import SchemaError, ingest, normalize
from metrics import RunMetrics
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from quality import QualityEngine
from reconciliation import Reconciler, ReliabilityTracker
from storage import Store

BATCH_SIZE = 2000

# Precomputed once: the validations list is identical for every event, and
# transformations only ever takes one of two forms (with/without
# "reconcile"). Re-serializing these constants inside the per-event hot
# loop was pure waste -- serialize once, reuse the string every time.
_VALIDATIONS_RUN_JSON = json.dumps([
    "schema", "sequence_gap", "duplicate", "ordering", "staleness",
    "price_sanity", "quote_consistency",
])
_TRANSFORMATIONS_JSON = {
    False: json.dumps(["ingest", "normalize", "quality_evaluate"]),
    True: json.dumps(["ingest", "normalize", "quality_evaluate", "reconcile"]),
}


def _code_version() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, cwd=None, timeout=2)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unversioned"


class Pipeline:
    def __init__(self, store: Store, quality: Optional[QualityEngine] = None,
                 reliability: Optional[ReliabilityTracker] = None,
                 archive: Optional['RawArchive'] = None,
                 analytics: Optional['MarketAnalytics'] = None,
                 bbo: Optional['BBOEngine'] = None,
                 watchdog: Optional['SourceWatchdog'] = None,
                 security: Optional['SecurityManager'] = None):
        self.store = store
        self.quality = quality or QualityEngine()
        self.reliability = reliability or ReliabilityTracker()
        self.reconciler = Reconciler(self.reliability)
        self.metrics = RunMetrics()
        self.code_version = _code_version()
        self.archive = archive
        self.analytics = analytics
        self.bbo = bbo
        self.watchdog = watchdog
        self.security = security

        self._canonical_batch: List[CanonicalEvent] = []
        self._quarantine_batch: List[tuple] = []
        self._lineage_batch: List[tuple] = []
        # Suppress erratic GC pauses during hot tick loops; collect deterministically during flushes
        gc.set_threshold(100_000, 10, 10)

    def process_one(self, raw: RawEvent, source_label: Optional[str] = None) -> Optional[CanonicalEvent]:
        t_start_ns = time.perf_counter_ns()
        # Security Guard: Rate Limiting & Input Sanitization (Spec §19)
        if self.security:
            if not self.security.rate_limiter.allow(raw.source):
                self.security._rate_limited_count += 1
                return None
            valid, err_msg = self.security.sanitizer.sanitize(raw.payload)
            if not valid:
                fake = CanonicalEvent(
                    event_id=raw.raw_id,
                    instrument_id=str(raw.payload.get("instrument", "MALFORMED")) if isinstance(raw.payload, dict) else "MALFORMED",
                    event_type=EventType.TRADE,
                    exchange_timestamp=0.0, receive_timestamp=raw.receive_timestamp,
                    processing_timestamp=time.time(), source=raw.source, sequence_number=None,
                    quality_status=QualityStatus.INVALID, reasons=[Reason.SCHEMA_VIOLATION.value],
                    raw_id=raw.raw_id,
                )
                self._quarantine_batch.append((
                    fake.event_id, fake.instrument_id, fake.source, fake.quality_status.value,
                    json.dumps([f"Security sanitization: {err_msg}"]),
                    json.dumps(raw.payload, default=str), raw.receive_timestamp,
                ))
                self.metrics.quality_counts["INVALID"] = self.metrics.quality_counts.get("INVALID", 0) + 1
                self.metrics.processed += 1
                self._maybe_flush()
                return fake
            # Cryptographic HMAC verification
            if isinstance(raw.payload, dict) and "signature" in raw.payload:
                if not self.security.verify_payload(raw.source, raw.payload, raw.payload["signature"]):
                    fake = CanonicalEvent(
                        event_id=raw.raw_id,
                        instrument_id=str(raw.payload.get("instrument", "UNKNOWN")),
                        event_type=EventType.TRADE,
                        exchange_timestamp=0.0, receive_timestamp=raw.receive_timestamp,
                        processing_timestamp=time.time(), source=raw.source, sequence_number=None,
                        quality_status=QualityStatus.INVALID, reasons=[Reason.SCHEMA_VIOLATION.value],
                        raw_id=raw.raw_id,
                    )
                    self._quarantine_batch.append((
                        fake.event_id, fake.instrument_id, fake.source, fake.quality_status.value,
                        json.dumps(["Cryptographic HMAC verification failed: tampered payload"]),
                        json.dumps(raw.payload, default=str), raw.receive_timestamp,
                    ))
                    self.metrics.quality_counts["INVALID"] = self.metrics.quality_counts.get("INVALID", 0) + 1
                    self.metrics.processed += 1
                    self._maybe_flush()
                    return fake

        # Write-ahead: archive raw event BEFORE any processing
        if self.archive:
            self.archive.write(raw)
        raw = ingest(raw)

        try:
            event = normalize(raw)
        except SchemaError as exc:
            # Structurally unparseable -- still classify + quarantine, never drop silently.
            _payload = raw.payload if isinstance(raw.payload, dict) else {}
            fake = CanonicalEvent(
                event_id=raw.raw_id,
                instrument_id=str(_payload.get("instrument", "UNKNOWN")),
                event_type=EventType.TRADE,  # sentinel; event is INVALID so type is irrelevant
                exchange_timestamp=0.0, receive_timestamp=raw.receive_timestamp,
                processing_timestamp=time.time(), source=raw.source, sequence_number=None,
                quality_status=QualityStatus.INVALID, reasons=[Reason.SCHEMA_VIOLATION.value],
                raw_id=raw.raw_id,
            )
            self._quarantine_batch.append((
                fake.event_id, fake.instrument_id, fake.source, fake.quality_status.value,
                json.dumps(fake.reasons), json.dumps(raw.payload, default=str), raw.receive_timestamp,
            ))
            self.metrics.quality_counts["INVALID"] = self.metrics.quality_counts.get("INVALID", 0) + 1
            self.metrics.processed += 1
            self._maybe_flush()
            return fake

        event = self.quality.evaluate(event)
        event.processing_timestamp = time.time()

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

        t_end_ns = time.perf_counter_ns()
        e2e_latency = event.receive_timestamp - event.exchange_timestamp
        proc_latency = (t_end_ns - t_start_ns) / 1_000_000_000.0
        self.metrics.record(e2e_latency, proc_latency, event.quality_status.value)

        if event.quality_status != QualityStatus.INVALID:
            self._canonical_batch.append(event)

        if event.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID):
            reasons_json = json.dumps(event.reasons) if event.reasons else "[]"
            self._quarantine_batch.append((
                event.event_id, event.instrument_id, event.source, event.quality_status.value,
                reasons_json, json.dumps(raw.payload, default=str), event.receive_timestamp,
            ))

        raw_id_json = f'["{event.raw_id}"]'
        self._lineage_batch.append((
            event.event_id, event.instrument_id, raw_id_json, event.raw_id,
            _TRANSFORMATIONS_JSON[bool(decision)],
            _VALIDATIONS_RUN_JSON,
            int(bool(decision and decision.disagreement)),
            decision.reason if decision else "single-source / no reconciliation needed",
            decision.chosen_source if decision else event.source,
            self.code_version, event.processing_timestamp,
        ))

        self._maybe_flush()
        return event

    def _maybe_flush(self):
        if len(self._canonical_batch) >= BATCH_SIZE or len(self._quarantine_batch) >= BATCH_SIZE:
            self.flush()

    def flush(self):
        self.store.write_canonical_batch(self._canonical_batch)
        self.store.write_quarantine_batch(self._quarantine_batch)
        self.store.write_lineage_batch(self._lineage_batch)
        health_rows = []
        now = time.time()
        for src, st in self.reliability.stats.items():
            health_rows.append((src, st.total, st.invalid, st.suspicious, st.duplicate,
                                 st.gap, round(st.ewma_latency_s, 6), st.score, now))
        self.store.upsert_source_health(health_rows)
        self.store.commit()
        self._canonical_batch.clear()
        self._quarantine_batch.clear()
        self._lineage_batch.clear()
        gc.collect(1)

    def finish(self):
        self.flush()
        if self.bbo:
            self.store.write_bbo_batch(list(self.bbo.all_bbos().values()))
            self.store.commit()
        self.metrics.finish()
