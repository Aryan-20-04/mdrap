"""Licensing, Entitlements, and Durable Usage Accounting (Phase 3 Workstream H).

Implements institutional compliance metering with durable SQLite backing:
  - Fail-closed entitlement verification (expiry, source/symbol filtering, revocation).
  - Durable, restart-safe usage accounting with unique idempotency keys.
  - Multi-tenant usage aggregation.
  - Audit report exports in JSON and CSV formats.
"""

from __future__ import annotations

import csv
import enum
import io
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .security import ClientEntitlement

logger = logging.getLogger(__name__)

__stability__ = "stable"


class MeteringUnit(str, enum.Enum):
    """Auditable billable units for market data licensing."""

    DISTRIBUTED_EVENT = "DISTRIBUTED_EVENT"
    INGESTED_EVENT = "INGESTED_EVENT"
    SUBSCRIPTION_TICK = "SUBSCRIPTION_TICK"


class EntitlementError(Exception):
    """Raised when entitlement authorization or licensing checks fail."""


@dataclass
class UsageRecord:
    """Immutable audit record representing a metered usage event."""

    record_id: str
    client_id: str
    tenant_id: str
    source: str
    symbol: str
    unit: MeteringUnit
    count: int
    window_start: float
    window_end: float
    idempotency_key: str
    recorded_at: float


class DurableUsageMeter:
    """Thread-safe, durable usage accounting manager backed by SQLite WAL."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS metering_records (
                    record_id TEXT PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    window_start REAL NOT NULL,
                    window_end REAL NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    recorded_at REAL NOT NULL
                );
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metering_client
                ON metering_records (client_id, recorded_at);
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metering_tenant
                ON metering_records (tenant_id, recorded_at);
            """)

    def verify_entitlement(
        self,
        entitlement: Optional[ClientEntitlement],
        source: str = "",
        symbol: str = "",
    ) -> bool:
        """Fail-closed evaluation of client entitlement permissions."""
        if entitlement is None:
            return False
        if not entitlement.is_active:
            return False
        if entitlement.expires_at is not None and time.time() > entitlement.expires_at:
            return False
        if entitlement.allowed_sources and source and source not in entitlement.allowed_sources:
            return False
        if entitlement.allowed_symbols and symbol and symbol not in entitlement.allowed_symbols:
            return False
        return True

    def record_usage(
        self,
        client_id: str,
        tenant_id: str,
        source: str,
        symbol: str,
        count: int,
        idempotency_key: str,
        unit: MeteringUnit = MeteringUnit.DISTRIBUTED_EVENT,
        window_start: Optional[float] = None,
        window_end: Optional[float] = None,
    ) -> bool:
        """Record billable consumption durably.
        
        Returns True if newly recorded; False if skipped due to duplicate idempotency key.
        """
        now = time.time()
        w_start = window_start if window_start is not None else now
        w_end = window_end if window_end is not None else now
        record_id = f"meter_{int(now * 1000)}_{idempotency_key[:16]}"

        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO metering_records (
                            record_id, client_id, tenant_id, source, symbol,
                            unit, count, window_start, window_end, idempotency_key, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            record_id,
                            client_id,
                            tenant_id,
                            source,
                            symbol,
                            unit.value,
                            count,
                            w_start,
                            w_end,
                            idempotency_key,
                            now,
                        ),
                    )
                return True
            except sqlite3.IntegrityError:
                # Idempotent deduplication: already accounted for
                logger.debug(
                    "[metering] Skipping duplicate usage event with idempotency key %s",
                    idempotency_key,
                )
                return False

    def get_client_total(self, client_id: str, since: float = 0.0) -> int:
        """Return total aggregated units metered for a client since timestamp."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM metering_records WHERE client_id = ? AND recorded_at >= ?;",
                (client_id, since),
            )
            return cur.fetchone()[0]

    def get_summary(
        self,
        start_ts: float = 0.0,
        end_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Return aggregated usage summary grouped by dimensions."""
        end = end_ts if end_ts is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT tenant_id, client_id, source, symbol, unit, SUM(count) as total_count, COUNT(*) as batch_count
                FROM metering_records
                WHERE recorded_at >= ? AND recorded_at <= ?
                GROUP BY tenant_id, client_id, source, symbol, unit
                ORDER BY tenant_id, client_id, total_count DESC;
                """,
                (start_ts, end),
            )
            return [dict(row) for row in cur.fetchall()]

    def export_report_json(
        self,
        start_ts: float = 0.0,
        end_ts: Optional[float] = None,
    ) -> str:
        """Generate machine-readable JSON compliance report."""
        summary = self.get_summary(start_ts, end_ts)
        payload = {
            "report_version": "1.0",
            "generated_at": time.time(),
            "query_window": {"start_ts": start_ts, "end_ts": end_ts or time.time()},
            "line_items": summary,
        }
        return json.dumps(payload, indent=2)

    def export_report_csv(
        self,
        start_ts: float = 0.0,
        end_ts: Optional[float] = None,
    ) -> str:
        """Generate machine-readable CSV compliance report."""
        summary = self.get_summary(start_ts, end_ts)
        output = io.StringIO()
        fieldnames = [
            "tenant_id",
            "client_id",
            "source",
            "symbol",
            "unit",
            "total_count",
            "batch_count",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary:
            writer.writerow(row)
        return output.getvalue()

    def close(self) -> None:
        """Close SQLite database connection."""
        with self._lock:
            self._conn.close()
