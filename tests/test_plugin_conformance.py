"""Institutional Conformance Test Kit for MDRAP Plugins and Adapters (Phase 5, Card #4).

Enforces:
1. FeedAdapter conformance suite (lifecycle, raw event emission, normalization, health).
2. AppendStorageSink and StorageBackend conformance suite (atomic batch writes, commit, rollback/close).
3. Third-party plugin validation, signature checking, and version-handshake verification.
4. Pre-evaluate and post-evaluate hook contracts in Pipeline.
"""

from __future__ import annotations

import time
from typing import Any
import pytest

from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from protocols import (
    AppendStorageSink,
    QueryStorageStore,
    StorageBackend,
)
from adapters import FeedAdapter
from adapters.reference import ReferenceFeedAdapter
from adapters.template import TemplateCustomVenueAdapter
from storage import Store
from pipeline import Pipeline
from plugins import (
    PluginRegistry,
    PluginValidationError,
    validate_plugin,
)


# ===========================================================================
# Reusable Conformance Verification Helpers
# ===========================================================================


class AdapterConformanceTester:
    """Reusable conformance verifier for third-party FeedAdapter authors."""

    @staticmethod
    def verify(adapter: Any) -> None:
        """Run full battery of protocol, signature, and lifecycle checks."""
        # 1. Structural check
        assert isinstance(adapter, FeedAdapter), (
            f"{adapter} does not satisfy FeedAdapter Protocol"
        )

        # 2. Lifecycle check
        if hasattr(adapter, "connect"):
            adapter.connect()
        elif hasattr(adapter, "open"):
            adapter.open()

        # 3. Receive / streaming check
        raw_event: RawEvent | None = None
        if hasattr(adapter, "receive"):
            raw_event = adapter.receive()
        elif hasattr(adapter, "__iter__"):
            iterator = iter(adapter)
            try:
                raw_event = next(iterator)
            except StopIteration:
                raw_event = None

        if raw_event is not None:
            assert isinstance(raw_event, RawEvent), (
                f"Expected RawEvent, got {type(raw_event)}"
            )
            assert raw_event.source, "RawEvent source cannot be empty"
            assert isinstance(raw_event.payload, dict), (
                "RawEvent payload must be a dict"
            )

            # 4. Normalization check if supported
            if hasattr(adapter, "normalize"):
                canonical = adapter.normalize(raw_event)
                assert isinstance(canonical, CanonicalEvent), (
                    f"Expected CanonicalEvent, got {type(canonical)}"
                )
                assert canonical.instrument_id, "CanonicalEvent must have instrument_id"

        # 5. Diagnostics check if supported
        if hasattr(adapter, "health"):
            diag = adapter.health()
            assert isinstance(diag, dict), "Adapter health() must return a dict"

        # 6. Cleanup check
        if hasattr(adapter, "disconnect"):
            adapter.disconnect()
        elif hasattr(adapter, "close"):
            adapter.close()


class StorageSinkConformanceTester:
    """Reusable conformance verifier for third-party AppendStorageSink authors."""

    @staticmethod
    def verify(sink: AppendStorageSink) -> None:
        """Verify atomic batch persistence contract."""
        assert isinstance(sink, AppendStorageSink), (
            f"{sink} does not satisfy AppendStorageSink Protocol"
        )

        now = time.time()
        canonical_batch = [
            CanonicalEvent(
                event_id="test-run-1",
                event_type=EventType.TRADE,
                instrument_id="BTC-USDT",
                source="TEST_FEED",
                price=50000.0,
                quantity=1.0,
                exchange_timestamp=now,
                receive_timestamp=now,
                processing_timestamp=now,
                sequence_number=1,
                quality_status=QualityStatus.VALID,
            )
        ]
        quarantine_batch = [
            (
                "q-1",
                "BTC-USDT",
                "TEST_FEED",
                "INVALID",
                '["SCHEMA_VIOLATION"]',
                "{}",
                now,
            )
        ]
        lineage_batch = [
            (
                "test-run-1",
                "BTC-USDT",
                "[]",
                "raw-1",
                "[]",
                "[]",
                0,
                "ok",
                "TEST_FEED",
                "v3.0.0",
                now,
            )
        ]

        # Individual batch methods must not raise
        sink.write_canonical_batch(canonical_batch)
        sink.write_quarantine_batch(quarantine_batch)
        sink.write_lineage_batch(lineage_batch)
        sink.commit()

        # Atomic batch method must not raise
        sink.write_batches_atomic(
            canonical=canonical_batch,
            quarantine=quarantine_batch,
            lineage=lineage_batch,
        )

        sink.commit()
        sink.close()


# ===========================================================================
# Conformance Tests
# ===========================================================================


def test_reference_adapter_conformance():
    """Verify built-in ReferenceFeedAdapter passes the full conformance battery."""
    ref = ReferenceFeedAdapter(source_name="CONFORMANCE_VENUE", symbol="AAPL")
    ref.connect()
    ref.feed_simulated_packet(seq=1, price=150.0, qty=10.0, side="BUY")
    AdapterConformanceTester.verify(ref)


def test_template_adapter_conformance():
    """Verify built-in TemplateCustomVenueAdapter passes the conformance battery."""
    tmpl = TemplateCustomVenueAdapter(venue_name="TEMPLATE_VENUE")
    AdapterConformanceTester.verify(tmpl)


def test_store_storage_sink_and_query_conformance():
    """Verify Store satisfies AppendStorageSink, QueryStorageStore, and composite StorageBackend."""
    store = Store(":memory:")
    assert isinstance(store, AppendStorageSink)
    assert isinstance(store, QueryStorageStore)
    assert isinstance(store, StorageBackend)
    StorageSinkConformanceTester.verify(store)


# ===========================================================================
# Card #4 T1: Plugin Validation & Error Reporting
# ===========================================================================


def test_plugin_validation_reports_missing_adapter_methods():
    """Verify broken adapter missing required methods fails validation with clear reason."""

    class BrokenAdapterNoMethods:
        pass

    is_valid, reason = validate_plugin("mdrap.adapters", BrokenAdapterNoMethods())
    assert is_valid is False
    assert reason is not None
    assert "Adapter contract violation" in reason


def test_plugin_validation_reports_bad_normalize_signature():
    """Verify adapter with invalid normalize signature is rejected with specific diagnostic."""

    class BadSignatureAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def receive(self) -> RawEvent | None:
            return None

        def normalize(self) -> None:  # missing raw parameter!
            pass

    is_valid, reason = validate_plugin("mdrap.adapters", BadSignatureAdapter())
    assert is_valid is False
    assert reason is not None
    assert "normalize() signature error" in reason


def test_plugin_validation_version_handshake():
    """Verify version handshake rejects plugins requiring future MDRAP versions."""

    class FuturePlugin:
        __min_mdrap_version__ = "4.0.0"

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def receive(self) -> None:
            pass

    is_valid, reason = validate_plugin("mdrap.adapters", FuturePlugin())
    assert is_valid is False
    assert reason is not None
    assert "Version mismatch: plugin requires MDRAP >= 4.0.0" in reason

    class CompatiblePlugin:
        __min_mdrap_version__ = "2.9.0"

        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def receive(self) -> None:
            pass

    is_valid, reason = validate_plugin("mdrap.adapters", CompatiblePlugin())
    assert is_valid is True
    assert reason is None


def test_plugin_registry_register_and_error_tracking():
    """Verify PluginRegistry records validation errors and allows query of reasons."""
    reg = PluginRegistry()

    class ValidAdapter:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def receive(self) -> None:
            pass

    reg.register("mdrap.adapters", "valid_adapter", ValidAdapter)
    assert reg.get("mdrap.adapters", "valid_adapter") is ValidAdapter

    class BrokenStorage:
        def close(self) -> None:
            pass

    with pytest.raises(PluginValidationError) as exc_info:
        reg.register("mdrap.storage_backends", "broken_sink", BrokenStorage)

    assert "missing required write methods" in str(exc_info.value)
    errors = reg.get_errors("mdrap.storage_backends")
    assert "broken_sink" in errors


# ===========================================================================
# Card #4: Pipeline Pre/Post Evaluate Hooks
# ===========================================================================


def test_pipeline_pre_and_post_evaluate_hooks():
    """Verify Pipeline executes pre_evaluate_hook and post_evaluate_hook in correct order."""
    store = Store(":memory:")
    events_pre_evaluated: list[str] = []
    events_post_evaluated: list[str] = []

    def pre_hook(ev: CanonicalEvent) -> CanonicalEvent | None:
        events_pre_evaluated.append(ev.event_id)
        # Tag event before quality checks
        ev.instrument_id = ev.instrument_id.upper()
        return ev

    def post_hook(ev: CanonicalEvent) -> CanonicalEvent | None:
        events_post_evaluated.append(ev.event_id)
        # Add a custom reason tag
        ev.reasons.append("CUSTOM_POST_HOOK_APPLIED")
        return ev

    pipeline = Pipeline(
        store=store,
        pre_evaluate_hook=pre_hook,
        post_evaluate_hook=post_hook,
    )

    now = time.time()
    raw = RawEvent(
        source="HOOK_VENUE",
        payload={
            "instrument": "eth-usd",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 1,
            "price": 3000.0,
            "quantity": 2.0,
        },
    )

    res = pipeline.process_one(raw)
    assert res is not None
    assert len(events_pre_evaluated) == 1
    assert len(events_post_evaluated) == 1
    assert res.instrument_id == "ETH-USD"
    assert "CUSTOM_POST_HOOK_APPLIED" in res.reasons

    # Verify batch execution of hooks
    events_pre_evaluated.clear()
    events_post_evaluated.clear()

    raws = [
        RawEvent(
            source="HOOK_VENUE",
            payload={
                "instrument": "btc-usd",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": 2 + i,
                "price": 50000.0 + i,
                "quantity": 1.0,
            },
        )
        for i in range(3)
    ]

    batch_res = pipeline.process_batch(raws)
    assert len(batch_res) == 3
    assert len(events_pre_evaluated) == 3
    assert len(events_post_evaluated) == 3
    for b in batch_res:
        assert b.instrument_id == "BTC-USD"
        assert "CUSTOM_POST_HOOK_APPLIED" in b.reasons

    store.close()


def test_pipeline_pre_evaluate_hook_drop():
    """Verify pre_evaluate_hook returning None drops the event before evaluation and dispatch."""
    store = Store(":memory:")

    def drop_hook(ev: CanonicalEvent) -> CanonicalEvent | None:
        if ev.price and ev.price < 100.0:
            return None  # Drop sub-100 ticks
        return ev

    pipeline = Pipeline(store=store, pre_evaluate_hook=drop_hook)
    now = time.time()

    raw_drop = RawEvent(
        source="TEST",
        payload={
            "instrument": "PENNY",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 1,
            "price": 5.0,
            "quantity": 100.0,
        },
    )
    raw_keep = RawEvent(
        source="TEST",
        payload={
            "instrument": "BLUECHIP",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 2,
            "price": 500.0,
            "quantity": 10.0,
        },
    )

    dropped_one = pipeline.process_one(raw_drop)
    assert dropped_one is not None
    assert dropped_one.quality_status == QualityStatus.INVALID
    assert "HOOK_DROPPED" in dropped_one.reasons
    assert pipeline.metrics.hook_dropped == 1
    assert pipeline._pending_raw_payloads == {}
    assert pipeline.process_one(raw_keep) is not None

    raw_drop_batch = RawEvent(
        source="TEST",
        payload={
            "instrument": "PENNY",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 3,
            "price": 5.0,
            "quantity": 100.0,
        },
    )
    raw_keep_batch = RawEvent(
        source="TEST",
        payload={
            "instrument": "BLUECHIP",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 4,
            "price": 500.0,
            "quantity": 10.0,
        },
    )
    batch = pipeline.process_batch([raw_drop_batch, raw_keep_batch])
    assert len(batch) == 2
    assert any("HOOK_DROPPED" in event.reasons for event in batch)
    assert any(event.instrument_id == "BLUECHIP" for event in batch)
    assert pipeline.metrics.hook_dropped == 2
    pipeline.finish()
    quarantined = store.conn.execute(
        "SELECT reasons FROM quarantine ORDER BY rowid"
    ).fetchall()
    assert sum("hook dropped" in row[0].lower() for row in quarantined) == 2

    store.close()


def test_hook_exceptions_are_quarantined_and_isolated():
    """Hook exceptions become visible outcomes and do not abort later events."""
    store = Store(":memory:")

    def raising_hook(ev: CanonicalEvent) -> CanonicalEvent:
        raise RuntimeError("hook failure")

    pipeline = Pipeline(store=store, pre_evaluate_hook=raising_hook)
    now = time.time()
    raw_events = [
        RawEvent(
            source="TEST",
            payload={
                "instrument": f"SYMBOL{i}",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": i + 1,
                "price": 100.0 + i,
                "quantity": 1.0,
            },
        )
        for i in range(5)
    ]

    one = pipeline.process_one(raw_events[0])
    batch = pipeline.process_batch(raw_events[1:])
    assert one is not None and "HOOK_ERROR" in one.reasons
    assert len(batch) == 4
    assert all("HOOK_ERROR" in event.reasons for event in batch)
    assert pipeline.metrics.hook_errors == 5
    assert pipeline.metrics.processed == 5
    assert pipeline._pending_raw_payloads == {}
    pipeline.finish()
    assert store.conn.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 5
    store.close()


def test_post_hook_drop_and_exception_are_accounted():
    now = time.time()
    raw_events = [
        RawEvent(
            source="POST_TEST",
            payload={
                "instrument": f"POST{i}",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": i + 1,
                "price": 100.0 + i,
                "quantity": 1.0,
            },
        )
        for i in range(3)
    ]

    drop_store = Store(":memory:")
    drop_pipeline = Pipeline(store=drop_store, post_evaluate_hook=lambda ev: None)
    dropped = drop_pipeline.process_one(raw_events[0])
    assert dropped is not None and "HOOK_DROPPED" in dropped.reasons
    assert drop_pipeline.metrics.hook_dropped == 1
    assert drop_pipeline._pending_raw_payloads == {}
    drop_pipeline.finish()
    assert drop_store.conn.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 1
    drop_store.close()

    error_store = Store(":memory:")

    def broken_post_hook(ev: CanonicalEvent) -> CanonicalEvent:
        raise LookupError("post-hook failure")

    error_pipeline = Pipeline(store=error_store, post_evaluate_hook=broken_post_hook)
    outcomes = error_pipeline.process_batch(raw_events[1:])
    assert len(outcomes) == 2
    assert all("HOOK_ERROR" in event.reasons for event in outcomes)
    assert error_pipeline.metrics.hook_errors == 2
    assert error_pipeline._pending_raw_payloads == {}
    error_pipeline.finish()
    assert (
        error_store.conn.execute("SELECT count(*) FROM quarantine").fetchone()[0] == 2
    )
    error_store.close()
