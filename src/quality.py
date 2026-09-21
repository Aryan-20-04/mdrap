"""Data-Quality Evaluation Engine.

This module provides stateful, per-(source, instrument) data validation checks applied
to every normalized CanonicalEvent in the processing pipeline. It evaluates and tags
each event with a QualityStatus (VALID, SUSPICIOUS, or INVALID) along with granular
Reason codes, strictly adhering to the architectural principle:
    "Never silently discard bad data — quarantine, never drop." (Spec §26)
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from models import CanonicalEvent, EventType, QualityStatus, Reason

# ---------------------------------------------------------------------------
# Data Quality & Market Integrity Constants (Spec §26)
# ---------------------------------------------------------------------------
DEFAULT_STALENESS_THRESHOLD_S: float = 0.05  # 50 ms staleness horizon
DEFAULT_PRICE_ANOMALY_STDDEV: float = 6.0  # 6-sigma statistical corridor
DEFAULT_PRICE_WINDOW: int = 50  # Rolling tick sample window
DEFAULT_PRICE_MIN_SAMPLES: int = 20  # Minimum samples required before sigma test
DEFAULT_PRICE_RESEED_AFTER: int = 8  # Consecutive consistent outliers before regime shift re-seed
DEFAULT_PRICE_SIGMA_FLOOR_REL: float = 2e-4  # 2 bps relative variance floor
DEFAULT_PRICE_RESEED_BAND_REL: float = 0.01  # 1% consistent cluster band for re-seeding
DEFAULT_MAX_FUTURE_SKEW_S: float = 1.0  # Max permissible exchange timestamp lead vs receive time
DEFAULT_SEQ_JUMP_LIMIT: int = 1 << 24  # Max realistic sequence gap before flagged as gap
DEFAULT_DEDUP_CACHE_SIZE: int = 200_000  # Default unsequenced two-generation cache capacity
SEQUENCE_BITMAP_BITS: int = 64  # Sliding bitmap width in bits
SEQUENCE_BITMAP_MASK: int = 0xFFFFFFFFFFFFFFFF  # 64-bit mask for sequence window
MIN_SAMPLES_FOR_VARIANCE: int = 3  # Minimum ticks required to evaluate running variance
WARMUP_PRICE_DEV_RATIO: float = 0.10  # 10% deviation allowed during rolling window warm-up
NSE_CIRCUIT_FILTER_RATIO: float = 0.10  # 10% daily price band circuit filter (NSE/India)
XETR_VOLATILITY_INTERRUPTION_RATIO: float = 0.05  # 5% dynamic price corridor (Xetra/Germany)
TSE_SPECIAL_QUOTE_RATIO: float = 0.08  # 8% special quote renewal limit (TSE/Japan)
RECENTER_FOLD_WINDOW_MULTIPLIER: int = 64  # Periodic full-precision re-centering interval
MAX_TRACKED_INSTRUMENTS_DEFAULT: int = 100_000  # Max active tracking slots before LRU eviction

try:
    from config import QualityConfig
except ImportError:
    @dataclass
    class QualityConfig:  # type: ignore[no-redef]
        staleness_threshold_s: float = DEFAULT_STALENESS_THRESHOLD_S
        price_anomaly_stddev: float = DEFAULT_PRICE_ANOMALY_STDDEV
        price_window: int = DEFAULT_PRICE_WINDOW
        price_min_samples: int = DEFAULT_PRICE_MIN_SAMPLES
        price_reseed_after: int = DEFAULT_PRICE_RESEED_AFTER
        price_sigma_floor_rel: float = DEFAULT_PRICE_SIGMA_FLOOR_REL
        price_reseed_band_rel: float = DEFAULT_PRICE_RESEED_BAND_REL
        max_future_skew_s: float = DEFAULT_MAX_FUTURE_SKEW_S
        seq_jump_limit: int = DEFAULT_SEQ_JUMP_LIMIT
        dedup_cache_size: int = DEFAULT_DEDUP_CACHE_SIZE
        allow_negative: bool = False
        unseq_dup_status: str = "SUSPICIOUS"

        def for_instrument(self, instrument_id: str) -> QualityConfig:
            return self


class _RollingStats:
    """Backward-compatible wrapper around sliding window mean and variance."""

    __slots__ = ("window", "values", "_mean", "_m2", "_n")

    def __init__(self, window: int):
        self.window = window
        self.values: deque[float] = deque()
        self._mean = 0.0
        self._m2 = 0.0
        self._n = 0

    def get_stats(self, x: float) -> tuple[float, float]:
        if self._n == 0:
            return x, 0.0
        mean = self._mean
        var = self._m2 / self._n if self._n > 0 else 0.0
        return mean, math.sqrt(max(0.0, var))

    def update(self, x: float) -> None:
        self.values.append(x)
        self._n += 1
        delta = x - self._mean
        self._mean += delta / self._n
        self._m2 += delta * (x - self._mean)

        if self._n > self.window:
            old = self.values.popleft()
            self._n -= 1
            delta_old = old - self._mean
            self._mean -= delta_old / self._n if self._n > 0 else 0.0
            self._m2 -= delta_old * (old - self._mean)
            self._m2 = max(0.0, self._m2)

    def check_and_update(self, x: float) -> tuple[float, float]:
        mean, std = self.get_stats(x)
        self.update(x)
        return mean, std


class _SlotState:
    """Per-(source, instrument) sliding state matching C kernel ABI."""

    __slots__ = (
        "last_seq",
        "seen",
        "cand_plus1",
        "has_seq",
        "last_ts",
        "has_ts",
        "ring",
        "n",
        "head",
        "mean",
        "m2",
        "anom_count",
        "anom_last",
        "fold_count",
    )

    def __init__(self, window: int):
        self.last_seq = -1
        self.seen = 0
        self.cand_plus1 = 0
        self.has_seq = False
        self.last_ts = 0.0
        self.has_ts = False
        self.ring = [0.0] * window
        self.n = 0
        self.head = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.anom_count = 0
        self.anom_last = 0.0
        self.fold_count = 0

    def fold_price(self, x: float, window: int) -> None:
        if self.n >= window:
            old = self.ring[self.head]
            d = x - old
            nm = self.mean + d / window
            self.m2 += d * ((x - nm) + (old - self.mean))
            self.mean = nm
            self.ring[self.head] = x
            self.head += 1
            if self.head >= window:
                self.head = 0
            if self.m2 < 0.0:
                self.m2 = 0.0
        else:
            self.ring[self.n] = x
            self.n += 1
            delta = x - self.mean
            self.mean += delta / self.n
            self.m2 += delta * (x - self.mean)
            self.head = 0 if self.n >= window else self.n

        self.fold_count += 1
        if self.fold_count >= window * RECENTER_FOLD_WINDOW_MULTIPLIER:
            self.fold_count = 0
            s = sum(self.ring[: self.n])
            self.mean = s / self.n
            self.m2 = sum((v - self.mean) ** 2 for v in self.ring[: self.n])


class _UnseqDedup:
    """Two-generation sliding-window dedup cache for unsequenced events."""

    __slots__ = ("active", "older", "count", "max_entries")

    def __init__(self, max_entries: int = DEFAULT_DEDUP_CACHE_SIZE):
        self.active: dict[tuple, None] = {}
        self.older: dict[tuple, None] = {}
        self.count = 0
        self.max_entries = max(100, max_entries // 2)

    def check_and_insert(self, key: tuple) -> bool:
        hit = (key in self.older) or (key in self.active)
        if key not in self.active:
            self.active[key] = None
            self.count += 1
            if self.count >= self.max_entries:
                self.older = self.active
                self.active = {}
                self.count = 0
        return hit

    def clear(self) -> None:
        self.active.clear()
        self.older.clear()
        self.count = 0


class QualityEngine:
    """Stateful market data quality assessment engine with 100% C-kernel parity."""

    _STATUS_PRIORITY = {
        QualityStatus.VALID: 0,
        QualityStatus.SUSPICIOUS: 1,
        QualityStatus.INVALID: 2,
    }

    def __init__(self, config: QualityConfig | None = None):
        if config is None:
            try:
                from config import load_config

                self.cfg = load_config().quality
            except Exception:
                self.cfg = QualityConfig()
        else:
            self.cfg = config

        self._slots: dict[tuple[str, str], _SlotState] = {}
        self._cfg_cache: dict[str, QualityConfig] = {}
        self._unseq_dedup = _UnseqDedup(getattr(self.cfg, "dedup_cache_size", DEFAULT_DEDUP_CACHE_SIZE))
        self.max_tracked_instruments = MAX_TRACKED_INSTRUMENTS_DEFAULT

        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts: dict[str, int] = {}

        try:
            from rules import _USER_RULES, evaluate_user_rules

            self._user_rules = _USER_RULES
            self._evaluate_user_rules = evaluate_user_rules
        except (ImportError, AttributeError):
            self._user_rules = None
            self._evaluate_user_rules = None

    def reset(self) -> None:
        """Reset internal engine tracking state."""
        self._slots.clear()
        self._cfg_cache.clear()
        self._unseq_dedup.clear()
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts.clear()

    def reset_source(self, source: str) -> None:
        """Reset sequence and deduplication tracking for a specific source."""
        s_upper = source.upper()
        keys_to_del = [k for k in self._slots if k[0].upper() == s_upper]
        for k in keys_to_del:
            del self._slots[k]

    @property
    def _price_stats(self) -> dict:
        """Backward-compatible map of instrument_id to _SlotState."""
        return {k[1]: v for k, v in self._slots.items()}

    def _mark(
        self, event: CanonicalEvent, status: QualityStatus, reason: Reason
    ) -> None:
        """Escalate event QualityStatus if proposed status has higher priority."""
        if self._STATUS_PRIORITY[status] > self._STATUS_PRIORITY.get(
            event.quality_status, 0
        ):
            event.quality_status = status
        val = reason.value
        if val not in event.reasons:
            event.reasons.append(val)

    def _bump(self, reason: Reason) -> None:
        """Increment telemetry counter for the given Reason code."""
        self.reason_counts[reason.value] = self.reason_counts.get(reason.value, 0) + 1

    def _get_cfg(self, instrument_id: str) -> QualityConfig:
        cfg = self._cfg_cache.get(instrument_id)
        if cfg is None:
            cfg = (
                self.cfg.for_instrument(instrument_id)
                if hasattr(self.cfg, "for_instrument")
                else self.cfg
            )
            self._cfg_cache[instrument_id] = cfg
        return cfg

    def _get_slot(self, key: tuple[str, str], window: int) -> _SlotState:
        sl = self._slots.get(key)
        if sl is None:
            if len(self._slots) >= self.max_tracked_instruments:
                # Evict an arbitrary key
                self._slots.pop(next(iter(self._slots)))
            sl = self._slots[key] = _SlotState(window)
        return sl

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        """Execute stateful data quality checks across all dimensions."""
        key = (event.source, event.instrument_id)
        cfg = self._get_cfg(event.instrument_id)
        sl = self._get_slot(key, cfg.price_window)

        # Stage 1: Structural Validation
        is_quote = (
            event.event_type == EventType.QUOTE
            or event.event_type == "QUOTE"
        )
        is_trade = (
            event.event_type == EventType.TRADE
            or event.event_type == "TRADE"
        )

        bad = False
        if not is_trade and not is_quote:
            bad = True

        has_price = event.price is not None
        has_qty = event.quantity is not None
        has_bid = event.bid_price is not None
        has_ask = event.ask_price is not None
        has_bsz = event.bid_size is not None
        has_asz = event.ask_size is not None

        allow_neg = cfg.allow_negative

        if is_trade:
            if not has_price:
                bad = True
        else:
            if not has_bid and not has_ask:
                bad = True

        def num_bad(v: float, allow_n: bool) -> bool:
            return not math.isfinite(v) or (not allow_n and v < 0.0)

        if has_price and num_bad(event.price, allow_neg):  # type: ignore[arg-type]
            bad = True
        if has_bid and num_bad(event.bid_price, allow_neg):  # type: ignore[arg-type]
            bad = True
        if has_ask and num_bad(event.ask_price, allow_neg):  # type: ignore[arg-type]
            bad = True
        if has_qty and num_bad(event.quantity, False):  # type: ignore[arg-type]
            bad = True
        if has_bsz and num_bad(event.bid_size, False):  # type: ignore[arg-type]
            bad = True
        if has_asz and num_bad(event.ask_size, False):  # type: ignore[arg-type]
            bad = True

        ex_fin = math.isfinite(event.exchange_timestamp)
        rc_fin = math.isfinite(event.receive_timestamp)
        if not ex_fin or not rc_fin:
            bad = True

        if bad:
            self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
            self._bump(Reason.SCHEMA_VIOLATION)

        # Stage 2: Sequence / Duplicate Detection
        if event.sequence_number is not None and event.sequence_number >= 0:
            seq = int(event.sequence_number)
            if not sl.has_seq:
                sl.last_seq = seq
                sl.seen = 1
                sl.has_seq = True
            elif seq > sl.last_seq:
                jump = seq - sl.last_seq
                if jump > cfg.seq_jump_limit:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.SEQUENCE_GAP)
                    self._bump(Reason.SEQUENCE_GAP)
                    cand = sl.cand_plus1 - 1
                    if (
                        sl.cand_plus1 != 0
                        and seq > cand
                        and (seq - cand) <= cfg.seq_jump_limit
                    ):
                        sl.last_seq = seq
                        sl.seen = 1
                        sl.cand_plus1 = 0
                    else:
                        sl.cand_plus1 = seq + 1
                else:
                    if jump > 1:
                        self._mark(event, QualityStatus.SUSPICIOUS, Reason.SEQUENCE_GAP)
                        self._bump(Reason.SEQUENCE_GAP)
                    sl.seen = (
                        ((sl.seen << jump) | 1) & SEQUENCE_BITMAP_MASK
                        if jump < SEQUENCE_BITMAP_BITS
                        else 1
                    )
                    sl.last_seq = seq
                    sl.cand_plus1 = 0
            else:
                d = sl.last_seq - seq
                if d < SEQUENCE_BITMAP_BITS:
                    if (sl.seen >> d) & 1:
                        self._mark(event, QualityStatus.INVALID, Reason.DUPLICATE)
                        self._bump(Reason.DUPLICATE)
                    else:
                        sl.seen |= 1 << d
                        self._mark(event, QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER)
                        self._bump(Reason.OUT_OF_ORDER)
                else:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER)
                    self._bump(Reason.OUT_OF_ORDER)
        elif ex_fin:
            # Unsequenced events: 2-gen sliding window
            ev_type_str = (
                event.event_type.value
                if hasattr(event.event_type, "value")
                else str(event.event_type)
            )
            unseq_key = (
                event.source,
                event.instrument_id,
                ev_type_str,
                event.exchange_timestamp,
                event.price,
                event.quantity,
                event.bid_price,
                event.ask_price,
            )
            if self._unseq_dedup.check_and_insert(unseq_key):
                dup_st_name = getattr(cfg, "unseq_dup_status", "SUSPICIOUS")
                dup_st = (
                    QualityStatus[dup_st_name]
                    if isinstance(dup_st_name, str) and dup_st_name in QualityStatus.__members__
                    else QualityStatus.SUSPICIOUS
                )
                self._mark(event, dup_st, Reason.DUPLICATE)
                self._bump(Reason.DUPLICATE)

        # Stage 3: Exchange Timestamp Monotonicity & Watermark Protection
        if ex_fin and rc_fin:
            plausible = (
                event.exchange_timestamp
                <= event.receive_timestamp + cfg.max_future_skew_s
            )
            if not plausible:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.TS_IMPLAUSIBLE)
                self._bump(Reason.TS_IMPLAUSIBLE)

            if not sl.has_ts:
                if plausible:
                    sl.last_ts = event.exchange_timestamp
                    sl.has_ts = True
            elif event.exchange_timestamp < sl.last_ts:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER)
                self._bump(Reason.OUT_OF_ORDER)
            elif plausible:
                sl.last_ts = event.exchange_timestamp

            if (
                event.receive_timestamp - event.exchange_timestamp
                > cfg.staleness_threshold_s
            ):
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.STALE)
                self._bump(Reason.STALE)

        # Stage 4: Quote Consistency (Crossed Book Detection)
        if (
            not bad
            and has_bid
            and has_ask
            and event.bid_price > event.ask_price  # type: ignore[operator]
        ):
            self._mark(event, QualityStatus.INVALID, Reason.CROSSED_QUOTE)
            self._bump(Reason.CROSSED_QUOTE)

        # Stage 5: Statistical Price Corridor Evaluation (LAST: Clean events only)
        if event.quality_status != QualityStatus.INVALID and has_price and math.isfinite(event.price):
            px = float(event.price)  # type: ignore[arg-type]
            anomaly = False
            sd2 = 0.0
            if sl.n >= MIN_SAMPLES_FOR_VARIANCE:
                mean = sl.mean
                var = sl.m2 / sl.n if sl.n > 0 else 0.0
                if var < 0.0:
                    var = 0.0
                fl = cfg.price_sigma_floor_rel * abs(mean)
                sd2 = var if var > fl * fl else fl * fl
                dev = px - mean
                if sl.n >= cfg.price_min_samples:
                    anomaly = (dev * dev) > (
                        cfg.price_anomaly_stddev * cfg.price_anomaly_stddev * sd2
                    )
                else:
                    anomaly = abs(mean) > 0.0 and abs(dev) > WARMUP_PRICE_DEV_RATIO * abs(mean)

            if anomaly:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY)
                self._bump(Reason.PRICE_ANOMALY)

                # Venue circuit filter and volatility corridor rules
                if event.venue == "XNSE" and sl.mean > 0 and abs(dev) / sl.mean >= NSE_CIRCUIT_FILTER_RATIO:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.CIRCUIT_FILTER_BREACH)
                    self._bump(Reason.CIRCUIT_FILTER_BREACH)
                elif event.venue == "XETR" and sl.mean > 0 and abs(dev) / sl.mean >= XETR_VOLATILITY_INTERRUPTION_RATIO:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.VOLATILITY_INTERRUPTION)
                    self._bump(Reason.VOLATILITY_INTERRUPTION)
                elif event.venue in ("XTKS", "TSE") and sl.mean > 0 and abs(dev) / sl.mean >= TSE_SPECIAL_QUOTE_RATIO:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.SPECIAL_QUOTE_INDICATION)
                    self._bump(Reason.SPECIAL_QUOTE_INDICATION)

                band = cfg.price_anomaly_stddev * math.sqrt(sd2)
                rel = cfg.price_reseed_band_rel * abs(px)
                if rel > band:
                    band = rel
                if sl.anom_count > 0 and abs(px - sl.anom_last) <= band:
                    sl.anom_count += 1
                else:
                    sl.anom_count = 1
                sl.anom_last = px

                if (
                    cfg.price_reseed_after > 0
                    and sl.anom_count >= cfg.price_reseed_after
                ):
                    sl.n = 0
                    sl.head = 0
                    sl.mean = 0.0
                    sl.m2 = 0.0
                    sl.anom_count = 0
                    sl.fold_count = 0
                    sl.fold_price(px, cfg.price_window)
            else:
                sl.anom_count = 0
                sl.fold_price(px, cfg.price_window)

        # Stage 6: Isolated User Rules
        if self._user_rules and self._evaluate_user_rules:
            try:
                event = self._evaluate_user_rules(event)
            except Exception:
                pass

        self.counts[event.quality_status.value] += 1
        return event


# Phase 5: Explicit Two-Tier Architecture Aliases
PythonQualityEngine = QualityEngine
