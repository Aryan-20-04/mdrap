"""MDRAP Extension Protocol Interfaces.

Formal Protocol definitions for the four swappable subsystems in MDRAP.
These Protocols define the contracts that alternative implementations must satisfy
to be used as drop-in replacements for the default concrete classes.

Each Protocol uses ``typing.Protocol`` with ``@runtime_checkable`` so that
conformance can be verified at runtime via ``isinstance()`` checks without
requiring explicit inheritance.

Extension Points:
    - ``StorageBackend``: Persistent event storage (default: ``Store`` in storage.py)
    - ``AuthProvider``: Authentication and authorization (default: ``SecurityManager`` in security.py)
    - ``QualityEvaluator``: Data quality evaluation (default: ``QualityEngine`` in quality.py)
    - ``OutputSink``: Event delivery to consumers (default: SHM/TCP/WebSocket in service.py)

See Also:
    - docs/extending/ for implementation guides
    - docs/map.md for the full architecture map
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from models import CanonicalEvent

__stability__ = "stable"

__all__ = [
    "StorageBackend",
    "AuthProvider",
    "QualityEvaluator",
    "OutputSink",
    "AlertSink",
    "DeliveryResult",
]


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for persistent event storage engines.

    The pipeline calls ``write_batches_atomic()`` on every flush cycle
    (default: every 2000 events or 1 second). Implementations must handle
    batched writes atomically — either all rows commit or none do.

    The default implementation is ``Store`` in storage.py (SQLite with WAL mode).
    Alternative implementations could target PostgreSQL, DuckDB, Parquet files,
    or cloud-native time-series databases.

    Minimum Viable Implementation:
        For pipeline integration, implement at minimum:
        ``write_batches_atomic``, ``commit``, and ``close``.
        Query methods are only needed if the API or CLI is used.
    """

    def write_canonical_batch(self, events: list[CanonicalEvent]) -> None:
        """Persist a batch of validated canonical events."""
        ...

    def write_quarantine_batch(self, rows: list[tuple]) -> None:
        """Persist a batch of quarantined (INVALID/SUSPICIOUS) events."""
        ...

    def write_lineage_batch(self, rows: list[tuple]) -> None:
        """Persist a batch of lineage/provenance records."""
        ...

    def upsert_source_health(self, rows: list[tuple]) -> None:
        """Update per-source reliability and health statistics."""
        ...

    def write_batches_atomic(
        self,
        canonical: list[CanonicalEvent] | None = None,
        quarantine: list[tuple] | None = None,
        lineage: list[tuple] | None = None,
        source_health: list[tuple] | None = None,
    ) -> None:
        """Atomically persist all batch types in a single transaction."""
        ...

    def write_bbo_batch(self, bbos: list) -> None:
        """Persist consolidated best-bid-offer snapshots."""
        ...

    def write_ohlcv_batch(self, candles: list[dict]) -> None:
        """Persist OHLCV candle aggregations."""
        ...

    def write_spread_batch(self, spreads: list[dict]) -> None:
        """Persist spread statistics."""
        ...

    def write_volatility_batch(self, stats: list[dict]) -> None:
        """Persist volatility statistics."""
        ...

    def write_depth_batch(self, ladders: list) -> None:
        """Persist consolidated depth ladder snapshots."""
        ...

    def write_vwap_batch(self, curves: list) -> None:
        """Persist VWAP curve data points."""
        ...

    def commit(self) -> None:
        """Flush pending writes to durable storage."""
        ...

    def query_events(
        self, instrument_id: str | None = None, limit: int = 1000
    ) -> list[dict]:
        """Query canonical events, optionally filtered by instrument."""
        ...

    def latest(self, instrument_id: str, limit: int = 1) -> list[dict]:
        """Retrieve the most recent events for an instrument."""
        ...

    def feed_health(self) -> list[dict]:
        """Retrieve per-source health and reliability statistics."""
        ...

    def quarantine_sample(self, limit: int = 20) -> list[dict]:
        """Retrieve a sample of quarantined events for inspection."""
        ...

    def counts(self) -> dict[str, int]:
        """Return row counts for canonical, quarantine, and lineage tables."""
        ...

    def close(self) -> None:
        """Release all resources and connections."""
        ...


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for authentication and authorization providers.

    The API layer calls ``get_entitlement()`` to resolve tokens and
    ``authorize()`` to enforce RBAC. The default implementation is
    ``SecurityManager`` in security.py (HMAC feed auth, SHA-256 key hashing,
    Merkle-chained audit log).

    Alternative implementations could provide OAuth2/JWT verification,
    LDAP/Active Directory lookup, or mTLS certificate-based auth.

    Role Hierarchy:
        VIEWER (1) < OPERATOR (2) < ADMIN (3)
    """

    def get_entitlement(
        self, token: str, active_only: bool = False
    ) -> Any | None:
        """Resolve a bearer token or API key to a ClientEntitlement, or None."""
        ...

    def authorize(
        self,
        actor_or_token: Any,
        required_role: Any,
        action_name: str = "",
    ) -> None:
        """Verify that actor has sufficient permissions. Raise AccessDenied if not."""
        ...

    def log_audit(
        self,
        action: str,
        actor: str = "system",
        role: Any = None,
        details: str = "",
        timestamp: float | None = None,
    ) -> str:
        """Record a tamper-evident audit log entry. Returns the entry hash."""
        ...


@runtime_checkable
class QualityEvaluator(Protocol):
    """Protocol for data quality evaluation engines.

    The pipeline calls ``evaluate()`` on every normalized CanonicalEvent.
    The evaluator must set ``quality_status`` and ``reasons`` on the event
    according to the monotonic priority hierarchy:
        INVALID (2) > SUSPICIOUS (1) > VALID (0)

    An event's quality status can never be downgraded.

    The default implementations are ``QualityEngine`` (pure Python) and
    ``FastQualityEngine`` (C FFI accelerated) in quality.py / fastpath.py.
    """

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        """Evaluate data quality and return the event with updated status/reasons."""
        ...

    def reset(self) -> None:
        """Clear all per-instrument tracking state."""
        ...


@runtime_checkable
class OutputSink(Protocol):
    """Protocol for event delivery to downstream consumers.

    After pipeline processing, events are broadcast to consumers via one or
    more output sinks. The default system provides three delivery paths:
        - SHM ring buffer (shm.py) — zero-copy, sub-microsecond IPC
        - TCP socket streaming (service.py) — JSON/binary framing
        - WebSocket pub/sub (api.py) — browser-compatible streaming

    Alternative implementations could target Kafka, Redis Streams, NATS,
    or custom message brokers.
    """

    def broadcast_tick(
        self, event: CanonicalEvent, bbo: Any | None = None
    ) -> None:
        """Deliver a processed tick event to consumers."""
        ...

    def broadcast_depth(self, ladder: Any) -> None:
        """Deliver a consolidated depth ladder update to consumers."""
        ...

    def close(self) -> None:
        """Release all delivery resources and connections."""
        ...


@dataclass(slots=True)
class DeliveryResult:
    """Evidentiary outcome of an external alert delivery attempt."""

    alert_id: int
    sink_name: str
    status: str  # "DELIVERED", "FAILED", "PENDING"
    attempts: int = 1
    error_message: str | None = None
    delivered_at: float | None = None


@runtime_checkable
class AlertSink(Protocol):
    """Protocol for external alert delivery sinks (Slack, Webhook, PagerDuty).

    Alert sinks consume from the alerting engine outside the synchronous tick evaluation
    loop. Implementations must handle delivery failures gracefully, retry with backoff,
    and never block tick processing.
    """

    def deliver(self, alert: Any) -> DeliveryResult:
        """Deliver an alert payload to the destination system.

        Args:
            alert: The Alert instance triggered by the engine.

        Returns:
            DeliveryResult recording delivery status and diagnostic metadata.
        """
        ...

    def close(self) -> None:
        """Release underlying client or network connections."""
        ...

