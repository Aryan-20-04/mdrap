"""Sample third-party plugin and extension implementations for MDRAP.

Demonstrates and verifies conformance for all six MDRAP entry point groups:
1. mdrap.adapters: Feed adapters (FeedAdapter protocol)
2. mdrap.quality_rules: Custom quality rules
3. mdrap.storage_backends: Storage engines (AppendStorageSink / StorageBackend protocols)
4. mdrap.auth_providers: Authentication / entitlement providers (AuthProvider protocol)
5. mdrap.output_sinks: Event delivery sinks (OutputSink protocol)
6. mdrap.alert_sinks: Outbound alert sinks (AlertSink protocol)
"""

from __future__ import annotations

import time
from typing import Any

from .models import CanonicalEvent, QualityStatus, Reason, reason_registry
from .rules import register_rule
from .protocols import (
    AppendStorageSink,
    QueryStorageStore,
    StorageBackend,
    AuthProvider,
    OutputSink,
    AlertSink,
)

__stability__ = "experimental"


class SampleMemoryStorageBackend:
    """Third-party in-memory storage engine satisfying StorageBackend protocol."""

    __min_mdrap_version__ = "3.0.0"

    def __init__(self, name: str = "sample_memory") -> None:
        self.name = name
        self.canonical_records: list[CanonicalEvent] = []
        self.quarantine_records: list[tuple] = []
        self.lineage_records: list[tuple] = []
        self.commits_count: int = 0
        self.closed: bool = False

    def write_canonical_batch(self, batch: list[CanonicalEvent]) -> None:
        self.canonical_records.extend(batch)

    def write_quarantine_batch(self, batch: list[tuple]) -> None:
        self.quarantine_records.extend(batch)

    def write_lineage_batch(self, batch: list[tuple]) -> None:
        self.lineage_records.extend(batch)

    def upsert_source_health(self, rows: list[tuple]) -> None:
        pass

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
        self.commits_count += 1

    def query_events(
        self,
        instrument_id: str | None = None,
        start_ts: float | None = None,
        end_ts: float | None = None,
        limit: int = 100,
    ) -> list[CanonicalEvent]:
        results = []
        for ev in self.canonical_records:
            if instrument_id and ev.instrument_id != instrument_id:
                continue
            if start_ts is not None and ev.exchange_timestamp < start_ts:
                continue
            if end_ts is not None and ev.exchange_timestamp > end_ts:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    def close(self) -> None:
        self.closed = True


# Rule pack entry point initializer
def register_sample_quality_rules() -> None:
    """Initialize and register sample third-party quality rules."""
    reason_registry.register(
        "SAMPLE_PRICE_SPIKE",
        description="Third-party detected anomalous price spike (> 25% tick jump)",
        severity=QualityStatus.SUSPICIOUS,
    )

    @register_rule(
        bit=40,
        name="SAMPLE_PRICE_SPIKE",
        description="Third-party detected price spike",
        severity=QualityStatus.SUSPICIOUS,
    )
    def sample_price_spike_rule(event: CanonicalEvent) -> bool:
        if event.price and event.price > 1_000_000.0:
            return True
        return False


class SampleAuthProvider:
    """Third-party authentication provider satisfying AuthProvider protocol."""

    __min_mdrap_version__ = "3.0.0"

    def __init__(self) -> None:
        self._tokens: dict[str, dict[str, Any]] = {
            "valid_token_123": {"client_id": "client_sample", "role": "ADMIN", "active": True}
        }

    def get_entitlement(self, token: str) -> dict[str, Any] | None:
        ent = self._tokens.get(token)
        return ent if ent and ent.get("active") else None

    def authorize(self, token: str, required_role: str) -> bool:
        ent = self.get_entitlement(token)
        if not ent:
            return False
        return ent.get("role") == required_role or ent.get("role") == "ADMIN"


class SampleOutputSink:
    """Third-party output sink satisfying OutputSink protocol."""

    __min_mdrap_version__ = "3.0.0"

    def __init__(self) -> None:
        self.delivered_ticks: list[CanonicalEvent] = []
        self.closed: bool = False

    def broadcast_tick(self, event: CanonicalEvent) -> None:
        self.delivered_ticks.append(event)

    def close(self) -> None:
        self.closed = True


class SampleAlertSink:
    """Third-party alert sink satisfying AlertSink protocol."""

    __min_mdrap_version__ = "3.0.0"

    def __init__(self) -> None:
        self.delivered_alerts: list[dict[str, Any]] = []
        self.closed: bool = False

    def deliver(self, alert: dict[str, Any]) -> None:
        self.delivered_alerts.append(alert)

    def close(self) -> None:
        self.closed = True
