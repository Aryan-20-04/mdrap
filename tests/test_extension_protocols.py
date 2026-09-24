"""Tests for MDRAP Extension Protocols and Plugin Discovery.

Verifies:
1. Default system classes satisfy their respective Protocol interfaces at runtime.
2. Minimal custom implementations satisfy each Protocol without inheriting from internal classes.
3. Custom implementations can be used with pipeline/components.
4. Dynamic discovery for quality rules and adapters functions without errors.
"""

from __future__ import annotations

import time
import pytest
from typing import Any, Iterator

from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from protocols import StorageBackend, AuthProvider, QualityEvaluator, OutputSink
from storage import Store
from security import SecurityManager, Role, ClientEntitlement
from quality import QualityEngine
from rules import register_rule, unregister_rule, clear_user_rules, discover_quality_rules, evaluate_user_rules
from adapters import FeedAdapter, discover_adapters


# ---------------------------------------------------------------------------
# Minimal Alternate Implementations
# ---------------------------------------------------------------------------

class MinimalMemoryStorage:
    """Minimal alternative in-memory storage implementation for testing StorageBackend."""

    def __init__(self) -> None:
        self.events: list[CanonicalEvent] = []
        self.quarantine: list[tuple] = []
        self.lineage: list[tuple] = []
        self.source_health: list[tuple] = []
        self.committed = False
        self.closed = False

    def write_canonical_batch(self, events: list[CanonicalEvent]) -> None:
        self.events.extend(events)

    def write_quarantine_batch(self, rows: list[tuple]) -> None:
        self.quarantine.extend(rows)

    def write_lineage_batch(self, rows: list[tuple]) -> None:
        self.lineage.extend(rows)

    def upsert_source_health(self, rows: list[tuple]) -> None:
        self.source_health.extend(rows)

    def write_batches_atomic(
        self,
        canonical: list[CanonicalEvent] | None = None,
        quarantine: list[tuple] | None = None,
        lineage: list[tuple] | None = None,
        source_health: list[tuple] | None = None,
    ) -> None:
        if canonical:
            self.write_canonical_batch(canonical)
        if quarantine:
            self.write_quarantine_batch(quarantine)
        if lineage:
            self.write_lineage_batch(lineage)
        if source_health:
            self.upsert_source_health(source_health)

    def write_bbo_batch(self, bbos: list) -> None:
        pass

    def write_ohlcv_batch(self, candles: list[dict]) -> None:
        pass

    def write_spread_batch(self, spreads: list[dict]) -> None:
        pass

    def write_volatility_batch(self, stats: list[dict]) -> None:
        pass

    def write_depth_batch(self, ladders: list) -> None:
        pass

    def write_vwap_batch(self, curves: list) -> None:
        pass

    def commit(self) -> None:
        self.committed = True

    def query_events(self, instrument_id: str | None = None, limit: int = 1000) -> list[dict]:
        return [{"event_id": e.event_id, "instrument_id": e.instrument_id} for e in self.events[:limit]]

    def latest(self, instrument_id: str, limit: int = 1) -> list[dict]:
        return [
            {"event_id": e.event_id, "instrument_id": e.instrument_id}
            for e in reversed(self.events)
            if e.instrument_id == instrument_id
        ][:limit]

    def feed_health(self) -> list[dict]:
        return []

    def quarantine_sample(self, limit: int = 20) -> list[dict]:
        return []

    def counts(self) -> dict[str, int]:
        return {"canonical": len(self.events), "quarantine": len(self.quarantine), "lineage": len(self.lineage)}

    def close(self) -> None:
        self.closed = True


class MinimalAuthProvider:
    """Minimal custom AuthProvider for testing."""

    def __init__(self) -> None:
        self.keys: dict[str, ClientEntitlement] = {
            "test_token": ClientEntitlement(
                token="test_token",
                client_id="ClientA",
                role=Role.ADMIN,
                is_active=True,
            )
        }

    def get_entitlement(self, token: str, active_only: bool = False) -> ClientEntitlement | None:
        ent = self.keys.get(token)
        if active_only and ent and not ent.is_active:
            return None
        return ent

    def authorize(self, actor_or_token: Any, required_role: Any, action_name: str = "") -> None:
        pass

    def log_audit(
        self,
        action: str,
        actor: str = "system",
        role: Any = None,
        details: str = "",
        timestamp: float | None = None,
    ) -> str:
        return "mock_audit_hash"


class MinimalQualityEvaluator:
    """Minimal custom QualityEvaluator."""

    def __init__(self) -> None:
        self.eval_count = 0

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        self.eval_count += 1
        if event.price is not None and event.price > 100000.0:
            event.quality_status = QualityStatus.INVALID
            event.reasons.append(Reason.PRICE_ANOMALY.value)
        return event

    def reset(self) -> None:
        self.eval_count = 0


class MinimalOutputSink:
    """Minimal custom OutputSink."""

    def __init__(self) -> None:
        self.ticks: list[CanonicalEvent] = []
        self.depths: list[Any] = []
        self.is_closed = False

    def broadcast_tick(self, event: CanonicalEvent, bbo: Any | None = None) -> None:
        self.ticks.append(event)

    def broadcast_depth(self, ladder: Any) -> None:
        self.depths.append(ladder)

    def close(self) -> None:
        self.is_closed = True


class MinimalFeedAdapter:
    """Minimal custom FeedAdapter."""

    def __init__(self) -> None:
        self.opened = False
        self.closed = False

    def open(self) -> None:
        self.opened = True

    def __iter__(self) -> Iterator[RawEvent]:
        now = time.time()
        yield RawEvent(
            source="TEST_FEED",
            payload={
                "instrument": "BTC/USD",
                "event_type": "TRADE",
                "exchange_ts": now,
                "sequence": 1,
                "price": 50000.0,
                "quantity": 1.0,
            },
        )

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_default_classes_satisfy_protocols():
    """Verify built-in components fulfill their respective runtime protocols."""
    store = Store(":memory:")
    try:
        assert isinstance(store, StorageBackend)
    finally:
        store.close()

    sec = SecurityManager()
    assert isinstance(sec, AuthProvider)

    engine = QualityEngine()
    assert isinstance(engine, QualityEvaluator)


def test_minimal_alternate_implementations_satisfy_protocols():
    """Verify third-party / alternate classes satisfy protocols via structural subtyping."""
    mem_store = MinimalMemoryStorage()
    assert isinstance(mem_store, StorageBackend)

    auth = MinimalAuthProvider()
    assert isinstance(auth, AuthProvider)

    qual = MinimalQualityEvaluator()
    assert isinstance(qual, QualityEvaluator)

    sink = MinimalOutputSink()
    assert isinstance(sink, OutputSink)

    feed = MinimalFeedAdapter()
    assert isinstance(feed, FeedAdapter)


def test_minimal_storage_pipeline_interop():
    """Verify Pipeline accepts and flushes to an alternate StorageBackend."""
    from pipeline import Pipeline

    alt_store = MinimalMemoryStorage()
    pipeline = Pipeline(store=alt_store)

    now = time.time()
    raw = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 1,
            "price": 50000.0,
            "quantity": 1.5,
        },
    )
    canonical = pipeline.process_one(raw)
    assert canonical is not None
    assert canonical.quality_status == QualityStatus.VALID

    pipeline.flush()
    assert len(alt_store.events) == 1
    assert alt_store.events[0].instrument_id == "BTC/USD"

    alt_store.close()
    assert alt_store.closed is True


def test_minimal_quality_evaluator_interop():
    """Verify Pipeline can use a custom QualityEvaluator."""
    from pipeline import Pipeline

    alt_store = MinimalMemoryStorage()
    alt_quality = MinimalQualityEvaluator()
    pipeline = Pipeline(store=alt_store, quality=alt_quality)

    now = time.time()
    raw_spike = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "event_type": "TRADE",
            "exchange_ts": now,
            "sequence": 2,
            "price": 150000.0,
            "quantity": 1.0,
        },
    )
    res = pipeline.process_one(raw_spike)
    assert res is not None
    assert res.quality_status == QualityStatus.INVALID
    assert Reason.PRICE_ANOMALY.value in res.reasons
    assert alt_quality.eval_count == 1


def test_quality_rule_plugin_discovery():
    """Verify discover_quality_rules executes gracefully."""
    count = discover_quality_rules()
    assert isinstance(count, int)
    assert count >= 0


def test_adapter_plugin_discovery():
    """Verify discover_adapters discovers built-in or registered entry point adapters."""
    adapters = discover_adapters()
    assert isinstance(adapters, dict)
    # If template is registered in pyproject, it may appear or return a dict
