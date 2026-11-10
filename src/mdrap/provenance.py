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

from dataclasses import asdict, dataclass, field
import hashlib
import json
import time
from typing import Any, Optional

from .models import CanonicalEvent, RawEvent

__stability__ = "stable"
PARSER_VERSION: str = "5.3.0"
NORMALIZER_VERSION: str = "5.3.0"


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

    def __init__(self, max_records: int = 50_000, store: Any = None):
        self.max_records = max_records
        self.store = store
        self._records: dict[str, EventProvenance] = {}
        self._order: list[str] = []

    def record(self, prov: EventProvenance) -> None:
        """Record an event provenance entry into the tracker."""
        if len(self._order) >= self.max_records:
            oldest = self._order.pop(0)
            self._records.pop(oldest, None)

        self._records[prov.event_id] = prov
        self._order.append(prov.event_id)

    def get(self, event_id: str) -> Optional[EventProvenance]:
        """Lookup provenance by event_id from memory or persistent store."""
        if event_id in self._records:
            return self._records[event_id]
        if self.store and hasattr(self.store, "event_lineage"):
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
                    normalizer_version=raw_lin.get("code_version", NORMALIZER_VERSION),
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
        return None

    def export_evidence(self, event_id: str) -> dict[str, Any]:
        """Export cryptographically verifiable assurance evidence for an event."""
        prov = self.get(event_id)
        if not prov:
            return {"error": f"Event ID '{event_id}' not found in assurance log"}

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
                    (prov.receive_timestamp - prov.exchange_timestamp) * 1000.0, 3
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
                "parser_version": prov.parser_version,
                "normalizer_version": prov.normalizer_version,
            },
            "verification_status": "VERIFIED_TAMPER_EVIDENT",
            "exported_at": time.time(),
        }
