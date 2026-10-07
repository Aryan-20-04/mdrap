"""Deterministic Core Market Data Engine for MDRAP.

Implements pure, I/O-free state transitions:
    state, decision = engine.step(state, raw_event, clock)

Supports deterministic snapshot/restore for state persistence, replay,
and verifiable fault-injection recovery.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
from typing import Any

from .clock import Clock, SystemClock
from .models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason

__stability__ = "stable"


DEFAULT_STALENESS_THRESHOLD_S: float = 0.05
DEFAULT_PRICE_STDDEV: float = 6.0
DEFAULT_PRICE_WINDOW: int = 50
DEFAULT_DEDUP_WINDOW_SIZE: int = 10_000


@dataclass
class EngineState:
    """Serializable, in-memory state of the deterministic engine fold."""

    event_count: int = 0
    sequence_state: dict[str, int] = field(default_factory=dict)
    dedup_window: list[str] = field(default_factory=list)
    dedup_set: set[str] = field(default_factory=set)
    price_windows: dict[str, list[float]] = field(default_factory=dict)
    bbo_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    reconciliation_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=lambda: {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0})
    staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "sequence_state": dict(self.sequence_state),
            "dedup_window": list(self.dedup_window),
            "price_windows": {k: list(v) for k, v in self.price_windows.items()},
            "bbo_state": copy.deepcopy(self.bbo_state),
            "reconciliation_state": copy.deepcopy(self.reconciliation_state),
            "counts": dict(self.counts),
            "staleness_threshold_s": self.staleness_threshold_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineState:
        state = cls(
            event_count=data.get("event_count", 0),
            sequence_state=data.get("sequence_state", {}),
            dedup_window=data.get("dedup_window", []),
            dedup_set=set(data.get("dedup_window", [])),
            price_windows=data.get("price_windows", {}),
            bbo_state=data.get("bbo_state", {}),
            reconciliation_state=data.get("reconciliation_state", {}),
            counts=data.get("counts", {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}),
            staleness_threshold_s=data.get("staleness_threshold_s", DEFAULT_STALENESS_THRESHOLD_S),
        )
        return state


@dataclass
class EngineDecision:
    """Immutable outcome produced by a single step of the Engine fold."""

    offset: int
    event_id: str
    quality_status: QualityStatus
    reasons: list[str] = field(default_factory=list)
    canonical_event: CanonicalEvent | None = None
    quarantine_row: tuple | None = None
    is_duplicate: bool = False
    disagreement: bool = False


class Engine:
    """Pure deterministic engine. Contains ZERO I/O (no SQLite, no disk, no network)."""

    def __init__(self, staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S) -> None:
        self.staleness_threshold_s = staleness_threshold_s

    def create_initial_state(self) -> EngineState:
        return EngineState(staleness_threshold_s=self.staleness_threshold_s)

    def step(
        self,
        state: EngineState,
        raw_event: RawEvent,
        clock: Clock,
        offset: int | None = None,
    ) -> tuple[EngineState, EngineDecision]:
        """Apply a single deterministic state transition: (state, raw_event, clock) -> (state', decision)."""
        if offset is None:
            offset = state.event_count

        state.event_count += 1
        now_ts = clock.now()
        recv_ts = raw_event.receive_timestamp if raw_event.receive_timestamp else now_ts

        # 1. Payload validation (Finding 5, Finding 6)
        payload = raw_event.payload
        if not isinstance(payload, dict):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                "MALFORMED",
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Payload is not a dictionary"]),
                json.dumps(str(payload)),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            decision = EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=["SCHEMA_VIOLATION"],
                quarantine_row=q_row,
            )
            return state, decision

        # 2. Schema field validation
        instrument = payload.get("instrument")
        event_type_str = payload.get("event_type", "TRADE")
        if not instrument or not isinstance(instrument, str):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                "UNKNOWN",
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps(["Missing or invalid instrument string"]),
                json.dumps(payload),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=["SCHEMA_VIOLATION"],
                quarantine_row=q_row,
            )

        # Sequence validation (signed int64)
        seq = payload.get("sequence")
        if seq is not None:
            if not isinstance(seq, int) or not (-9223372036854775808 <= seq <= 9223372036854775807):
                event_id = f"q_{raw_event.source}_{offset}"
                q_row = (
                    event_id,
                    instrument,
                    raw_event.source,
                    QualityStatus.INVALID.value,
                    json.dumps([f"Sequence number out of range: {seq}"]),
                    json.dumps(payload),
                    recv_ts,
                )
                state.counts["INVALID"] += 1
                return state, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=["INVALID_SEQUENCE"],
                    quarantine_row=q_row,
                )

        # Exchange timestamp validation
        exch_ts = payload.get("exchange_ts")
        if exch_ts is None:
            exch_ts = recv_ts
        elif not isinstance(exch_ts, (int, float)) or not math.isfinite(exch_ts):
            event_id = f"q_{raw_event.source}_{offset}"
            q_row = (
                event_id,
                instrument,
                raw_event.source,
                QualityStatus.INVALID.value,
                json.dumps([f"Exchange timestamp not finite: {exch_ts}"]),
                json.dumps(payload),
                recv_ts,
            )
            state.counts["INVALID"] += 1
            return state, EngineDecision(
                offset=offset,
                event_id=event_id,
                quality_status=QualityStatus.INVALID,
                reasons=["SCHEMA_VIOLATION"],
                quarantine_row=q_row,
            )

        # Numeric field finite validation
        for num_field in ("price", "quantity", "bid", "ask", "bid_size", "ask_size"):
            val = payload.get(num_field)
            if val is not None and (not isinstance(val, (int, float)) or not math.isfinite(val)):
                event_id = f"q_{raw_event.source}_{offset}"
                q_row = (
                    event_id,
                    instrument,
                    raw_event.source,
                    QualityStatus.INVALID.value,
                    json.dumps([f"Field {num_field} not a finite number: {val}"]),
                    json.dumps(payload),
                    recv_ts,
                )
                state.counts["INVALID"] += 1
                return state, EngineDecision(
                    offset=offset,
                    event_id=event_id,
                    quality_status=QualityStatus.INVALID,
                    reasons=["SCHEMA_VIOLATION"],
                    quarantine_row=q_row,
                )

        # 3. Deterministic Event Identity (Week 4)
        # Derive durable ID from log offset and source identity
        if raw_event.raw_id:
            event_id = str(raw_event.raw_id)
        elif seq is not None:
            event_id = f"evt_{raw_event.source}_{instrument}_{seq}"
        else:
            event_id = f"evt_{raw_event.source}_{instrument}_{offset}"

        # 4. Quality Rules Evaluation
        status = QualityStatus.VALID
        reasons: list[str] = []
        is_duplicate = False

        slot_key = f"{raw_event.source}:{instrument}"

        # Sequenced deduplication & gap detection
        if seq is not None:
            last_seq = state.sequence_state.get(slot_key)
            if last_seq is not None:
                if seq == last_seq:
                    is_duplicate = True
                    status = QualityStatus.INVALID
                    reasons.append(Reason.DUPLICATE.value)
                elif seq < last_seq:
                    status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.SEQUENCE_OUT_OF_ORDER.value)
                elif seq > last_seq + 1:
                    status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.SEQUENCE_GAP.value)
            state.sequence_state[slot_key] = max(last_seq if last_seq is not None else -1, seq)
        else:
            # Unsequenced deduplication window
            dedup_fingerprint = f"{instrument}:{exch_ts}:{payload.get('price')}:{payload.get('bid')}:{payload.get('ask')}"
            if dedup_fingerprint in state.dedup_set:
                is_duplicate = True
                status = QualityStatus.INVALID
                reasons.append(Reason.DUPLICATE.value)
            else:
                state.dedup_set.add(dedup_fingerprint)
                state.dedup_window.append(dedup_fingerprint)
                if len(state.dedup_window) > DEFAULT_DEDUP_WINDOW_SIZE:
                    evicted = state.dedup_window.pop(0)
                    state.dedup_set.discard(evicted)

        # Staleness check
        if status != QualityStatus.INVALID:
            if recv_ts - exch_ts > state.staleness_threshold_s:
                if status == QualityStatus.VALID:
                    status = QualityStatus.SUSPICIOUS
                reasons.append(Reason.STALE.value)

        # Quote crossed book check
        bid = payload.get("bid")
        ask = payload.get("ask")
        if bid is not None and ask is not None and bid > ask:
            status = QualityStatus.INVALID
            reasons.append(Reason.CROSSED_MARKET.value)

        # Price sanity check (Welford / rolling price corridor)
        price = payload.get("price")
        if price is not None and status != QualityStatus.INVALID:
            prices = state.price_windows.setdefault(slot_key, [])
            if len(prices) >= 20:
                mean = sum(prices) / len(prices)
                variance = sum((p - mean) ** 2 for p in prices) / len(prices)
                std = math.sqrt(variance)
                sigma_floor = mean * 0.0001
                effective_std = max(std, sigma_floor)
                if effective_std > 0 and abs(price - mean) > (DEFAULT_PRICE_STDDEV * effective_std):
                    if status == QualityStatus.VALID:
                        status = QualityStatus.SUSPICIOUS
                    reasons.append(Reason.PRICE_OUT_OF_BOUNDS.value)
            prices.append(price)
            if len(prices) > DEFAULT_PRICE_WINDOW:
                prices.pop(0)

        # Update BBO state
        if bid is not None or ask is not None:
            curr_bbo = state.bbo_state.setdefault(instrument, {"bid": 0.0, "ask": float("inf")})
            if bid is not None and bid > curr_bbo.get("bid", 0.0):
                curr_bbo["bid"] = bid
            if ask is not None and ask < curr_bbo.get("ask", float("inf")):
                curr_bbo["ask"] = ask

        # Cross-feed reconciliation
        disagreement = False
        if price is not None:
            recent_inst = state.reconciliation_state.setdefault(instrument, {})
            recent_inst[raw_event.source] = price
            prices_across = list(recent_inst.values())
            if len(prices_across) >= 2:
                spread = max(prices_across) - min(prices_across)
                avg_p = sum(prices_across) / len(prices_across)
                if avg_p > 0 and (spread / avg_p) > 0.01:  # 1% cross-feed disagreement
                    disagreement = True
                    reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
                    if status == QualityStatus.VALID:
                        status = QualityStatus.SUSPICIOUS

        state.counts[status.name] += 1

        try:
            ev_type = EventType(event_type_str)
        except ValueError:
            ev_type = EventType.UNKNOWN

        canonical_event = CanonicalEvent(
            event_id=event_id,
            instrument_id=instrument,
            event_type=ev_type,
            exchange_timestamp=float(exch_ts),
            receive_timestamp=float(recv_ts),
            processing_timestamp=float(now_ts),
            source=raw_event.source,
            sequence_number=seq,
            price=price,
            quantity=payload.get("quantity"),
            bid_price=bid,
            ask_price=ask,
            bid_size=payload.get("bid_size"),
            ask_size=payload.get("ask_size"),
            quality_status=status,
            reasons=reasons,
            raw_id=raw_event.raw_id,
        )

        decision = EngineDecision(
            offset=offset,
            event_id=event_id,
            quality_status=status,
            reasons=reasons,
            canonical_event=canonical_event,
            is_duplicate=is_duplicate,
            disagreement=disagreement,
        )

        return state, decision

    def snapshot(self, state: EngineState) -> bytes:
        """Create a deterministic byte snapshot of the current state."""
        data = state.to_dict()
        return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def restore(self, snapshot_bytes: bytes) -> EngineState:
        """Restore state from snapshot bytes."""
        data = json.loads(snapshot_bytes.decode("utf-8"))
        return EngineState.from_dict(data)
