"""MDRAP Event Provenance & Forensic Lineage Layer.

Builds the core trust layer for the Market Data Reliability & Assurance Platform.
Every ingested market event is stamped with complete cryptographic provenance:
  - Stable monotonic event ID
  - Provider, instrument, channel, and explicit data mode (LIVE, REPLAY, SIMULATION)
  - Tri-timestamp vector (exchange_ts, receive_ts, processing_ts)
  - Monotonic sequence numbers where exposed by venue
  - Parser & Normalizer software versions
  - SHA-256 payload integrity hash of the raw wire message
  - Quality classification & explicit anomaly reasons
  - End-to-end lineage hash linking raw frame to canonical state
"""

from __future__ import annotations

import collections
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Optional

from .models import CanonicalEvent, RawEvent

__stability__ = "stable"
PARSER_VERSION: str = "6.0.0"
NORMALIZER_VERSION: str = "6.0.0"

logger = logging.getLogger("mdrap.provenance")


class ProvenanceDurabilityState(str, Enum):
    """Explicit persistence states for event-level forensic assurance evidence."""

    PENDING = "PENDING"
    DURABLE = "DURABLE_DISK"
    FAILED_RECOVERABLE = "FAILED_RECOVERABLE"
    FAILED_UNRECOVERABLE = "FAILED_UNRECOVERABLE"


def compute_payload_hash(wire: str | bytes | dict | None) -> str:
    """Compute deterministic SHA-256 hash of a raw message payload."""
    if wire is None:
        return hashlib.sha256(b"").hexdigest()
    if isinstance(wire, bytes):
        payload_bytes = wire
    elif isinstance(wire, str):
        payload_bytes = wire.encode("utf-8")
    elif isinstance(wire, dict):
        payload_bytes = json.dumps(wire, sort_keys=True).encode("utf-8")
    else:
        payload_bytes = str(wire).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


@dataclass(slots=True)
class EventProvenance:
    """Complete cryptographic provenance record for a single market event."""

    event_id: str
    raw_id: str
    provider: str
    instrument: str
    channel: str
    data_mode: str  # "LIVE", "REPLAY", "SIMULATION"
    exchange_timestamp: float
    receive_timestamp: float
    processing_timestamp: float
    sequence_number: Optional[int]
    parser_version: str
    normalizer_version: str
    quality_status: str
    quality_reasons: list[str]
    payload_sha256: str
    processing_status: str  # "PROCESSED", "QUARANTINED", "DROPPED"
    durability_status: str  # "COMMITTED_MEM", "COMMITTED_WAL", "DURABLE_DISK"
    lineage_hash: str = ""

    def __post_init__(self):
        if not self.lineage_hash:
            # Deterministic hash binding raw frame, normalization metadata, and quality outcome
            manifest = f"{self.event_id}:{self.raw_id}:{self.provider}:{self.instrument}:{self.data_mode}:{self.payload_sha256}:{self.quality_status}:{self.processing_timestamp}"
            object.__setattr__(
                self,
                "lineage_hash",
                hashlib.sha256(manifest.encode("utf-8")).hexdigest(),
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventProvenance:
        return cls(**data)


def create_provenance(
    raw: RawEvent,
    canon: CanonicalEvent,
    channel: str = "ORDERBOOK_L1",
    data_mode: str = "LIVE",
    durability_status: str = "COMMITTED_MEM",
) -> EventProvenance:
    """Create a first-class EventProvenance record linking raw and canonical events."""
    raw_wire = raw.wire if raw.wire else raw.payload
    payload_hash = compute_payload_hash(raw_wire)

    q_status = (
        canon.quality_status.name
        if hasattr(canon.quality_status, "name")
        else str(canon.quality_status)
    )
    processing_status = "QUARANTINED" if q_status == "INVALID" else "PROCESSED"

    return EventProvenance(
        event_id=canon.event_id,
        raw_id=canon.raw_id or raw.raw_id,
        provider=canon.source or raw.source,
        instrument=canon.instrument_id,
        channel=channel,
        data_mode=data_mode.upper(),
        exchange_timestamp=canon.exchange_timestamp,
        receive_timestamp=canon.receive_timestamp,
        processing_timestamp=canon.processing_timestamp or time.time(),
        sequence_number=canon.sequence_number,
        parser_version=PARSER_VERSION,
        normalizer_version=NORMALIZER_VERSION,
        quality_status=q_status,
        quality_reasons=list(canon.reasons or []),
        payload_sha256=payload_hash,
        processing_status=processing_status,
        durability_status=durability_status,
    )


class ProvenanceTracker:
    """In-memory and persistent tracker for event-level assurance evidence."""

    def __init__(
        self,
        max_records: int = 50_000,
        store: Any = None,
        db_path: Optional[str] = None,
    ):
        self.max_records = max_records
        self.store = store
        self.db_path = db_path or (
            getattr(store, "path", None) or getattr(store, "db_path", None)
            if store
            else None
        )
        self._lock = threading.RLock()
        self._records: dict[str, EventProvenance] = {}
        self._order: collections.deque[str] = collections.deque()
        self._pending_retry: collections.deque[EventProvenance] = collections.deque(
            maxlen=10_000
        )
        self._db_conn: Optional[sqlite3.Connection] = None

        # Operational failure & lifecycle counters (Spec §5 & §26, Agent 1)
        self.recorded_count: int = 0
        self.persisted_count: int = 0
        self.failed_recoverable_count: int = 0
        self.failed_unrecoverable_count: int = 0
        self.recovered_count: int = 0
        self.dropped_provenance_count: int = 0

        if self.db_path and self.db_path != ":memory:":
            self._init_db()

    def _init_db(self) -> None:
        if not self.db_path or self.db_path == ":memory:":
            return
        try:
            self._db_conn = sqlite3.connect(
                self.db_path, timeout=5.0, check_same_thread=False
            )
            self._db_conn.execute("PRAGMA journal_mode=WAL;")
            self._db_conn.execute("PRAGMA busy_timeout=5000;")
            with self._db_conn:
                self._db_conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_provenance (
                        event_id TEXT PRIMARY KEY,
                        raw_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        instrument TEXT NOT NULL,
                        channel TEXT NOT NULL,
                        data_mode TEXT NOT NULL,
                        exchange_timestamp REAL NOT NULL,
                        receive_timestamp REAL NOT NULL,
                        processing_timestamp REAL NOT NULL,
                        sequence_number INTEGER,
                        parser_version TEXT NOT NULL,
                        normalizer_version TEXT NOT NULL,
                        quality_status TEXT NOT NULL,
                        quality_reasons TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        processing_status TEXT NOT NULL,
                        durability_status TEXT NOT NULL,
                        lineage_hash TEXT NOT NULL
                    )
                    """
                )
                self._db_conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_provenance_inst_ts ON event_provenance(instrument, receive_timestamp)"
                )
                self._db_conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_provenance_prov ON event_provenance(provider)"
                )
        except Exception as exc:
            logger.error(
                "[provenance] Failed to initialize SQLite schema at %s: %s",
                self.db_path,
                exc,
            )

    def record(self, prov: EventProvenance) -> None:
        """Record an event provenance entry into the tracker and persistent store."""
        with self._lock:
            self.recorded_count += 1
            if len(self._order) >= self.max_records:
                oldest = self._order.popleft()
                self._records.pop(oldest, None)

            self._records[prov.event_id] = prov
            self._order.append(prov.event_id)

            # Persist to store if available
            persisted = False
            if self.store and hasattr(self.store, "write_provenance_batch"):
                try:
                    self.store.write_provenance_batch([prov])
                    persisted = True
                except Exception as exc:
                    self._handle_persistence_failure(prov, exc)
            elif self.db_path and self.db_path != ":memory:":
                try:
                    conn = self._db_conn
                    if not conn:
                        self._db_conn = sqlite3.connect(
                            self.db_path, timeout=5.0, check_same_thread=False
                        )
                        conn = self._db_conn
                    with conn:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO event_provenance (
                                event_id, raw_id, provider, instrument, channel, data_mode,
                                exchange_timestamp, receive_timestamp, processing_timestamp,
                                sequence_number, parser_version, normalizer_version,
                                quality_status, quality_reasons, payload_sha256,
                                processing_status, durability_status, lineage_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                prov.event_id,
                                prov.raw_id,
                                prov.provider,
                                prov.instrument,
                                prov.channel,
                                prov.data_mode,
                                prov.exchange_timestamp,
                                prov.receive_timestamp,
                                prov.processing_timestamp,
                                prov.sequence_number,
                                prov.parser_version,
                                prov.normalizer_version,
                                prov.quality_status,
                                json.dumps(prov.quality_reasons),
                                prov.payload_sha256,
                                prov.processing_status,
                                prov.durability_status,
                                prov.lineage_hash,
                            ),
                        )
                    persisted = True
                except Exception as exc:
                    self._handle_persistence_failure(prov, exc)
            else:
                # In-memory only store
                persisted = True
                prov.durability_status = "COMMITTED_MEM"

            if persisted:
                self.persisted_count += 1
                is_disk = bool(
                    (self.db_path and self.db_path != ":memory:")
                    or (
                        self.store
                        and getattr(self.store, "path", None)
                        and getattr(self.store, "path") != ":memory:"
                    )
                )
                if is_disk:
                    prov.durability_status = (
                        ProvenanceDurabilityState.DURABLE.value
                    )
                elif prov.durability_status not in (
                    "COMMITTED_MEM",
                    "COMMITTED_WAL",
                ):
                    prov.durability_status = "COMMITTED_MEM"

    def _handle_persistence_failure(
        self, prov: EventProvenance, exc: Exception
    ) -> None:
        """Classify and record provenance persistence failure."""
        err_msg = str(exc).lower()
        is_recoverable = isinstance(
            exc, (sqlite3.OperationalError, TimeoutError, BlockingIOError)
        ) and any(
            k in err_msg
            for k in [
                "locked",
                "busy",
                "timeout",
                "resource temporarily unavailable",
            ]
        )
        if is_recoverable:
            self.failed_recoverable_count += 1
            prov.durability_status = (
                ProvenanceDurabilityState.FAILED_RECOVERABLE.value
            )
            if len(self._pending_retry) >= self._pending_retry.maxlen:
                self.dropped_provenance_count += 1
            self._pending_retry.append(prov)
            logger.warning(
                "[provenance] Transient storage lock for event '%s': %s. Queued in dead-letter retry buffer (pending=%d)",
                prov.event_id,
                exc,
                len(self._pending_retry),
            )
        else:
            self.failed_unrecoverable_count += 1
            self.dropped_provenance_count += 1
            prov.durability_status = (
                ProvenanceDurabilityState.FAILED_UNRECOVERABLE.value
            )
            logger.error(
                "[provenance] Unrecoverable persistence failure for event '%s': %s. Lineage marked unpersisted.",
                prov.event_id,
                exc,
            )

    def flush_pending_retries(self) -> int:
        """Attempt to flush pending dead-letter retry items to persistent storage."""
        with self._lock:
            if not self._pending_retry:
                return 0
            items_to_retry = list(self._pending_retry)
            recovered = 0
            remaining: collections.deque[EventProvenance] = collections.deque(
                maxlen=10_000
            )

            for prov in items_to_retry:
                persisted = False
                if self.store and hasattr(self.store, "write_provenance_batch"):
                    try:
                        self.store.write_provenance_batch([prov])
                        persisted = True
                    except Exception:
                        persisted = False
                elif self.db_path and self.db_path != ":memory:":
                    try:
                        conn = self._db_conn
                        if not conn:
                            self._db_conn = sqlite3.connect(
                                self.db_path, timeout=5.0, check_same_thread=False
                            )
                            conn = self._db_conn
                        with conn:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO event_provenance (
                                    event_id, raw_id, provider, instrument, channel, data_mode,
                                    exchange_timestamp, receive_timestamp, processing_timestamp,
                                    sequence_number, parser_version, normalizer_version,
                                    quality_status, quality_reasons, payload_sha256,
                                    processing_status, durability_status, lineage_hash
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    prov.event_id,
                                    prov.raw_id,
                                    prov.provider,
                                    prov.instrument,
                                    prov.channel,
                                    prov.data_mode,
                                    prov.exchange_timestamp,
                                    prov.receive_timestamp,
                                    prov.processing_timestamp,
                                    prov.sequence_number,
                                    prov.parser_version,
                                    prov.normalizer_version,
                                    prov.quality_status,
                                    json.dumps(prov.quality_reasons),
                                    prov.payload_sha256,
                                    prov.processing_status,
                                    ProvenanceDurabilityState.DURABLE.value,
                                    prov.lineage_hash,
                                ),
                            )
                        persisted = True
                    except Exception:
                        persisted = False

                if persisted:
                    prov.durability_status = (
                        ProvenanceDurabilityState.DURABLE.value
                    )
                    recovered += 1
                    self.recovered_count += 1
                else:
                    remaining.append(prov)

            self._pending_retry = remaining
            if recovered > 0:
                logger.info(
                    "[provenance] Successfully flushed %d pending provenance records to durable storage (remaining=%d)",
                    recovered,
                    len(self._pending_retry),
                )
            return recovered

    def close(self) -> None:
        """Close tracker, flush pending retries, and release database resources."""
        with self._lock:
            self.flush_pending_retries()
            if self._db_conn:
                try:
                    self._db_conn.commit()
                except Exception:
                    pass
                try:
                    self._db_conn.close()
                except Exception:
                    pass
                self._db_conn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def get(self, event_id: str) -> Optional[EventProvenance]:
        """Lookup provenance by event_id from memory or persistent store."""
        if event_id in self._records:
            return self._records[event_id]

        # 1. Query store if available
        if self.store and hasattr(self.store, "get_provenance"):
            try:
                r = self.store.get_provenance(event_id)
                if r:
                    reasons = (
                        r["quality_reasons"]
                        if isinstance(r["quality_reasons"], list)
                        else json.loads(r["quality_reasons"] or "[]")
                    )
                    prov = EventProvenance(
                        event_id=r["event_id"],
                        raw_id=r["raw_id"],
                        provider=r["provider"],
                        instrument=r["instrument"],
                        channel=r["channel"],
                        data_mode=r["data_mode"],
                        exchange_timestamp=r["exchange_timestamp"],
                        receive_timestamp=r["receive_timestamp"],
                        processing_timestamp=r["processing_timestamp"],
                        sequence_number=r["sequence_number"],
                        parser_version=r["parser_version"],
                        normalizer_version=r["normalizer_version"],
                        quality_status=r["quality_status"],
                        quality_reasons=reasons,
                        payload_sha256=r["payload_sha256"],
                        processing_status=r["processing_status"],
                        durability_status=r["durability_status"],
                        lineage_hash=r["lineage_hash"],
                    )
                    self._records[event_id] = prov
                    return prov
            except Exception as exc:
                logger.debug(
                    "[provenance] Store lookup for '%s' failed: %s",
                    event_id,
                    exc,
                )

        # 2. Query SQLite directly if db_path configured
        if self.db_path and self.db_path != ":memory:":
            conn = None
            try:
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                r = conn.execute(
                    "SELECT * FROM event_provenance WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if r:
                    reasons = json.loads(r["quality_reasons"] or "[]")
                    prov = EventProvenance(
                        event_id=r["event_id"],
                        raw_id=r["raw_id"],
                        provider=r["provider"],
                        instrument=r["instrument"],
                        channel=r["channel"],
                        data_mode=r["data_mode"],
                        exchange_timestamp=r["exchange_timestamp"],
                        receive_timestamp=r["receive_timestamp"],
                        processing_timestamp=r["processing_timestamp"],
                        sequence_number=r["sequence_number"],
                        parser_version=r["parser_version"],
                        normalizer_version=r["normalizer_version"],
                        quality_status=r["quality_status"],
                        quality_reasons=reasons,
                        payload_sha256=r["payload_sha256"],
                        processing_status=r["processing_status"],
                        durability_status=r["durability_status"],
                        lineage_hash=r["lineage_hash"],
                    )
                    self._records[event_id] = prov
                    return prov
            except Exception as exc:
                logger.debug(
                    "[provenance] Direct SQLite lookup for '%s' failed: %s",
                    event_id,
                    exc,
                )
            finally:
                if conn:
                    conn.close()

        # 3. Fallback to legacy lineage table
        if self.store and hasattr(self.store, "event_lineage"):
            try:
                raw_lin = self.store.event_lineage(event_id)
                if raw_lin:
                    # Reconstruct EventProvenance from persistent lineage table
                    return EventProvenance(
                        event_id=raw_lin.get("event_id", event_id),
                        raw_id=raw_lin.get("raw_id", ""),
                        provider=raw_lin.get("chosen_source", ""),
                        instrument=raw_lin.get("instrument_id", ""),
                        channel="UNKNOWN",
                        data_mode="HISTORICAL",
                        exchange_timestamp=0.0,
                        receive_timestamp=raw_lin.get("created_at", 0.0),
                        processing_timestamp=raw_lin.get("created_at", 0.0),
                        sequence_number=None,
                        parser_version=raw_lin.get("code_version", PARSER_VERSION),
                        normalizer_version=raw_lin.get(
                            "code_version", NORMALIZER_VERSION
                        ),
                        quality_status="VALID"
                        if not raw_lin.get("conflict")
                        else "SUSPICIOUS",
                        quality_reasons=[raw_lin.get("decision_reason", "")]
                        if raw_lin.get("decision_reason")
                        else [],
                        payload_sha256="",
                        processing_status="PROCESSED",
                        durability_status="DURABLE_DISK",
                    )
            except Exception as exc:
                logger.debug(
                    "[provenance] Lineage fallback lookup for '%s' failed: %s",
                    event_id,
                    exc,
                )
        return None

    def export_evidence(self, event_id: str) -> dict[str, Any]:
        """Export cryptographically verifiable assurance evidence for an event."""
        prov = self.get(event_id)
        if not prov:
            return {"error": f"Event ID '{event_id}' not found in assurance log"}

        verif_status = "VERIFIED_TAMPER_EVIDENT"
        if prov.durability_status in (
            ProvenanceDurabilityState.FAILED_RECOVERABLE.value,
            ProvenanceDurabilityState.FAILED_UNRECOVERABLE.value,
            ProvenanceDurabilityState.PENDING.value,
        ):
            verif_status = f"DEGRADED_{prov.durability_status}"

        return {
            "version": "1.0",
            "evidence_type": "MARKET_DATA_ASSURANCE_PROVENANCE",
            "event_id": prov.event_id,
            "provider": prov.provider,
            "instrument": prov.instrument,
            "channel": prov.channel,
            "data_mode": prov.data_mode,
            "timestamps": {
                "exchange_ts": prov.exchange_timestamp,
                "receive_ts": prov.receive_timestamp,
                "processing_ts": prov.processing_timestamp,
                "clock_drift_ms": round(
                    (prov.receive_timestamp - prov.exchange_timestamp) * 1000.0,
                    3,
                )
                if prov.exchange_timestamp
                else None,
            },
            "sequence_number": prov.sequence_number,
            "quality": {
                "status": prov.quality_status,
                "reasons": prov.quality_reasons,
                "processing_status": prov.processing_status,
            },
            "integrity": {
                "payload_sha256": prov.payload_sha256,
                "lineage_hash": prov.lineage_hash,
                "durability_status": prov.durability_status,
                "persistence_state": prov.durability_status,
                "parser_version": prov.parser_version,
                "normalizer_version": prov.normalizer_version,
            },
            "verification_status": verif_status,
            "exported_at": time.time(),
        }


def compute_lineage_hash(
    event_id: str,
    raw_id: str,
    provider: str,
    instrument: str,
    data_mode: str,
    payload_sha256: str,
    quality_status: str,
    processing_timestamp: float,
) -> str:
    """Compute deterministic lineage hash binding raw frame and canonical outcome."""
    manifest = f"{event_id}:{raw_id}:{provider}:{instrument}:{data_mode}:{payload_sha256}:{quality_status}:{processing_timestamp}"
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def verify_evidence_package(evidence: dict[str, Any]) -> tuple[bool, list[str]]:
    """Perform rigorous cryptographic verification of an exported evidence manifest."""
    import hmac

    errors: list[str] = []

    # 1. Schema check
    req_fields = [
        "evidence_type",
        "event_id",
        "provider",
        "instrument",
        "timestamps",
        "quality",
        "integrity",
    ]
    for f in req_fields:
        if f not in evidence:
            errors.append(f"Missing required manifest field: '{f}'")
    if errors:
        return False, errors

    event_id = evidence["event_id"]
    provider = evidence["provider"]
    instrument = evidence["instrument"]
    data_mode = evidence.get("data_mode", "LIVE")
    processing_ts = evidence["timestamps"].get("processing_ts", 0.0)
    quality_status = evidence["quality"].get("status", "VALID")
    claimed_payload_sha256 = evidence["integrity"].get("payload_sha256", "")
    claimed_lineage_hash = evidence["integrity"].get("lineage_hash", "")
    raw_id = evidence["integrity"].get("raw_id", "") or evidence.get("raw_id", "")

    # 2. Wire payload check if raw wire is provided
    raw_wire = evidence.get("raw_wire")
    if raw_wire is not None:
        recalc_wire_hash = compute_payload_hash(raw_wire)
        if not hmac.compare_digest(recalc_wire_hash, claimed_payload_sha256):
            errors.append(
                f"PAYLOAD TAMPER DETECTED: Computed wire SHA-256 '{recalc_wire_hash}' "
                f"does not match claimed payload digest '{claimed_payload_sha256}'"
            )

    # 3. Lineage hash check
    recalc_lineage = compute_lineage_hash(
        event_id=event_id,
        raw_id=raw_id,
        provider=provider,
        instrument=instrument,
        data_mode=data_mode,
        payload_sha256=claimed_payload_sha256,
        quality_status=quality_status,
        processing_timestamp=processing_ts,
    )
    if not hmac.compare_digest(recalc_lineage, claimed_lineage_hash):
        errors.append(
            f"LINEAGE TAMPER DETECTED: Recomputed lineage digest '{recalc_lineage}' "
            f"does not match sealed manifest hash '{claimed_lineage_hash}'"
        )

    # 4. Merkle proof check if present
    merkle_proof = evidence.get("merkle_proof") or evidence.get("integrity", {}).get(
        "merkle_proof"
    )
    expected_root = evidence.get("merkle_root") or evidence.get(
        "integrity", {}
    ).get("merkle_root")
    if expected_root is not None:
        leaf_input = (
            evidence["integrity"].get("entry_hash") or claimed_lineage_hash
        )
        curr = hashlib.sha256(b"\x00" + leaf_input.encode("utf-8")).hexdigest()
        if merkle_proof:
            for sibling_hex, pos in merkle_proof:
                if pos.upper() == "R":
                    curr = hashlib.sha256(
                        b"\x01" + bytes.fromhex(curr) + bytes.fromhex(sibling_hex)
                    ).hexdigest()
                else:
                    curr = hashlib.sha256(
                        b"\x01" + bytes.fromhex(sibling_hex) + bytes.fromhex(curr)
                    ).hexdigest()
        if not hmac.compare_digest(curr, expected_root):
            errors.append(
                f"MERKLE PROOF REJECTED: Inclusion proof failed to reconstruct root '{expected_root}'"
            )

    return len(errors) == 0, errors

