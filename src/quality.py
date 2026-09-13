"""
Data-Quality Engine.

Stateful, per-(source, instrument) checks applied to every normalized
event. Classifies each event VALID / SUSPICIOUS / INVALID and attaches
reason codes -- never silently drops anything (see quarantine.py).

Design principle from the spec: an anomaly is not automatically an
error. Sequence gaps, out-of-order arrivals, and price outliers are
SUSPICIOUS (flagged, kept, explainable) rather than INVALID. Only
structural problems -- failed schema parsing, exact duplicates, and
crossed quotes -- are INVALID.
"""
from __future__ import annotations

import math
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from models import CanonicalEvent, QualityStatus, Reason


@dataclass
class QualityConfig:
    staleness_threshold_s: float = 0.05        # receive - exchange ts
    price_anomaly_stddev: float = 6.0            # flag price beyond k * rolling stddev
    price_window: int = 50                       # rolling window size per instrument
    dedup_cache_size: int = 200_000               # bounded LRU-ish window per source


class _RollingStats:
    """Fixed-window mean/stddev tracker using Welford's online algorithm.

    Numerically stable: avoids the naive sum-of-squares cancellation
    that can produce negative variance on long streams.

    `check_and_update(x)` returns the mean/stddev of the *prior*
    baseline (before x is folded in), so an anomalous point is judged
    against established history, not a window biased by the point
    being tested.
    """
    __slots__ = ("window", "values", "_mean", "_m2", "_n")

    def __init__(self, window: int):
        self.window = window
        self.values: deque[float] = deque()
        self._mean = 0.0
        self._m2 = 0.0     # sum of squared deviations from mean
        self._n = 0

    def get_stats(self, x: float) -> Tuple[float, float]:
        """Return prior clean baseline (mean, stddev) before x."""
        if self._n == 0:
            return x, 0.0
        mean = self._mean
        var = self._m2 / self._n if self._n > 0 else 0.0
        return mean, math.sqrt(max(0.0, var))

    def update(self, x: float) -> None:
        """Fold x into clean baseline window (Welford online update)."""
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

    def check_and_update(self, x: float) -> Tuple[float, float]:
        """Backward-compatible helper."""
        mean, std = self.get_stats(x)
        self.update(x)
        return mean, std


class QualityEngine:
    def __init__(self, config: Optional[QualityConfig] = None):
        if config is None:
            try:
                from config import load_config
                self.cfg = load_config().quality
            except Exception:
                self.cfg = QualityConfig()
        else:
            self.cfg = config
        self._last_seq: Dict[Tuple[str, str], int] = {}
        self._last_ts: Dict[Tuple[str, str], float] = {}
        self._price_stats: Dict[Tuple[str, str], _RollingStats] = {}
        self._seen_keys: OrderedDict = OrderedDict()  # O(1) LRU
        # per-run counters for observability / benchmark scoring
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts: Dict[str, int] = {}

    # Priority map: never downgrade INVALID -> SUSPICIOUS or SUSPICIOUS -> VALID
    _STATUS_PRIORITY = {QualityStatus.VALID: 0, QualityStatus.SUSPICIOUS: 1, QualityStatus.INVALID: 2}

    def _mark(self, event: CanonicalEvent, status: QualityStatus, reason: Reason):
        if self._STATUS_PRIORITY[status] > self._STATUS_PRIORITY.get(event.quality_status, 0):
            event.quality_status = status
        event.reasons.append(reason.value)

    def _bump(self, reason: Reason):
        self.reason_counts[reason.value] = self.reason_counts.get(reason.value, 0) + 1

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        key = (event.source, event.instrument_id)

        # -- Duplicate detection (exact identity within the dedup window).
        dedup_key = event.dedup_key()
        if dedup_key in self._seen_keys:
            self._mark(event, QualityStatus.INVALID, Reason.DUPLICATE)
            self._bump(Reason.DUPLICATE)
        else:
            self._seen_keys[dedup_key] = None
            if len(self._seen_keys) > self.cfg.dedup_cache_size:
                self._seen_keys.popitem(last=False)

        # -- Sequence-gap detection.
        last_seq = self._last_seq.get(key)
        if event.sequence_number is not None:
            if last_seq is not None and event.sequence_number > last_seq + 1:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.SEQUENCE_GAP)
                self._bump(Reason.SEQUENCE_GAP)
            if last_seq is None or event.sequence_number > last_seq:
                self._last_seq[key] = event.sequence_number

        # -- Ordering detection (event time regression per source+instrument).
        last_ts = self._last_ts.get(key)
        if last_ts is not None and event.exchange_timestamp < last_ts:
            self._mark(event, QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER)
            self._bump(Reason.OUT_OF_ORDER)
        else:
            self._last_ts[key] = event.exchange_timestamp

        # Resolve instrument-specific config overrides (e.g. crypto vs equities)
        cfg = self.cfg.for_instrument(event.instrument_id) if hasattr(self.cfg, "for_instrument") else self.cfg

        # -- Numerical Validity Bounds (Strict Financial Data Validation)
        invalid_num = False
        if event.price is not None:
            if math.isnan(event.price) or math.isinf(event.price) or event.price < 0:
                self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
                self._bump(Reason.SCHEMA_VIOLATION)
                invalid_num = True

        if event.quantity is not None:
            if math.isnan(event.quantity) or math.isinf(event.quantity) or event.quantity < 0:
                self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
                self._bump(Reason.SCHEMA_VIOLATION)
                invalid_num = True

        if event.bid_price is not None and (math.isnan(event.bid_price) or math.isinf(event.bid_price) or event.bid_price < 0):
            self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
            self._bump(Reason.SCHEMA_VIOLATION)
            invalid_num = True

        if event.ask_price is not None and (math.isnan(event.ask_price) or math.isinf(event.ask_price) or event.ask_price < 0):
            self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
            self._bump(Reason.SCHEMA_VIOLATION)
            invalid_num = True

        # -- Staleness.
        if event.receive_timestamp - event.exchange_timestamp > cfg.staleness_threshold_s:
            self._mark(event, QualityStatus.SUSPICIOUS, Reason.STALE)
            self._bump(Reason.STALE)

        # -- Quote consistency: crossed book is structurally invalid.
        if event.bid_price is not None and event.ask_price is not None and not invalid_num:
            if event.bid_price > event.ask_price:
                self._mark(event, QualityStatus.INVALID, Reason.CROSSED_QUOTE)
                self._bump(Reason.CROSSED_QUOTE)

        # -- Price sanity (trades only): flag outliers, never auto-invalidate.
        # Only fold clean, finite, non-negative prices into rolling stats
        if event.price is not None and not invalid_num:
            stats = self._price_stats.setdefault(key, _RollingStats(cfg.price_window))
            mean, stddev = stats.get_stats(event.price)
            if stddev > 0 and abs(event.price - mean) > cfg.price_anomaly_stddev * stddev:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY)
                self._bump(Reason.PRICE_ANOMALY)
                # Outlier is flagged as SUSPICIOUS and clean baseline is preserved
            else:
                stats.update(event.price)

        self.counts[event.quality_status.value] += 1
        return event
