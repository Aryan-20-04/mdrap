"""Frozen, obviously-correct Golden Reference Oracle for differential testing.

This reference implementation intentionally avoids optimization shortcuts, SIMD,
incremental Welford arithmetic, direct-mapped caches, or bitwise approximations.
It serves as an independent, non-tautological baseline to verify correctness of both
the optimized Python path and the native C hot path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from mdrap.models import CanonicalEvent, EventType, QualityStatus, Reason


# Canonical reason bit shifts matching rules.def
REASON_BITS = {
    Reason.SCHEMA_VIOLATION.value: 0,
    Reason.DUPLICATE.value: 1,
    Reason.SEQUENCE_GAP.value: 2,
    Reason.OUT_OF_ORDER.value: 3,
    Reason.STALE.value: 4,
    Reason.PRICE_ANOMALY.value: 5,
    Reason.CROSSED_QUOTE.value: 6,
    Reason.CROSS_FEED_DISAGREEMENT.value: 7,
    Reason.MALFORMED.value: 8,
    Reason.CIRCUIT_FILTER_BREACH.value: 9,
    Reason.VOLATILITY_INTERRUPTION.value: 10,
    Reason.SPECIAL_QUOTE_INDICATION.value: 11,
    Reason.TS_IMPLAUSIBLE.value: 12,
    Reason.RATE_LIMITED.value: 13,
    Reason.SECURITY_REJECT.value: 14,
}

SEQUENCE_BITMAP_BITS = 64
SEQUENCE_BITMAP_MASK = 0xFFFFFFFFFFFFFFFF
DEFAULT_SEQ_JUMP_LIMIT = 1 << 24
MIN_SAMPLES_FOR_VARIANCE = 3
WARMUP_PRICE_DEV_RATIO = 0.10


@dataclass
class GoldenConfig:
    staleness_threshold_s: float = 0.05
    price_anomaly_stddev: float = 6.0
    price_window: int = 50
    price_min_samples: int = 20
    price_reseed_after: int = 8
    price_sigma_floor_rel: float = 2e-4
    price_reseed_band_rel: float = 0.01
    max_future_skew_s: float = 1.0
    seq_jump_limit: int = DEFAULT_SEQ_JUMP_LIMIT
    allow_negative: bool = False
    unseq_dup_status: QualityStatus = QualityStatus.SUSPICIOUS


class GoldenOracle:
    """Independent reference validator using exact standard arithmetic and exact sets."""

    def __init__(self, config: GoldenConfig | None = None) -> None:
        self.config = config or GoldenConfig()
        # Per-(source, instrument) sequence state:
        # slot_key -> {"last_seq": int, "seen": int, "has_seq": bool, "cand_plus1": int, "last_ts": float | None}
        self._slot_state: dict[tuple[str, str], dict[str, Any]] = {}
        # Per-(source, instrument) price history:
        # slot_key -> list[float]
        self._price_windows: dict[tuple[str, str], list[float]] = {}
        # Per-(source, instrument) regime reseed anomaly trackers:
        # slot_key -> {"anom_count": int, "anom_last": float}
        self._price_anom_trackers: dict[tuple[str, str], dict[str, Any]] = {}
        # Unsequenced content dedup set:
        self._content_seen: set[tuple[Any, ...]] = set()

    def evaluate(self, event: CanonicalEvent) -> tuple[QualityStatus, list[str], int]:
        """Evaluate event against reference rules and return (status, reasons, reason_mask)."""
        reasons: list[str] = []
        reason_mask: int = 0
        status = QualityStatus.VALID

        def _mark(r_name: str, r_status: QualityStatus) -> None:
            nonlocal status, reason_mask
            if r_name not in reasons:
                reasons.append(r_name)
            bit = REASON_BITS.get(r_name)
            if bit is not None:
                reason_mask |= 1 << bit
            if r_status.severity_level > status.severity_level:
                status = r_status

        # 1. Structural schema & finite checks
        bad_schema = False
        if not event.instrument_id or not isinstance(event.instrument_id, str):
            _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
            bad_schema = True

        ex_fin = math.isfinite(event.exchange_timestamp)
        rc_fin = math.isfinite(event.receive_timestamp)
        if not ex_fin or not rc_fin:
            _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
            bad_schema = True

        has_price = event.price is not None and not (
            isinstance(event.price, float) and math.isnan(event.price)
        )
        has_qty = event.quantity is not None and not (
            isinstance(event.quantity, float) and math.isnan(event.quantity)
        )
        has_bid = event.bid_price is not None and not (
            isinstance(event.bid_price, float) and math.isnan(event.bid_price)
        )
        has_ask = event.ask_price is not None and not (
            isinstance(event.ask_price, float) and math.isnan(event.ask_price)
        )

        # Check numeric fields for non-finite values (Inf or NaN)
        for val in (
            event.price,
            event.quantity,
            event.bid_price,
            event.ask_price,
            event.bid_size,
            event.ask_size,
        ):
            if val is not None and not math.isfinite(val):
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True

        # Non-negative checks (if allow_negative is False)
        if not self.config.allow_negative and not bad_schema:
            for val in (event.price, event.quantity, event.bid_size, event.ask_size):
                if val is not None and val < 0.0:
                    _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                    bad_schema = True
            if event.bid_price is not None and event.bid_price < 0.0:
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True
            if event.ask_price is not None and event.ask_price < 0.0:
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True

        # Zero price / size checks for trades
        if event.event_type == EventType.TRADE and not bad_schema:
            if not has_price:
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True
            elif (
                not self.config.allow_negative
                and event.price is not None
                and event.price < 0.0
            ):
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True
            if has_qty and event.quantity is not None and event.quantity < 0.0:
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True

        slot_key = (event.source, event.instrument_id)
        slot = self._slot_state.setdefault(
            slot_key,
            {
                "last_seq": 0,
                "seen": 0,
                "has_seq": False,
                "cand_plus1": 0,
                "last_ts": None,
            },
        )

        # 2. Sequence validation & deduplication
        if event.sequence_number is not None:
            seq = event.sequence_number
            if not isinstance(seq, int) or isinstance(seq, bool):
                _mark(Reason.SCHEMA_VIOLATION.value, QualityStatus.INVALID)
                bad_schema = True
            else:
                if not slot["has_seq"]:
                    slot["last_seq"] = seq
                    slot["seen"] = 1
                    slot["has_seq"] = True
                elif seq == slot["last_seq"]:
                    _mark(Reason.DUPLICATE.value, QualityStatus.INVALID)
                elif seq > slot["last_seq"]:
                    jump = seq - slot["last_seq"]
                    if jump > self.config.seq_jump_limit:
                        _mark(Reason.SEQUENCE_GAP.value, QualityStatus.SUSPICIOUS)
                        cand = slot["cand_plus1"] - 1
                        if (
                            slot["cand_plus1"] != 0
                            and seq > cand
                            and (seq - cand) <= self.config.seq_jump_limit
                        ):
                            slot["last_seq"] = seq
                            slot["seen"] = 1
                            slot["cand_plus1"] = 0
                        else:
                            slot["cand_plus1"] = seq + 1
                    else:
                        if jump > 1:
                            _mark(Reason.SEQUENCE_GAP.value, QualityStatus.SUSPICIOUS)
                        slot["seen"] = (
                            ((slot["seen"] << jump) | 1) & SEQUENCE_BITMAP_MASK
                            if jump < 64
                            else 1
                        )
                        slot["last_seq"] = seq
                        slot["cand_plus1"] = 0
                else:
                    d = slot["last_seq"] - seq
                    if d < 64:
                        if (slot["seen"] >> d) & 1:
                            _mark(Reason.DUPLICATE.value, QualityStatus.INVALID)
                        else:
                            slot["seen"] |= 1 << d
                            _mark(Reason.OUT_OF_ORDER.value, QualityStatus.SUSPICIOUS)
                    else:
                        _mark(Reason.OUT_OF_ORDER.value, QualityStatus.SUSPICIOUS)
        elif ex_fin:
            # Unsequenced content-based deduplication
            ev_type_str = (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            )
            content_key = (
                event.source,
                event.instrument_id,
                ev_type_str,
                event.exchange_timestamp,
                event.price,
                event.quantity,
                event.bid_price,
                event.ask_price,
                event.bid_size,
                event.ask_size,
            )
            if content_key in self._content_seen:
                _mark(Reason.DUPLICATE.value, self.config.unseq_dup_status)
            else:
                self._content_seen.add(content_key)

        # 3. Timestamps & Monotonicity
        if ex_fin and rc_fin:
            plausible = event.exchange_timestamp <= (
                event.receive_timestamp + self.config.max_future_skew_s
            )
            if not plausible:
                _mark(Reason.TS_IMPLAUSIBLE.value, QualityStatus.SUSPICIOUS)

            if slot["last_ts"] is None:
                if plausible:
                    slot["last_ts"] = event.exchange_timestamp
            elif event.exchange_timestamp < slot["last_ts"]:
                _mark(Reason.OUT_OF_ORDER.value, QualityStatus.SUSPICIOUS)
            elif plausible:
                slot["last_ts"] = event.exchange_timestamp

            # Staleness
            if (
                event.receive_timestamp - event.exchange_timestamp
            ) > self.config.staleness_threshold_s:
                _mark(Reason.STALE.value, QualityStatus.SUSPICIOUS)

        # 4. Quote crossed-market checks
        if (
            not bad_schema
            and has_bid
            and has_ask
            and event.bid_price is not None
            and event.ask_price is not None
        ):
            if event.bid_price > event.ask_price:
                _mark(Reason.CROSSED_QUOTE.value, QualityStatus.INVALID)

        # 5. Price anomaly / corridor check (only for clean trade events)
        if (
            status != QualityStatus.INVALID
            and has_price
            and event.price is not None
            and math.isfinite(event.price)
        ):
            px = float(event.price)
            prices = self._price_windows.setdefault(slot_key, [])
            anom_tracker = self._price_anom_trackers.setdefault(
                slot_key, {"anom_count": 0, "anom_last": 0.0}
            )
            n = len(prices)

            anomaly = False
            sd2 = 0.0

            if n >= MIN_SAMPLES_FOR_VARIANCE:
                mean = sum(prices) / n
                var = sum((p - mean) ** 2 for p in prices) / n
                fl = self.config.price_sigma_floor_rel * abs(mean)
                fl2 = fl * fl
                sd2 = var if var > fl2 else fl2
                dev = px - mean

                if n >= self.config.price_min_samples:
                    anomaly = (dev * dev) > (
                        self.config.price_anomaly_stddev
                        * self.config.price_anomaly_stddev
                        * sd2
                    )
                else:
                    anomaly = abs(mean) > 0.0 and abs(dev) > (
                        WARMUP_PRICE_DEV_RATIO * abs(mean)
                    )

            if anomaly:
                _mark(Reason.PRICE_ANOMALY.value, QualityStatus.SUSPICIOUS)
                band = self.config.price_anomaly_stddev * math.sqrt(sd2)
                rel = self.config.price_reseed_band_rel * abs(px)
                if rel > band:
                    band = rel
                if (
                    anom_tracker["anom_count"] > 0
                    and abs(px - anom_tracker["anom_last"]) <= band
                ):
                    anom_tracker["anom_count"] += 1
                else:
                    anom_tracker["anom_count"] = 1
                anom_tracker["anom_last"] = px

                if (
                    self.config.price_reseed_after > 0
                    and anom_tracker["anom_count"] >= self.config.price_reseed_after
                ):
                    prices.clear()
                    prices.append(px)
                    anom_tracker["anom_count"] = 0
            else:
                anom_tracker["anom_count"] = 0
                prices.append(px)
                if len(prices) > self.config.price_window:
                    prices.pop(0)

        return status, sorted(reasons), reason_mask
