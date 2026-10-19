"""Storage layer: High-throughput SQLite persistence with batched commits.

This module manages the persistent storage tier for MDRAP, rigorously separating raw,
processed, and derived data structures in compliance with core architectural principles
(MDRAP Spec §5 & §26):
  - `canonical_events`: Fast queryable store of VALID and SUSPICIOUS ticks.
  - `quarantine`: SUSPICIOUS and INVALID records with raw payloads preserved for replay.
  - `lineage`: Immutable decision audit trail documenting source arbitrations and logic.
  - `source_health`: Real-time reliability and error rate snapshots per upstream feed.
  - `ohlcv_candles`: Aggregated multi-interval historical candles.
  - `spread_stats` & `volatility_stats`: Real-time microstructure summary statistics.
  - `consolidated_bbo`: National Best Bid and Offer top-of-book quotes.
  - `watchdog_alerts`: Anomaly alerts and automated remediation logs.

Performance & Concurrency Pragmas:
  - `PRAGMA journal_mode=WAL;`: Write-Ahead Logging allows concurrent readers without
    blocking active writers, and avoids disk sync stalls during hot writes.
  - `PRAGMA synchronous=NORMAL;`: Reduces fsync frequency while maintaining zero corruption
    guarantees in WAL mode.
  - `PRAGMA mmap_size=268435456;`: Memory-maps 256MB of database file directly into process
    virtual memory, bypassing operating system read/write syscall context switch overhead.
  - `PRAGMA cache_size=-64000;`: Allocates 64MB of dedicated RAM page cache.
  - `PRAGMA temp_store=MEMORY;`: Keeps transient sorting and index B-trees entirely in RAM.
  - `executemany`: Batched multi-row commits eliminate per-row filesystem sync overhead.
"""

from __future__ import annotations

from contextlib import contextmanager
import functools
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

from .models import CanonicalEvent
from .storage_audit import StoreAuditMixin
from .storage_schema import (
    BYTES_PER_MB,
    CURRENT_SCHEMA_VERSION,
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_SQLITE_CACHE_MB,
    DEFAULT_SQLITE_MMAP_MB,
    DEFAULT_SQLITE_RETRIES,
    DEFAULT_SQLITE_TIMEOUT_S,
    PAGE_CACHE_KIB_PER_MB,
    SCHEMA,
    SQLITE_RETRY_BACKOFF_READ_S,
    SQLITE_RETRY_BACKOFF_WRITE_S,
    PersistenceMode,
    StorageConflictError,
    _compute_merkle_root,
    _read_synchronized,
    _synchronized,
)

__stability__ = "stable"

logger = logging.getLogger("mdrap.storage")

__all__ = [
    "BYTES_PER_MB",
    "CURRENT_SCHEMA_VERSION",
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_SQLITE_CACHE_MB",
    "DEFAULT_SQLITE_MMAP_MB",
    "DEFAULT_SQLITE_RETRIES",
    "DEFAULT_SQLITE_TIMEOUT_S",
    "PAGE_CACHE_KIB_PER_MB",
    "PersistenceMode",
    "SCHEMA",
    "SQLITE_RETRY_BACKOFF_READ_S",
    "SQLITE_RETRY_BACKOFF_WRITE_S",
    "StorageConflictError",
    "Store",
    "StoreAuditMixin",
    "_compute_merkle_root",
    "_read_synchronized",
    "_synchronized",
]


class Store(StoreAuditMixin):
    """Thread-safe SQLite storage engine for MDRAP event stream and analytics."""

    def __init__(
        self,
        path: str = ":memory:",
        durability: str = "balanced",
        persistence_mode: str | None = None,
    ):
        self.path = path
        self.db_path = path
        self.durability = (durability or "balanced").lower()
        self._on_close: list = []
        if self.durability not in ("fast", "balanced", "compliance"):
            raise ValueError(
                f"Invalid durability mode: {durability!r}. Must be one of ('fast', 'balanced', 'compliance')"
            )
        self.conflicts: int = 0
        self.recovery_conflicts: int = 0
        is_mem = (
            path == ":memory:"
            or "mode=memory" in path
            or path.startswith("file::memory:")
        )
        self.is_memory: bool = is_mem
        if persistence_mode is not None:
            self.persistence_mode = str(persistence_mode)
        else:
            self.persistence_mode = (
                PersistenceMode.DEVELOPMENT_IN_MEMORY.value
                if is_mem
                else PersistenceMode.PRODUCTION_DURABLE.value
            )
        self._lock = threading.RLock()
        self._read_lock = self._lock if is_mem else threading.RLock()
        with self._lock:
            if path != ":memory:" and not path.startswith("file:"):
                dir_path = os.path.dirname(os.path.abspath(path))
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
            self.conn = sqlite3.connect(
                path, timeout=DEFAULT_SQLITE_TIMEOUT_S, check_same_thread=False
            )
            self.conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};")
            try:
                cur = self.conn.execute("PRAGMA journal_mode=WAL;")
                jm_row = cur.fetchone()
                if (
                    path != ":memory:"
                    and not path.startswith("file::memory:")
                    and jm_row
                    and jm_row[0].lower() != "wal"
                ):
                    logger.warning(
                        "SQLite database at %s could not set journal_mode=WAL (got %s)",
                        path,
                        jm_row[0],
                    )
            except sqlite3.OperationalError:
                pass  # In-memory or read-only filesystems do not support WAL

            if self.durability == "fast":
                self.conn.execute("PRAGMA synchronous=OFF;")
            elif self.durability == "compliance":
                self.conn.execute("PRAGMA synchronous=FULL;")
            else:
                self.conn.execute("PRAGMA synchronous=NORMAL;")
            try:
                self.conn.execute("PRAGMA wal_autocheckpoint=1000;")
            except sqlite3.OperationalError:
                pass

            # Memory-tuned pragmas: 64MB mmap and 16MB page cache by default
            mmap_mb = int(
                os.environ.get("MDRAP_SQLITE_MMAP_MB", DEFAULT_SQLITE_MMAP_MB)
            )
            cache_mb = int(
                os.environ.get("MDRAP_SQLITE_CACHE_MB", DEFAULT_SQLITE_CACHE_MB)
            )
            mmap_bytes = mmap_mb * BYTES_PER_MB
            cache_kib = cache_mb * PAGE_CACHE_KIB_PER_MB
            self.conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
            self.conn.execute(f"PRAGMA cache_size=-{cache_kib};")
            self.conn.execute("PRAGMA temp_store=MEMORY;")

            cur_ver_row = self.conn.execute("PRAGMA user_version;").fetchone()
            db_version = cur_ver_row[0] if cur_ver_row else 0
            if db_version > CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported database schema version {db_version} (current platform version is {CURRENT_SCHEMA_VERSION}). "
                    f"Please upgrade MDRAP to access this database."
                )

            # Forward-only schema migrations framework
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL,
                    description TEXT NOT NULL
                );
            """)
            applied_rows = self.conn.execute(
                "SELECT version FROM schema_migrations;"
            ).fetchall()
            applied_versions = {r[0] for r in applied_rows}

            # Migration 1: Baseline institutional market data schema
            if 1 not in applied_versions:
                self.conn.executescript(SCHEMA)
                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                    (1, time.time(), "Baseline institutional market data schema"),
                )
                self.conn.execute("PRAGMA user_version = 1;")
                applied_versions.add(1)

            # Migration 2: Format versioning and Merkle audit log
            if 2 not in applied_versions:
                try:
                    self.conn.execute(
                        "ALTER TABLE audit_log ADD COLUMN format_version INTEGER NOT NULL DEFAULT 2"
                    )
                except sqlite3.OperationalError:
                    # Column format_version already exists
                    pass
                except Exception as exc:
                    logger.debug("Migration format_version note: %s", exc)

                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                    (2, time.time(), "Format versioning and Merkle audit log"),
                )
                self.conn.execute("PRAGMA user_version = 2;")
                applied_versions.add(2)

            # Migration 3: Quarantine Merkle log and API key SHA-256 hash storage
            if 3 not in applied_versions:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS quarantine_merkle_log (
                        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        entry_hash TEXT NOT NULL,
                        prev_root TEXT NOT NULL,
                        batch_root TEXT NOT NULL,
                        batch_size INTEGER NOT NULL,
                        format_version INTEGER NOT NULL DEFAULT 3
                    );
                """)
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_quarantine_merkle_ts ON quarantine_merkle_log(timestamp);"
                )

                # Auto-migration for api_keys schema (from legacy 'token' column to 'token_hash' + 'role' + 'key_prefix')
                try:
                    cur_cols = self.conn.execute(
                        "PRAGMA table_info(api_keys)"
                    ).fetchall()
                    col_names = {c[1] for c in cur_cols}
                    if col_names and (
                        "token_hash" not in col_names
                        or "rate_limit_eps" in col_names
                        or "tier" in col_names
                    ):
                        # Legacy table exists with older columns
                        self.conn.execute(
                            "ALTER TABLE api_keys RENAME TO api_keys_legacy"
                        )
                        self.conn.execute("""
                            CREATE TABLE api_keys (
                                token_hash TEXT PRIMARY KEY,
                                key_prefix TEXT NOT NULL DEFAULT '',
                                client_id TEXT NOT NULL,
                                role TEXT NOT NULL DEFAULT 'VIEWER',
                                is_active INTEGER NOT NULL DEFAULT 1,
                                created_at REAL NOT NULL,
                                expires_at REAL
                            )
                        """)
                        self.conn.execute(
                            "CREATE INDEX IF NOT EXISTS idx_api_keys_client ON api_keys(client_id)"
                        )
                        cur_legacy = self.conn.execute("SELECT * FROM api_keys_legacy")
                        legacy_col_names = [d[0] for d in cur_legacy.description]
                        legacy_rows = cur_legacy.fetchall()
                        for r in legacy_rows:
                            row_dict = dict(zip(legacy_col_names, r))
                            raw_tok = str(row_dict.get("token") or "")
                            tok_hash = str(row_dict.get("token_hash") or "")
                            if not tok_hash and raw_tok:
                                tok_hash = hashlib.sha256(
                                    raw_tok.encode("utf-8")
                                ).hexdigest()
                            if not tok_hash:
                                continue
                            pfx = str(row_dict.get("key_prefix") or "")
                            if not pfx and raw_tok:
                                pfx = (
                                    raw_tok[:12] + "..."
                                    if len(raw_tok) > 12
                                    else raw_tok
                                )
                            elif not pfx:
                                pfx = tok_hash[:10] + "..."
                            client_id = str(
                                row_dict.get("client_id") or "Migrated_Client"
                            )
                            role = str(row_dict.get("role") or "VIEWER")
                            active = int(row_dict.get("is_active", 1))
                            created = float(row_dict.get("created_at") or time.time())
                            expires = row_dict.get("expires_at")
                            self.conn.execute(
                                """INSERT OR REPLACE INTO api_keys
                                   (token_hash, key_prefix, client_id, role, is_active, created_at, expires_at)
                                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    tok_hash,
                                    pfx,
                                    client_id,
                                    role,
                                    active,
                                    created,
                                    expires,
                                ),
                            )
                        self.conn.execute("DROP TABLE api_keys_legacy")
                    elif col_names:
                        if "role" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN role TEXT NOT NULL DEFAULT 'VIEWER'"
                            )
                        if "key_prefix" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN key_prefix TEXT NOT NULL DEFAULT ''"
                            )
                        if "key_id" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN key_id TEXT NOT NULL DEFAULT ''"
                            )
                        if "allowed_cidrs" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN allowed_cidrs TEXT NOT NULL DEFAULT '[]'"
                            )
                        if "allowed_sources" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN allowed_sources TEXT NOT NULL DEFAULT '[]'"
                            )
                        if "allowed_symbols" not in col_names:
                            self.conn.execute(
                                "ALTER TABLE api_keys ADD COLUMN allowed_symbols TEXT NOT NULL DEFAULT '[]'"
                            )
                except Exception as e:
                    import logging

                    logging.getLogger("mdrap.storage").warning(
                        "Auto-migration notice: %s", e
                    )

                self.conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                    (
                        3,
                        time.time(),
                        "Quarantine Merkle log and API key SHA-256 hash storage",
                    ),
                )
                self.conn.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION};")
                applied_versions.add(3)

            # Ensure projection_checkpoints exists across all migrated databases
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS projection_checkpoints (
                    name TEXT PRIMARY KEY,
                    last_offset INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)
            self.conn.commit()

            if not is_mem:
                try:
                    self.read_conn = sqlite3.connect(
                        path, timeout=DEFAULT_SQLITE_TIMEOUT_S, check_same_thread=False
                    )
                    self.read_conn.execute("PRAGMA query_only=ON;")
                    self.read_conn.execute(
                        f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS};"
                    )
                    self.read_conn.execute(f"PRAGMA mmap_size={mmap_bytes};")
                    self.read_conn.execute(f"PRAGMA cache_size=-{cache_kib};")
                    self.read_conn.execute("PRAGMA temp_store=MEMORY;")
                except Exception:
                    self.read_conn = self.conn
                    self._read_lock = self._lock
            else:
                self.read_conn = self.conn
                self._read_lock = self._lock

            if path != ":memory:" and not path.startswith("file:"):
                journal_path = f"{path}.journal"
                if os.path.exists(journal_path) and os.path.getsize(journal_path) > 0:
                    self._recover_from_journal(journal_path)

    def _recover_from_journal(self, journal_path: str) -> None:
        """Recover unprojected transactions from write-ahead journal after an ungraceful crash (Audit C1)."""
        if not os.path.exists(journal_path) or os.path.getsize(journal_path) == 0:
            return
        from .models import CanonicalEvent, QualityStatus

        canon: list[CanonicalEvent] = []
        quar: list[tuple] = []
        lin: list[tuple] = []
        has_corrupted_records = False
        recovery_succeeded = False
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    raw_line = line.strip()
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                        if not isinstance(obj, dict):
                            raise ValueError(
                                f"Journal line is not a JSON object: {type(obj).__name__}"
                            )
                        entry_type = obj.get("type")
                        payload = obj.get("payload")
                        if entry_type == "canonical" and isinstance(payload, dict):
                            canon.append(CanonicalEvent.from_dict(payload))
                        elif entry_type == "quarantine" and isinstance(
                            payload, (list, tuple)
                        ):
                            quar.append(tuple(payload))
                        elif entry_type == "lineage" and isinstance(
                            payload, (list, tuple)
                        ):
                            lin.append(tuple(payload))
                        else:
                            raise ValueError(
                                f"Invalid or unrecognized journal entry: type={entry_type!r}"
                            )
                    except Exception as parse_exc:
                        has_corrupted_records = True
                        logger.error(
                            "[storage] Malformed journal entry at line %d in %s: %s",
                            line_idx + 1,
                            journal_path,
                            parse_exc,
                        )
                        corrupt_id = f"corrupt_jrn_{hashlib.sha256(raw_line.encode('utf-8')).hexdigest()[:16]}_{line_idx}"
                        q_row = (
                            corrupt_id,
                            "UNKNOWN",
                            "JOURNAL_RECOVERY",
                            QualityStatus.INVALID.value,
                            json.dumps(["MALFORMED_JOURNAL_RECORD"]),
                            json.dumps({"raw_line": raw_line, "error": str(parse_exc)}),
                            time.time(),
                        )
                        quar.append(q_row)

            if canon or quar or lin:
                self.write_batches_atomic(
                    canonical=canon or None,
                    quarantine=quar or None,
                    lineage=lin or None,
                    is_recovery=True,
                )
                self.commit()
                logger.info(
                    "[storage] Recovered %d canonical, %d quarantine, %d lineage records from crash journal %s",
                    len(canon),
                    len(quar),
                    len(lin),
                    journal_path,
                )
            recovery_succeeded = True
        except Exception as exc:
            logger.warning(
                "[storage] Error recovering from journal %s: %s", journal_path, exc
            )
        finally:
            if recovery_succeeded and not has_corrupted_records:
                try:
                    with open(journal_path, "w", encoding="utf-8"):
                        pass
                except Exception:
                    pass
            elif has_corrupted_records:
                logger.warning(
                    "[storage] Preserving crash journal %s due to malformed records",
                    journal_path,
                )
            else:
                logger.warning(
                    "[storage] Preserving crash journal %s due to failed recovery (corrupted=%s, succeeded=%s)",
                    journal_path,
                    has_corrupted_records,
                    recovery_succeeded,
                )

    @contextmanager
    def transaction(self):
        """Explicit transaction context manager: BEGIN IMMEDIATE, commit on success, rollback on error."""
        retries = DEFAULT_SQLITE_RETRIES
        with self._lock:
            while True:
                try:
                    self.conn.execute("BEGIN IMMEDIATE;")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or retries <= 0:
                        raise
                    retries -= 1
                    time.sleep(SQLITE_RETRY_BACKOFF_WRITE_S)
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @_synchronized
    def write_canonical_batch(self, events: list[CanonicalEvent]):
        """Persist a batch of CanonicalEvents via executemany with conflict preservation."""
        if not events:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            """INSERT INTO canonical_events VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO NOTHING""",
            (
                (
                    e.event_id,
                    e.instrument_id,
                    e.event_type.value,
                    e.exchange_timestamp,
                    e.receive_timestamp,
                    e.processing_timestamp,
                    e.source,
                    e.sequence_number,
                    e.price,
                    e.quantity,
                    e.bid_price,
                    e.bid_size,
                    e.ask_price,
                    e.ask_size,
                    e.quality_status.value,
                    json.dumps(e.reasons) if e.reasons else "[]",
                    e.raw_id,
                )
                for e in events
            ),
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(events):
            self.conflicts += len(events) - inserted

    @_synchronized
    def write_quarantine_batch(self, rows: list[tuple]):
        """Persist a batch of quarantined anomaly records with conflict preservation."""
        if not rows:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            "INSERT INTO quarantine VALUES (?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            rows,
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(rows):
            self.conflicts += len(rows) - inserted
        self._append_quarantine_merkle_batch_in_tx(rows)

    @_synchronized
    def write_lineage_batch(self, rows: list[tuple]):
        """Persist a batch of audit lineage records with conflict preservation."""
        if not rows:
            return
        c_before = self.conn.total_changes
        self.conn.executemany(
            "INSERT INTO lineage VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
            rows,
        )
        inserted = self.conn.total_changes - c_before
        if inserted < len(rows):
            self.conflicts += len(rows) - inserted

    @_synchronized
    def upsert_source_health(self, rows: list[tuple]):
        """Upsert feed health and composite reliability score rows."""
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )

    @_synchronized
    def write_batches_atomic(
        self,
        canonical: list[CanonicalEvent] | None = None,
        quarantine: list[tuple] | None = None,
        lineage: list[tuple] | None = None,
        source_health: list[tuple] | None = None,
        is_recovery: bool = False,
    ) -> None:
        """Atomic multi-batch write in a single BEGIN IMMEDIATE transaction."""
        with self.transaction():
            if canonical:
                c_before = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO canonical_events VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
                        (
                            e.event_id,
                            e.instrument_id,
                            e.event_type.value,
                            e.exchange_timestamp,
                            e.receive_timestamp,
                            e.processing_timestamp,
                            e.source,
                            e.sequence_number,
                            e.price,
                            e.quantity,
                            e.bid_price,
                            e.bid_size,
                            e.ask_price,
                            e.ask_size,
                            e.quality_status.value,
                            json.dumps(e.reasons) if e.reasons else "[]",
                            e.raw_id,
                        )
                        for e in canonical
                    ),
                )
                ins = self.conn.total_changes - c_before
                if ins < len(canonical):
                    if is_recovery:
                        self.recovery_conflicts += len(canonical) - ins
                    else:
                        self.conflicts += len(canonical) - ins
            if quarantine:
                c_before_q = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO quarantine VALUES
                       (?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    quarantine,
                )
                ins_q = self.conn.total_changes - c_before_q
                if ins_q < len(quarantine):
                    if is_recovery:
                        self.recovery_conflicts += len(quarantine) - ins_q
                    else:
                        self.conflicts += len(quarantine) - ins_q
                self._append_quarantine_merkle_batch_in_tx(quarantine)
            if lineage:
                c_before_l = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO lineage VALUES
                       (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    lineage,
                )
                ins_l = self.conn.total_changes - c_before_l
                if ins_l < len(lineage):
                    if is_recovery:
                        self.recovery_conflicts += len(lineage) - ins_l
                    else:
                        self.conflicts += len(lineage) - ins_l
            if source_health:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?,?)",
                    source_health,
                )

    @_synchronized
    def checkpoint(self) -> int:
        """Return highest committed WAL offset from projection_checkpoints, or -1."""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT last_offset FROM projection_checkpoints WHERE name = 'store_canonical';"
            )
            row = cur.fetchone()
            return row[0] if row else -1
        except sqlite3.OperationalError:
            return -1

    @_synchronized
    def apply(self, batch: list[Any], offset: int) -> None:
        """Apply engine batch of decisions/events to store in a single atomic transaction."""
        if not batch and offset < 0:
            return

        from .models import QualityStatus, CanonicalEvent
        from .engine import EngineDecision

        canonical_events: list[CanonicalEvent] = []
        quarantine_rows: list[tuple] = []

        for item in batch:
            if isinstance(item, EngineDecision):
                if (
                    item.canonical_event
                    and item.quality_status != QualityStatus.INVALID
                ):
                    canonical_events.append(item.canonical_event)
                if item.quarantine_row:
                    quarantine_rows.append(item.quarantine_row)
            elif isinstance(item, CanonicalEvent):
                if item.quality_status != QualityStatus.INVALID:
                    canonical_events.append(item)
                else:
                    q_row = (
                        item.event_id,
                        item.instrument_id,
                        item.source,
                        QualityStatus.INVALID.value,
                        json.dumps(item.reasons),
                        json.dumps({"raw_id": item.raw_id}),
                        item.receive_timestamp,
                    )
                    quarantine_rows.append(q_row)
            elif isinstance(item, tuple) and len(item) == 7:
                quarantine_rows.append(item)

        with self.transaction():
            if canonical_events:
                c_before = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO canonical_events VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    (
                        (
                            e.event_id,
                            e.instrument_id,
                            e.event_type.value,
                            e.exchange_timestamp,
                            e.receive_timestamp,
                            e.processing_timestamp,
                            e.source,
                            e.sequence_number,
                            e.price,
                            e.quantity,
                            e.bid_price,
                            e.bid_size,
                            e.ask_price,
                            e.ask_size,
                            e.quality_status.value,
                            json.dumps(e.reasons) if e.reasons else "[]",
                            e.raw_id,
                        )
                        for e in canonical_events
                    ),
                )
                ins = self.conn.total_changes - c_before
                if ins < len(canonical_events):
                    self.conflicts += len(canonical_events) - ins

            if quarantine_rows:
                c_before_q = self.conn.total_changes
                self.conn.executemany(
                    """INSERT INTO quarantine VALUES
                       (?,?,?,?,?,?,?)
                       ON CONFLICT(event_id) DO NOTHING""",
                    quarantine_rows,
                )
                ins_q = self.conn.total_changes - c_before_q
                if ins_q < len(quarantine_rows):
                    self.conflicts += len(quarantine_rows) - ins_q
                self._append_quarantine_merkle_batch_in_tx(quarantine_rows)

            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO projection_checkpoints (name, last_offset, updated_at)
                VALUES ('store_canonical', ?, ?);
                """,
                (offset, time.time()),
            )

    @_synchronized
    def commit(self):
        """Commit active database transaction."""
        self.conn.commit()

    # -- Rowid cursor tailing for streaming sinks (Kafka, downstream consumers) --

    @_read_synchronized
    def get_max_rowid(self, table: str = "canonical_events") -> int:
        """Return the maximum rowid currently stored in a table."""
        # Clean table name to prevent SQL injection
        tbl_clean = (
            "canonical_events"
            if table == "canonical_events"
            else ("quarantine" if table == "quarantine" else "canonical_events")
        )
        cur = self.read_conn.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {tbl_clean}")
        row = cur.fetchone()
        return row[0] if row else 0

    @_read_synchronized
    def query_canonical_after_rowid(
        self, last_rowid: int, limit: int = 100
    ) -> list[tuple]:
        """Fetch canonical events with rowid strictly greater than last_rowid in monotonic order."""
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute(
            """SELECT rowid, event_id, instrument_id, event_type, exchange_timestamp,
                      receive_timestamp, processing_timestamp, source, sequence_number,
                      price, quantity, bid_price, bid_size, ask_price, ask_size,
                      quality_status, reasons, raw_id
               FROM canonical_events
               WHERE rowid > ?
               ORDER BY rowid ASC
               LIMIT ?""",
            (last_rowid, limit),
        )
        return cur.fetchall()

    @_read_synchronized
    def query_quarantine_after_rowid(
        self, last_rowid: int, limit: int = 100
    ) -> list[tuple]:
        """Fetch quarantine records with rowid strictly greater than last_rowid in monotonic order."""
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute(
            """SELECT rowid, event_id, instrument_id, source, quality_status, reasons,
                      payload_json, receive_timestamp
               FROM quarantine
               WHERE rowid > ?
               ORDER BY rowid ASC
               LIMIT ?""",
            (last_rowid, limit),
        )
        return cur.fetchall()

    # -- Query helpers (backs the CLI `query` subcommand / future API) --

    @_read_synchronized
    def latest(self, instrument_id: str, limit: int = 1):
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute(
            """SELECT * FROM canonical_events WHERE instrument_id=?
               ORDER BY exchange_timestamp DESC LIMIT ?""",
            (instrument_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_events(
        self, instrument_id: str | None = None, limit: int = 1000
    ) -> list[dict]:
        limit = min(max(1, limit), 10000)
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM canonical_events WHERE instrument_id=? ORDER BY exchange_timestamp DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM canonical_events ORDER BY exchange_timestamp DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def get_event(self, event_id: str) -> dict | None:
        """Fetch a single canonical event by its unique event_id."""
        cur = self.read_conn.execute(
            "SELECT * FROM canonical_events WHERE event_id=?", (event_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    @_read_synchronized
    def event_lineage(self, event_id: str) -> dict | None:
        cur = self.read_conn.execute(
            "SELECT * FROM lineage WHERE event_id=?", (event_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    @_read_synchronized
    def get_quarantined_record(self, event_id: str) -> dict | None:
        """Fetch a single quarantine record by event_id."""
        cur = self.read_conn.execute(
            "SELECT * FROM quarantine WHERE event_id=?", (event_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def reprocess_quarantine(self, event_id: str, pipeline: Any) -> Any | None:
        """Reprocess a quarantined event through the pipeline."""
        rec = self.get_quarantined_record(event_id)
        if not rec:
            return None
        import json
        from .models import RawEvent

        payload_str = rec.get("payload_json", "{}")
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Cannot reprocess quarantine event {event_id}: stored payload is invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"Cannot reprocess quarantine event {event_id}: stored payload is not an object"
            )
        raw = RawEvent(
            source=rec.get("source", "UNKNOWN"),
            payload=payload,
            receive_timestamp=float(rec.get("receive_timestamp", 0.0)),
            raw_id=rec.get("event_id", ""),
        )
        return pipeline.process_one(raw)

    @_read_synchronized
    def feed_health(self):
        cur = self.read_conn.execute("SELECT * FROM source_health")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def quarantine_sample(self, limit: int = 20):
        limit = min(max(1, limit), 10000)
        cur = self.read_conn.execute("SELECT * FROM quarantine LIMIT ?", (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_quarantine(self, limit: int = 50):
        return self.quarantine_sample(limit)

    @_read_synchronized
    def counts(self):
        cur = self.read_conn.execute(
            "SELECT quality_status, COUNT(*) FROM canonical_events GROUP BY quality_status"
        )
        return dict(cur.fetchall())

    @_synchronized
    def close(self):
        for cb in getattr(self, "_on_close", []):
            try:
                cb()
            except Exception:
                pass
        self._on_close = []
        if (
            hasattr(self, "read_conn")
            and self.read_conn
            and self.read_conn != self.conn
        ):
            try:
                self.read_conn.close()
            except Exception as exc:
                logger.debug("Error closing read_conn: %s", exc)
            self.read_conn = None
        if hasattr(self, "conn") and self.conn:
            self.conn.commit()
            self.conn.close()
            self.conn = None

    # -- Data lifecycle: retention & compaction --
    # ponytail: WORM tables (audit_log, lineage) are never touched — spec A3.

    @_synchronized
    def retention_compact(
        self, retain_days: int = 30, quarantine_days: int = 90
    ) -> dict:
        """Delete canonical_events older than retain_days in chunks, then reclaim disk.

        quarantine records are kept for quarantine_days (default 90) for
        evidentiary/audit compliance, while operational events prune at retain_days.
        Does NOT touch audit_log or lineage (append-only / WORM by design, spec A3).
        Returns dict with counts of deleted rows and compaction status.
        """
        cutoff = time.time() - (retain_days * 86400)
        q_cutoff = time.time() - (quarantine_days * 86400)
        deleted_canonical = 0
        deleted_quarantine = 0

        while True:
            cur = self.conn.execute(
                "DELETE FROM canonical_events WHERE rowid IN ("
                "SELECT rowid FROM canonical_events WHERE COALESCE(processing_timestamp, exchange_timestamp) < ? LIMIT 5000"
                ")",
                (cutoff,),
            )
            deleted_canonical += cur.rowcount
            self.conn.commit()
            if cur.rowcount < 5000:
                break

        while True:
            cur_q = self.conn.execute(
                "DELETE FROM quarantine WHERE rowid IN ("
                "SELECT rowid FROM quarantine WHERE receive_timestamp < ? LIMIT 5000"
                ")",
                (q_cutoff,),
            )
            deleted_quarantine += cur_q.rowcount
            self.conn.commit()
            if cur_q.rowcount < 5000:
                break

        try:
            self.conn.execute("DELETE FROM vwap_curves WHERE timestamp < ?", (cutoff,))
            self.conn.execute(
                "DELETE FROM watchdog_alerts WHERE timestamp < ?", (cutoff,)
            )
            self.conn.commit()
        except Exception as exc:
            logger.debug("Optional table retention pruning skipped: %s", exc)

        # Reclaim disk: checkpoint WAL then truncate
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except sqlite3.OperationalError:
            pass  # :memory: or non-WAL mode
        except Exception as exc:
            logger.debug("WAL checkpoint truncate skipped: %s", exc)
        return {
            "deleted_canonical": deleted_canonical,
            "deleted_quarantine": deleted_quarantine,
            "cutoff_ts": cutoff,
            "quarantine_cutoff_ts": q_cutoff,
        }

    @_synchronized
    def vacuum(self):
        """Full VACUUM to reclaim disk space after large deletions."""
        self.conn.execute("VACUUM;")
        return True

    # -- V3 Analytical write methods --

    @_synchronized
    def write_ohlcv_batch(self, candles: list[dict]) -> None:
        if not candles:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO ohlcv_candles VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    c["instrument_id"],
                    c["bucket_start"],
                    c["interval_s"],
                    c["open"],
                    c["high"],
                    c["low"],
                    c["close"],
                    c["volume"],
                    c["event_count"],
                )
                for c in candles
            ],
        )

    @_synchronized
    def write_spread_batch(self, spreads: list[dict]) -> None:
        if not spreads:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO spread_stats VALUES (?,?,?,?,?,?,?)",
            [
                (
                    s["instrument_id"],
                    s["quote_count"],
                    s["mean_spread"],
                    s["min_spread"],
                    s["max_spread"],
                    s["crossed_count"],
                    s["crossed_pct"],
                )
                for s in spreads
            ],
        )

    @_synchronized
    def write_volatility_batch(self, stats: list[dict]) -> None:
        if not stats:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO volatility_stats VALUES (?,?,?,?,?,?,?)",
            [
                (
                    v["instrument_id"],
                    v["trade_count"],
                    v["mean_price"],
                    v["std_dev"],
                    v["min_price"],
                    v["max_price"],
                    v["price_range_pct"],
                )
                for v in stats
            ],
        )

    # -- V3 Analytical query methods --

    @_read_synchronized
    def query_ohlcv(self, instrument_id: str = None, limit: int = 50) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM ohlcv_candles WHERE instrument_id=? ORDER BY bucket_start DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM ohlcv_candles ORDER BY instrument_id, bucket_start DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_spread(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM spread_stats WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute("SELECT * FROM spread_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_read_synchronized
    def query_volatility(self, instrument_id: str = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM volatility_stats WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute("SELECT * FROM volatility_stats")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Phase 7 Watchdog alert methods --

    @_read_synchronized
    def query_alerts(self, limit: int = 20) -> list[dict]:
        cur = self.read_conn.execute(
            "SELECT source, alert_type, timestamp, details, action_taken FROM watchdog_alerts ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Consolidated BBO methods --

    @_synchronized
    def write_bbo_batch(self, bbos: list) -> None:
        if not bbos:
            return
        now = time.time()
        rows = []
        for b in bbos:
            if hasattr(b, "instrument_id"):
                rows.append(
                    (
                        b.instrument_id,
                        b.best_bid,
                        b.best_bid_size,
                        b.best_bid_source,
                        b.best_ask,
                        b.best_ask_size,
                        b.best_ask_source,
                        b.spread,
                        b.mid_price,
                        1 if b.is_crossed else 0,
                        1 if b.is_locked else 0,
                        b.timestamp,
                        now,
                    )
                )
            elif isinstance(b, dict):
                rows.append(
                    (
                        b["instrument_id"],
                        b["best_bid"],
                        b["best_bid_size"],
                        b["best_bid_source"],
                        b["best_ask"],
                        b["best_ask_size"],
                        b["best_ask_source"],
                        b["spread"],
                        b["mid_price"],
                        1 if b.get("is_crossed") else 0,
                        1 if b.get("is_locked") else 0,
                        b["timestamp"],
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT OR REPLACE INTO consolidated_bbo VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_bbo(self, instrument_id: str | None = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_bbo WHERE instrument_id=?", (instrument_id,)
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_bbo ORDER BY instrument_id ASC"
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Consolidated Level-2 Market Depth methods --

    @_synchronized
    def write_depth_batch(self, ladders: list) -> None:
        if not ladders:
            return
        now = time.time()
        rows = []
        for lad in ladders:
            if hasattr(lad, "instrument_id"):
                bids_json = json.dumps([b.to_dict() for b in lad.bids])
                asks_json = json.dumps([a.to_dict() for a in lad.asks])
                rows.append(
                    (
                        lad.instrument_id,
                        bids_json,
                        asks_json,
                        lad.micro_price,
                        lad.imbalance_ratio,
                        1 if lad.is_crossed else 0,
                        lad.timestamp,
                        now,
                    )
                )
            elif isinstance(lad, dict):
                rows.append(
                    (
                        lad["instrument_id"],
                        json.dumps(lad.get("bids", [])),
                        json.dumps(lad.get("asks", [])),
                        lad.get("micro_price", 0.0),
                        lad.get("imbalance_ratio", 0.0),
                        1 if lad.get("is_crossed") else 0,
                        lad.get("timestamp", now),
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT OR REPLACE INTO consolidated_depth VALUES
               (?,?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_depth(self, instrument_id: str | None = None) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_depth WHERE instrument_id=?",
                (instrument_id,),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM consolidated_depth ORDER BY instrument_id ASC"
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    # -- Real-Time VWAP Slicing & Liquidity Depth methods --

    @_synchronized
    def write_vwap_batch(self, curves: list) -> None:
        if not curves:
            return
        now = time.time()
        rows = []
        for c in curves:
            if hasattr(c, "instrument_id"):
                rows.append(
                    (
                        c.instrument_id,
                        c.timestamp,
                        c.mid_price,
                        c.best_bid,
                        c.best_ask,
                        json.dumps(c.to_dict()),
                        now,
                    )
                )
            elif isinstance(c, dict):
                rows.append(
                    (
                        c["instrument_id"],
                        c.get("timestamp", now),
                        c.get("mid_price", 0.0),
                        c.get("best_bid", 0.0),
                        c.get("best_ask", 0.0),
                        json.dumps(c),
                        now,
                    )
                )
        self.conn.executemany(
            """INSERT INTO vwap_curves (instrument_id, timestamp, mid_price, best_bid, best_ask, curve_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            rows,
        )

    @_read_synchronized
    def query_vwap_curves(
        self, instrument_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        if instrument_id:
            cur = self.read_conn.execute(
                "SELECT * FROM vwap_curves WHERE instrument_id=? ORDER BY timestamp DESC LIMIT ?",
                (instrument_id, limit),
            )
        else:
            cur = self.read_conn.execute(
                "SELECT * FROM vwap_curves ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    @_synchronized
    def get_schema_version(self) -> int:
        """Return current database schema version from PRAGMA user_version."""
        row = self.conn.execute("PRAGMA user_version;").fetchone()
        return row[0] if row else 0

    @_synchronized
    def get_applied_migrations(self) -> list[dict]:
        """Return list of applied schema migrations."""
        cur = self.conn.execute(
            "SELECT version, applied_at, description FROM schema_migrations ORDER BY version ASC"
        )
        return [
            {"version": r[0], "applied_at": r[1], "description": r[2]}
            for r in cur.fetchall()
        ]

    @_synchronized
    def save_api_key(self, ent: Any) -> None:
        """Save or update a client API key (stores token_hash, never raw token)."""
        role_val = getattr(ent, "role", "VIEWER")
        role_str = role_val.value if hasattr(role_val, "value") else str(role_val)

        raw_token = getattr(ent, "token", "")
        token_hash = getattr(ent, "token_hash", "")
        if not token_hash and raw_token:
            from .security import hash_api_key

            token_hash = hash_api_key(raw_token)
            ent.token_hash = token_hash
        if not token_hash:
            raise ValueError("Cannot persist API key without token or token_hash")

        key_prefix = getattr(ent, "key_prefix", "")
        if not key_prefix and raw_token:
            key_prefix = raw_token[:12] + "..." if len(raw_token) > 12 else raw_token
            ent.key_prefix = key_prefix
        elif not key_prefix:
            key_prefix = token_hash[:12] + "..."
            ent.key_prefix = key_prefix

        cur_cols = {
            c[1] for c in self.conn.execute("PRAGMA table_info(api_keys)").fetchall()
        }
        key_id = getattr(ent, "key_id", "") or (token_hash[:16] if token_hash else "")
        allowed_cidrs_str = json.dumps(list(getattr(ent, "allowed_cidrs", []) or []))
        allowed_sources_str = json.dumps(
            list(getattr(ent, "allowed_sources", []) or [])
        )
        allowed_symbols_str = json.dumps(
            list(getattr(ent, "allowed_symbols", []) or [])
        )

        if "allowed_symbols" in cur_cols:
            self.conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (token_hash, key_prefix, key_id, client_id, role, is_active, created_at, expires_at, allowed_cidrs, allowed_sources, allowed_symbols)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_hash,
                    key_prefix,
                    key_id,
                    ent.client_id,
                    role_str,
                    1 if ent.is_active else 0,
                    float(ent.created_at),
                    float(ent.expires_at) if ent.expires_at is not None else None,
                    allowed_cidrs_str,
                    allowed_sources_str,
                    allowed_symbols_str,
                ),
            )
        elif "rate_limit_eps" in cur_cols:
            self.conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (token_hash, key_prefix, client_id, role, rate_limit_eps, tier, can_access_l2, can_use_binary, can_use_shm, max_replay_events, is_active, created_at, expires_at)
                   VALUES (?, ?, ?, ?, 20000.0, 'STANDARD', 1, 1, 1, 100000, ?, ?, ?)""",
                (
                    token_hash,
                    key_prefix,
                    ent.client_id,
                    role_str,
                    1 if ent.is_active else 0,
                    float(ent.created_at),
                    float(ent.expires_at) if ent.expires_at is not None else None,
                ),
            )
        else:
            self.conn.execute(
                """INSERT OR REPLACE INTO api_keys
                   (token_hash, key_prefix, client_id, role, is_active, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    token_hash,
                    key_prefix,
                    ent.client_id,
                    role_str,
                    1 if ent.is_active else 0,
                    float(ent.created_at),
                    float(ent.expires_at) if ent.expires_at is not None else None,
                ),
            )
        self.conn.commit()

    @_synchronized
    def load_api_keys(self) -> list:
        """Load all registered API keys from the store."""
        from .security import ClientEntitlement, Role

        cur_cols = {
            c[1] for c in self.conn.execute("PRAGMA table_info(api_keys)").fetchall()
        }
        if "allowed_symbols" in cur_cols:
            cur = self.conn.execute(
                """SELECT token_hash, key_prefix, client_id, role, is_active, created_at, expires_at,
                          COALESCE(key_id, ''), COALESCE(allowed_cidrs, '[]'), COALESCE(allowed_sources, '[]'), COALESCE(allowed_symbols, '[]')
                   FROM api_keys"""
            )
            results = []
            for row in cur.fetchall():
                role_raw = row[3]
                role = Role[role_raw] if role_raw in Role.__members__ else Role.VIEWER
                try:
                    cidrs = json.loads(row[8]) if row[8] else []
                except Exception:
                    cidrs = []
                try:
                    sources = json.loads(row[9]) if row[9] else []
                except Exception:
                    sources = []
                try:
                    symbols = json.loads(row[10]) if row[10] else []
                except Exception:
                    symbols = []
                results.append(
                    ClientEntitlement(
                        token_hash=row[0],
                        key_prefix=row[1],
                        token=row[0],
                        client_id=row[2],
                        role=role,
                        is_active=bool(row[4]),
                        created_at=float(row[5]),
                        expires_at=float(row[6]) if row[6] is not None else None,
                        key_id=row[7] or (row[0][:16] if row[0] else ""),
                        allowed_cidrs=list(cidrs) if isinstance(cidrs, list) else [],
                        allowed_sources=list(sources)
                        if isinstance(sources, list)
                        else [],
                        allowed_symbols=list(symbols)
                        if isinstance(symbols, list)
                        else [],
                    )
                )
            return results
        else:
            cur = self.conn.execute(
                """SELECT token_hash, key_prefix, client_id, role, is_active, created_at, expires_at
                   FROM api_keys"""
            )
            results = []
            for row in cur.fetchall():
                role_raw = row[3]
                role = Role[role_raw] if role_raw in Role.__members__ else Role.VIEWER
                results.append(
                    ClientEntitlement(
                        token_hash=row[0],
                        key_prefix=row[1],
                        token=row[0],
                        client_id=row[2],
                        role=role,
                        is_active=bool(row[4]),
                        created_at=float(row[5]),
                        expires_at=float(row[6]) if row[6] is not None else None,
                        key_id=row[0][:16] if row[0] else "",
                    )
                )
            return results

    @_synchronized
    def revoke_api_key(self, token_or_hash: str) -> bool:
        """Revoke by token or hash; prefixes must be resolved unambiguously in SecurityManager."""
        salt = os.environ.get("MDRAP_API_KEY_SALT", "").strip()
        salted_hash = (
            hmac.new(
                salt.encode("utf-8"), token_or_hash.encode("utf-8"), hashlib.sha256
            ).hexdigest()
            if salt
            else ""
        )
        raw_sha256 = hashlib.sha256(token_or_hash.encode("utf-8")).hexdigest()
        prefix_rows = self.conn.execute(
            "SELECT token_hash FROM api_keys WHERE key_prefix = ? AND is_active = 1",
            (token_or_hash,),
        ).fetchall()
        if len(prefix_rows) > 1:
            raise ValueError("API key prefix is ambiguous; use a token or token hash")
        exact_hash = prefix_rows[0][0] if prefix_rows else token_or_hash
        cur = self.conn.execute(
            """UPDATE api_keys SET is_active = 0 
               WHERE token_hash = ? 
                  OR token_hash = ? 
                  OR token_hash = ?""",
            (salted_hash, raw_sha256, exact_hash),
        )
        self.conn.commit()
        return cur.rowcount > 0
