"""
Decoupled Streaming Pipeline (MDRAP V2).

Implements the V2 Streaming Baseline (Spec Section 14, 16, 23 Phase 6).
Decouples ingestion, quality evaluation, and persistence into independent
workers connected by an EventBroker:

  Feed Producer -> [raw_events topic] -> Stream Processor Worker
                                               |
        +--------------------------------------+--------------------------------------+
        | [canonical_events topic]             | [quarantine topic]                   | [lineage topic]
        v                                      v                                      v
                             Storage Sink Worker (batched commits)
"""
from __future__ import annotations

import gc
import json
import subprocess
import threading
import time
from typing import List, Optional

from broker import EventBroker, QueueBroker
from gateway import SchemaError, ingest, normalize
from metrics import RunMetrics
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from quality import QualityEngine
from reconciliation import Reconciler, ReliabilityTracker
from storage import Store

BATCH_SIZE = 2000
_SENTINEL = "__MDRAP_STREAM_END__"

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


_EXPECTED_QUALITY = {
    "duplicate": (QualityStatus.INVALID, Reason.DUPLICATE.value),
    "out_of_order": (QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER.value),
    "malformed": (QualityStatus.INVALID, Reason.SCHEMA_VIOLATION.value),
    "price_anomaly": (QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY.value),
    "crossed_quote": (QualityStatus.INVALID, Reason.CROSSED_QUOTE.value),
}


class StreamingPipeline:
    """
    Decoupled multi-worker streaming pipeline for MDRAP V2.
    """

    def __init__(
        self,
        store: Store,
        broker: Optional[EventBroker] = None,
        quality: Optional[QualityEngine] = None,
        reliability: Optional[ReliabilityTracker] = None,
        batch_size: int = BATCH_SIZE,
        archive: Optional['RawArchive'] = None,
        analytics: Optional['MarketAnalytics'] = None,
        bbo: Optional['BBOEngine'] = None,
    ):
        self.store = store
        self.broker = broker or QueueBroker()
        self.quality = quality or QualityEngine()
        self.reliability = reliability or ReliabilityTracker()
        self.reconciler = Reconciler(self.reliability)
        self.metrics = RunMetrics()
        self.code_version = _code_version()
        self.batch_size = batch_size
        self.archive = archive
        self.analytics = analytics
        self.bbo = bbo
        gc.set_threshold(100_000, 10, 10)

        self.raw_topic = "raw_events"
        self.canonical_topic = "canonical_events"
        self.quarantine_topic = "quarantine_events"
        self.lineage_topic = "lineage_events"

        self.ground_truth = {
            "injected_seen": {k: 0 for k in _EXPECTED_QUALITY},
            "detected": {k: 0 for k in _EXPECTED_QUALITY},
            "false_positive_counts": {r.value: 0 for r in Reason},
            "total_valid_no_fault": 0,
        }

        self._stop_event = threading.Event()
        self._processor_thread = threading.Thread(target=self._stream_processor_loop, daemon=True)
        self._storage_thread = threading.Thread(target=self._storage_sink_loop, daemon=True)

        self._started = False

    def start(self):
        """Start asynchronous stream processor and storage sink workers."""
        if not self._started:
            self._started = True
            self._processor_thread.start()
            self._storage_thread.start()

    def ingest_one(self, raw: RawEvent, source_label: Optional[str] = None):
        """Ingest a single raw event from the producer feed."""
        if not self._started:
            self.start()

        # Handle backpressure: if broker backlog exceeds high watermark, yield briefly
        if isinstance(self.broker, QueueBroker):
            if self.broker.is_backpressure_active(self.raw_topic):
                self.metrics.record_streaming(self.broker.depth(self.raw_topic), backpressure_stall=True)
                time.sleep(0.0005)

        t_start = time.time()
        # Write-ahead: archive raw event BEFORE any processing
        if self.archive:
            self.archive.write(raw)
        raw = ingest(raw)
        self.broker.publish(self.raw_topic, (raw, source_label, t_start))

    def process_one(self, raw: RawEvent, source_label: Optional[str] = None):
        """Compatibility method matching V1 Pipeline interface."""
        self.ingest_one(raw, source_label)

    def reset_ground_truth(self):
        """Reset ground truth counters after warmup."""
        self.ground_truth = {
            "injected_seen": {k: 0 for k in _EXPECTED_QUALITY},
            "detected": {k: 0 for k in _EXPECTED_QUALITY},
            "false_positive_counts": {r.value: 0 for r in Reason},
            "total_valid_no_fault": 0,
        }

    def _stream_processor_loop(self):
        """Processes raw events -> evaluates quality -> reconciles -> publishes downstream."""
        while not self._stop_event.is_set():
            item = self.broker.poll(self.raw_topic, timeout=0.05)
            if item is None:
                continue
            if item == _SENTINEL:
                self.broker.publish(self.canonical_topic, _SENTINEL)
                self.broker.publish(self.quarantine_topic, _SENTINEL)
                self.broker.publish(self.lineage_topic, _SENTINEL)
                break

            raw, source_label, t_ingest = item
            proc_t0_ns = time.perf_counter_ns()

            # Record queue depth telemetry periodically
            cur_depth = self.broker.depth(self.raw_topic)
            if self.metrics.processed % 200 == 0:
                self.metrics.record_streaming(cur_depth)

            try:
                event = normalize(raw)
            except SchemaError:
                _payload = raw.payload if isinstance(raw.payload, dict) else {}
                fake = CanonicalEvent(
                    event_id=raw.raw_id,
                    instrument_id=str(_payload.get("instrument", "UNKNOWN")),
                    event_type=EventType.TRADE,
                    exchange_timestamp=0.0,
                    receive_timestamp=raw.receive_timestamp,
                    processing_timestamp=time.time(),
                    source=raw.source,
                    sequence_number=None,
                    quality_status=QualityStatus.INVALID,
                    reasons=[Reason.SCHEMA_VIOLATION.value],
                    raw_id=raw.raw_id,
                )
                quarantine_row = (
                    fake.event_id, fake.instrument_id, fake.source, fake.quality_status.value,
                    json.dumps(fake.reasons), json.dumps(raw.payload, default=str), raw.receive_timestamp,
                )
                self.broker.publish(self.quarantine_topic, quarantine_row)
                self.metrics.quality_counts["INVALID"] = self.metrics.quality_counts.get("INVALID", 0) + 1
                self.metrics.processed += 1

                if source_label is not None:
                    self.ground_truth["injected_seen"][source_label] = (
                        self.ground_truth["injected_seen"].get(source_label, 0) + 1
                    )
                    exp_status, exp_reason = _EXPECTED_QUALITY.get(source_label, (None, None))
                    if fake.quality_status == exp_status and exp_reason in fake.reasons:
                        self.ground_truth["detected"][source_label] = (
                            self.ground_truth["detected"].get(source_label, 0) + 1
                        )
                else:
                    self.ground_truth["total_valid_no_fault"] += 1
                    for r in fake.reasons:
                        self.ground_truth["false_positive_counts"][r] = (
                            self.ground_truth["false_positive_counts"].get(r, 0) + 1
                        )
                continue

            event = self.quality.evaluate(event)

            if source_label is not None:
                self.ground_truth["injected_seen"][source_label] = (
                    self.ground_truth["injected_seen"].get(source_label, 0) + 1
                )
                exp_status, exp_reason = _EXPECTED_QUALITY.get(source_label, (None, None))
                if event.quality_status == exp_status and exp_reason in event.reasons:
                    self.ground_truth["detected"][source_label] = (
                        self.ground_truth["detected"].get(source_label, 0) + 1
                    )
            else:
                self.ground_truth["total_valid_no_fault"] += 1
                if event.quality_status != QualityStatus.VALID:
                    for r in event.reasons:
                        self.ground_truth["false_positive_counts"][r] = (
                            self.ground_truth["false_positive_counts"].get(r, 0) + 1
                        )

            event.processing_timestamp = time.time()

            decision = self.reconciler.reconcile(event)
            self.reliability.observe(event)

            # Feed analytics engine (V3: OHLCV, spread, volatility)
            if self.analytics:
                self.analytics.observe(event)

            # Feed Consolidated BBO engine
            if self.bbo:
                self.bbo.observe(event)

            e2e_latency = event.receive_timestamp - event.exchange_timestamp
            proc_latency = (time.perf_counter_ns() - proc_t0_ns) / 1_000_000_000.0
            self.metrics.record(e2e_latency, proc_latency, event.quality_status.value)

            if event.quality_status != QualityStatus.INVALID:
                self.broker.publish(self.canonical_topic, event)

            if event.quality_status in (QualityStatus.SUSPICIOUS, QualityStatus.INVALID):
                reasons_json = json.dumps(event.reasons) if event.reasons else "[]"
                quarantine_row = (
                    event.event_id, event.instrument_id, event.source, event.quality_status.value,
                    reasons_json, json.dumps(raw.payload, default=str), event.receive_timestamp,
                )
                self.broker.publish(self.quarantine_topic, quarantine_row)

            raw_id_json = f'["{event.raw_id}"]'
            lineage_row = (
                event.event_id, event.instrument_id, raw_id_json, event.raw_id,
                _TRANSFORMATIONS_JSON[bool(decision)],
                _VALIDATIONS_RUN_JSON,
                int(bool(decision and decision.disagreement)),
                decision.reason if decision else "single-source / no reconciliation needed",
                decision.chosen_source if decision else event.source,
                self.code_version, event.processing_timestamp,
            )
            self.broker.publish(self.lineage_topic, lineage_row)

    def _storage_sink_loop(self):
        """Drains downstream event topics and batches writes to Store."""
        canonical_batch: List[CanonicalEvent] = []
        quarantine_batch: List[tuple] = []
        lineage_batch: List[tuple] = []

        canonical_done = False
        quarantine_done = False
        lineage_done = False

        last_flush = time.time()

        while not (canonical_done and quarantine_done and lineage_done):
            # Drain canonical
            if not canonical_done:
                events = self.broker.poll_batch(self.canonical_topic, max_items=self.batch_size, timeout=0.01)
                for ev in events:
                    if ev == _SENTINEL:
                        canonical_done = True
                    else:
                        canonical_batch.append(ev)

            # Drain quarantine
            if not quarantine_done:
                rows = self.broker.poll_batch(self.quarantine_topic, max_items=self.batch_size, timeout=0.01)
                for r in rows:
                    if r == _SENTINEL:
                        quarantine_done = True
                    else:
                        quarantine_batch.append(r)

            # Drain lineage
            if not lineage_done:
                l_rows = self.broker.poll_batch(self.lineage_topic, max_items=self.batch_size, timeout=0.01)
                for lr in l_rows:
                    if lr == _SENTINEL:
                        lineage_done = True
                    else:
                        lineage_batch.append(lr)

            # Flush if batches are full or time interval exceeded
            now = time.time()
            time_to_flush = (now - last_flush >= 0.1)
            batch_full = (
                len(canonical_batch) >= self.batch_size
                or len(quarantine_batch) >= self.batch_size
                or len(lineage_batch) >= self.batch_size
            )

            if batch_full or time_to_flush or (canonical_done and quarantine_done and lineage_done):
                if canonical_batch:
                    # Measure storage worker lag for the newest event in batch
                    newest = canonical_batch[-1]
                    lag = now - newest.processing_timestamp
                    self.metrics.record_streaming(self.broker.depth(self.canonical_topic), storage_lag_s=lag)
                    self.store.write_canonical_batch(canonical_batch)
                    canonical_batch.clear()

                if quarantine_batch:
                    self.store.write_quarantine_batch(quarantine_batch)
                    quarantine_batch.clear()

                if lineage_batch:
                    self.store.write_lineage_batch(lineage_batch)
                    lineage_batch.clear()

                health_rows = []
                for src, st in self.reliability.stats.items():
                    health_rows.append((
                        src, st.total, st.invalid, st.suspicious, st.duplicate,
                        st.gap, round(st.ewma_latency_s, 6), st.score, now,
                    ))
                if health_rows:
                    self.store.upsert_source_health(health_rows)

                self.store.commit()
                last_flush = now

    def flush(self):
        """Force flush remaining state."""
        pass

    def finish(self):
        """Gracefully stop producer, flush queues, and join worker threads."""
        if not self._started:
            return

        # Send termination sentinel to raw topic
        self.broker.publish(self.raw_topic, _SENTINEL)

        # Wait for workers to complete processing and persistence
        self._processor_thread.join(timeout=30.0)
        self._storage_thread.join(timeout=30.0)

        if self.bbo:
            self.store.write_bbo_batch(list(self.bbo.all_bbos().values()))
            self.store.commit()

        self.metrics.finish()
        self.broker.close()
