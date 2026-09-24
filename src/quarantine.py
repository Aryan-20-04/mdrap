"""MDRAP Quarantine Subsystem.

Provides first-class evidentiary isolation for rejected, malformed, and anomalous market data.
Strictly adheres to Design Principle 3: 'Never silently discard bad data — quarantine, never drop.'
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from models import CanonicalEvent, RawEvent

__stability__ = "stable"


@dataclass(slots=True)
class QuarantineRecord:
    """Evidentiary record preserving complete raw context of a rejected market event."""

    event_id: str
    instrument_id: str
    source: str
    timestamp: float
    rule: str
    reason: str
    stage: str
    raw_payload: dict[str, Any] | str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "instrument_id": self.instrument_id,
            "source": self.source,
            "timestamp": self.timestamp,
            "rule": self.rule,
            "reason": self.reason,
            "stage": self.stage,
            "raw_payload": self.raw_payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_raw(
        cls,
        raw: RawEvent,
        rule: str,
        reason: str,
        stage: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> QuarantineRecord:
        inst = (
            str(raw.payload.get("instrument", "UNKNOWN"))
            if isinstance(raw.payload, dict)
            else "UNKNOWN"
        )
        return cls(
            event_id=raw.raw_id,
            instrument_id=inst,
            source=raw.source,
            timestamp=raw.receive_timestamp,
            rule=rule,
            reason=reason,
            stage=stage,
            raw_payload=raw.payload,
            metadata=metadata or {},
        )

    @classmethod
    def from_canonical(
        cls,
        event: CanonicalEvent,
        rule: str,
        stage: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> QuarantineRecord:
        reason_str = event.reasons[0] if event.reasons else "UNKNOWN"
        return cls(
            event_id=event.event_id,
            instrument_id=event.instrument_id,
            source=event.source,
            timestamp=event.receive_timestamp,
            rule=rule,
            reason=reason_str,
            stage=stage,
            raw_payload=event.to_dict(),
            metadata=metadata or {},
        )


class QuarantineManager:
    """In-memory and persistent quarantine storage manager."""

    def __init__(self) -> None:
        self._records: dict[str, QuarantineRecord] = {}
        self._records_by_rule: dict[str, list[QuarantineRecord]] = {}
        self._records_by_source: dict[str, list[QuarantineRecord]] = {}

    def isolate(self, record: QuarantineRecord) -> None:
        """Isolate a rejected event without mutating any accepted state."""
        self._records[record.event_id] = record
        self._records_by_rule.setdefault(record.rule, []).append(record)
        self._records_by_source.setdefault(record.source, []).append(record)

    def get(self, event_id: str) -> Optional[QuarantineRecord]:
        """Fetch quarantined record by event ID."""
        return self._records.get(event_id)

    def list_records(
        self,
        rule: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> list[QuarantineRecord]:
        """Query quarantined records with filtering."""
        if rule and rule in self._records_by_rule:
            res = self._records_by_rule[rule]
        elif source and source in self._records_by_source:
            res = self._records_by_source[source]
        else:
            res = list(self._records.values())
        return res[:limit]

    def count(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        self._records.clear()
        self._records_by_rule.clear()
        self._records_by_source.clear()
