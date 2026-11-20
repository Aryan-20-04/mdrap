"""MDRAP Quarantine Subsystem.

Provides first-class evidentiary isolation for rejected, malformed, and anomalous market data.
Strictly adheres to Design Principle 3: 'Never silently discard bad data — quarantine, never drop.'
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Optional
from .models import CanonicalEvent, RawEvent

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
