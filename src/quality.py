"""Data-Quality Evaluation Engine.

This module provides stateful, per-(source, instrument) data validation checks applied
to every normalized CanonicalEvent in the processing pipeline. It evaluates and tags
each event with a QualityStatus (VALID, SUSPICIOUS, or INVALID) along with granular
Reason codes, strictly adhering to the architectural principle:
    "Never silently discard bad data — quarantine, never drop." (Spec §26)

Design Principles & Quality Invariants:
  1. Market Anomalies vs. Data Errors:
     A genuine market dislocation (e.g. flash crash, illiquid spike, sequence gap across
     lossy UDP channels) is not automatically a corrupt data event. Hence, sequence gaps,
     out-of-order packet arrivals, staleness, and statistical price outliers are classified
     as SUSPICIOUS (retained in canonical feeds, flagged, and audited).
  2. Structural Corruption:
     Only unparseable schema violations, mathematical absurdities (NaN, Infinity, negative
     prices), exact duplicated packets, and crossed bid-ask spreads (bid > ask) are
     classified as INVALID and excluded from canonical distribution.
  3. Status Monotonicity:
     Status follows a strict priority hierarchy: INVALID (2) > SUSPICIOUS (1) > VALID (0).
     An event's status can only escalate; it can NEVER be downgraded.
  4. Non-Contaminating Statistical Baselines:
     Rolling price statistics use Welford's online variance algorithm with explicit
     separation between baseline querying (`get_stats`) and window ingestion (`update`).
     Outlier prices are tagged as anomalous and excluded from mutating the clean baseline.
"""

from __future__ import annotations

import math
from collections import deque, OrderedDict
from dataclasses import dataclass

from models import CanonicalEvent, QualityStatus, Reason


@dataclass
class QualityConfig:
    """Configurable thresholds for data quality evaluation."""

    staleness_threshold_s: float = (
        0.05  # Ingress receive_ts - exchange_ts latency threshold
    )
    price_anomaly_stddev: float = (
        6.0  # Flag prices exceeding k * rolling standard deviations
    )
    price_window: int = 50  # Rolling sample window size per (source, instrument)
    dedup_cache_size: int = 50_000  # Bounded LRU deduplication window per feed gateway


class _RollingStats:
    """Fixed-window mean and standard deviation tracker using Welford's online algorithm.

    Mathematical Formulation:
      Standard sum-of-squares formulations ($E[X^2] - (E[X])^2$) suffer from catastrophic
      floating-point cancellation when variance is small relative to the mean.
      Welford's (1962) recurrence calculates sample mean $\\mu$ and squared deviations $M_2$:
        $$\\delta = x_n - \\mu_{n-1}$$
        $$\\mu_n = \\mu_{n-1} + \\frac{\\delta}{n}$$
        $$M_{2,n} = M_{2,n-1} + \\delta (x_n - \\mu_n)$$
        $$\\sigma_n = \\sqrt{\\frac{M_{2,n}}{n}}$$

      When the window capacity $W$ is exceeded, the oldest observation $x_{old}$ is popped:
        $$\\delta_{old} = x_{old} - \\mu_n$$
        $$\\mu_{n-1} = \\mu_n - \\frac{\\delta_{old}}{n-1}$$
        $$M_{2,n-1} = M_{2,n} - \\delta_{old} (x_{old} - \\mu_{n-1})$$

    Decoupled Baseline Invariant:
      `get_stats(x)` inspects the prior clean historical distribution $(\\mu, \\sigma)$
      WITHOUT modifying internal state. `update(x)` is only invoked after an event is
      proven to NOT be an outlier, preventing extreme flash spikes from distorting future checks.
    """

    __slots__ = ("window", "values", "_mean", "_m2", "_n")

    def __init__(self, window: int):
        self.window = window
        self.values: deque[float] = deque()
        self._mean = 0.0
        self._m2 = 0.0  # Sum of squared deviations from mean: sum((x - mean)^2)
        self._n = 0

    def get_stats(self, x: float) -> tuple[float, float]:
        """Return the prior clean baseline (mean, stddev) before x is evaluated."""
        if self._n == 0:
            return x, 0.0
        mean = self._mean
        var = self._m2 / self._n if self._n > 0 else 0.0
        return mean, math.sqrt(max(0.0, var))

    def update(self, x: float) -> None:
        """Fold valid observation x into the clean rolling baseline window."""
        self.values.append(x)
        self._n += 1
        delta = x - self._mean
        self._mean += delta / self._n
        self._m2 += delta * (x - self._mean)

        # Evict oldest entry when sliding window capacity is exceeded
        if self._n > self.window:
            old = self.values.popleft()
            self._n -= 1
            delta_old = old - self._mean
            self._mean -= delta_old / self._n if self._n > 0 else 0.0
            self._m2 -= delta_old * (old - self._mean)
            self._m2 = max(0.0, self._m2)

    def check_and_update(self, x: float) -> tuple[float, float]:
        """Backward-compatible helper returning prior baseline and updating state."""
        mean, std = self.get_stats(x)
        self.update(x)
        return mean, std


class QualityEngine:
    """Stateful market data quality assessment engine."""

    def __init__(self, config: QualityConfig | None = None):
        if config is None:
            try:
                from config import load_config

                self.cfg = load_config().quality
            except Exception as exc:
                import sys

                print(
                    f"[mdrap WARNING] Failed to load quality config: {exc}. Using defaults.",
                    file=sys.stderr,
                )
                self.cfg = QualityConfig()
        else:
            self.cfg = config

        self._last_seq: dict[tuple[str, str], int] = {}
        self._last_ts: dict[tuple[str, str], float] = {}
        self._price_stats: dict[tuple[str, str], _RollingStats] = {}
        self._seen_keys: OrderedDict = (
            OrderedDict()
        )  # O(1) LRU bounded deduplication cache

        # Operational counters for runtime observability and benchmark scoring
        self.counts = {"VALID": 0, "SUSPICIOUS": 0, "INVALID": 0}
        self.reason_counts: dict[str, int] = {}

    # Priority map: never downgrade INVALID -> SUSPICIOUS or SUSPICIOUS -> VALID
    _STATUS_PRIORITY = {
        QualityStatus.VALID: 0,
        QualityStatus.SUSPICIOUS: 1,
        QualityStatus.INVALID: 2,
    }

    def _mark(
        self, event: CanonicalEvent, status: QualityStatus, reason: Reason
    ) -> None:
        """Escalate event QualityStatus if proposed status has higher priority.

        Under MDRAP's non-downgrading rule, an INVALID event will retain its INVALID
        status even if subsequent checks identify SUSPICIOUS attributes. All encountered
        Reason codes are appended to the event's audit trail.
        """
        if self._STATUS_PRIORITY[status] > self._STATUS_PRIORITY.get(
            event.quality_status, 0
        ):
            event.quality_status = status
        event.reasons.append(reason.value)

    def _bump(self, reason: Reason) -> None:
        """Increment telemetry counter for the given Reason code."""
        self.reason_counts[reason.value] = self.reason_counts.get(reason.value, 0) + 1

    def evaluate(self, event: CanonicalEvent) -> CanonicalEvent:
        """Execute stateful data quality checks across all dimensions.

        Processing Pipeline Stages:
          1. Exact Duplicate Filtering (LRU cache check).
          2. Sequence Continuity Validation (monotonic counter gap analysis).
          3. Exchange Timestamp Monotonicity (out-of-order packet arrival detection).
          4. IEEE-754 Floating-Point Sanity (NaN, Inf, negative value rejection).
          5. Transmission Staleness Assessment (receive_ts - exchange_ts latency threshold).
          6. Microstructure Order Book Consistency (crossed quote bid > ask check).
          7. Statistical Price Corridor Evaluation (Welford rolling stddev & venue circuit bands).
        """
        key = (event.source, event.instrument_id)

        # Stage 1: Exact Duplicate Detection
        # Events sharing an identical dedup_key within the sliding LRU cache window are
        # marked INVALID. This prevents double-counting volume or re-executing strategy fills.
        dedup_hash = hash(event.dedup_key())
        if dedup_hash in self._seen_keys:
            self._mark(event, QualityStatus.INVALID, Reason.DUPLICATE)
            self._bump(Reason.DUPLICATE)
        else:
            self._seen_keys[dedup_hash] = None
            if len(self._seen_keys) > self.cfg.dedup_cache_size:
                # Evict oldest entry in O(1) time
                self._seen_keys.popitem(last=False)

        # Stage 2: Sequence Gap Detection
        # A jump in the sequence number indicates dropped UDP multicast packets or feed drops.
        # Marked SUSPICIOUS rather than INVALID because the received packet itself contains
        # valid state, even if intermediate ticks were lost.
        last_seq = self._last_seq.get(key)
        if event.sequence_number is not None:
            if last_seq is not None and event.sequence_number > last_seq + 1:
                self._mark(event, QualityStatus.SUSPICIOUS, Reason.SEQUENCE_GAP)
                self._bump(Reason.SEQUENCE_GAP)
            if last_seq is None or event.sequence_number > last_seq:
                self._last_seq[key] = event.sequence_number

        # Stage 3: Exchange Timestamp Monotonicity (Out-of-Order Packet Arrival)
        # Network jitter or multi-path routing can cause packets to arrive out of exchange order.
        # Flagged as SUSPICIOUS to alert downstream consumers while preserving event payload.
        last_ts = self._last_ts.get(key)
        if last_ts is not None and event.exchange_timestamp < last_ts:
            self._mark(event, QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER)
            self._bump(Reason.OUT_OF_ORDER)
        else:
            self._last_ts[key] = event.exchange_timestamp

        # Resolve instrument-specific config overrides (e.g. crypto vs equities)
        cfg = (
            self.cfg.for_instrument(event.instrument_id)
            if hasattr(self.cfg, "for_instrument")
            else self.cfg
        )

        # Stage 4: Strict Numerical Validity Bounds
        # Any non-finite (NaN, Infinity) or non-positive price/size violates fundamental
        # financial invariants and is marked INVALID.
        invalid_num = False
        if event.price is not None:
            if math.isnan(event.price) or math.isinf(event.price) or event.price < 0:
                self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
                self._bump(Reason.SCHEMA_VIOLATION)
                invalid_num = True

        if event.quantity is not None:
            if (
                math.isnan(event.quantity)
                or math.isinf(event.quantity)
                or event.quantity < 0
            ):
                self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
                self._bump(Reason.SCHEMA_VIOLATION)
                invalid_num = True

        if event.bid_price is not None and (
            math.isnan(event.bid_price)
            or math.isinf(event.bid_price)
            or event.bid_price < 0
        ):
            self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
            self._bump(Reason.SCHEMA_VIOLATION)
            invalid_num = True

        if event.ask_price is not None and (
            math.isnan(event.ask_price)
            or math.isinf(event.ask_price)
            or event.ask_price < 0
        ):
            self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
            self._bump(Reason.SCHEMA_VIOLATION)
            invalid_num = True

        # Stage 5: Ingress Latency / Staleness Evaluation
        # If transmission latency (receive_timestamp - exchange_timestamp) exceeds the
        # configured staleness threshold, mark as SUSPICIOUS.
        if (
            event.receive_timestamp - event.exchange_timestamp
            > cfg.staleness_threshold_s
        ):
            self._mark(event, QualityStatus.SUSPICIOUS, Reason.STALE)
            self._bump(Reason.STALE)

        # Stage 6: Quote Consistency (Crossed Book Detection)
        # In a single-venue direct feed, bid > ask represents a structural quote violation.
        # Marked INVALID because a crossed quote cannot represent an orderly market.
        if (
            event.bid_price is not None
            and event.ask_price is not None
            and not invalid_num
        ):
            if event.bid_price > event.ask_price:
                self._mark(event, QualityStatus.INVALID, Reason.CROSSED_QUOTE)
                self._bump(Reason.CROSSED_QUOTE)

        # Stage 7: Statistical Price Corridor & Microstructure Bands (Trades Only)
        # Evaluates whether the trade price exceeds k * rolling standard deviations from
        # the Welford baseline, or breaches international venue circuit limit filters.
        if event.price is not None and not invalid_num:
            stats = self._price_stats.setdefault(key, _RollingStats(cfg.price_window))
            mean, stddev = stats.get_stats(event.price)
            is_anomaly = (
                stddev > 0
                and abs(event.price - mean) > cfg.price_anomaly_stddev * stddev
            ) or (
                stddev == 0.0
                and mean > 0.0
                and stats._n >= 3
                and abs(event.price - mean) / mean > 0.10
            )
            if is_anomaly:
                pct_move = abs(event.price - mean) / mean if mean > 0 else 0.0
                # NSE / BSE: Circuit filter band limits (±10%)
                if getattr(event, "venue", "") in ("XNSE", "XBOM") and pct_move >= 0.10:
                    self._mark(
                        event, QualityStatus.SUSPICIOUS, Reason.CIRCUIT_FILTER_BREACH
                    )
                    self._bump(Reason.CIRCUIT_FILTER_BREACH)
                # Deutsche Börse Xetra: Dynamic volatility interruption corridor (±5%)
                elif (
                    getattr(event, "venue", "") in ("XETR", "XEUR") and pct_move >= 0.05
                ):
                    self._mark(
                        event, QualityStatus.SUSPICIOUS, Reason.VOLATILITY_INTERRUPTION
                    )
                    self._bump(Reason.VOLATILITY_INTERRUPTION)
                # Tokyo Stock Exchange (TSE): Special Quote Indication (Tokuhai ±8%)
                elif (
                    getattr(event, "venue", "") in ("XTKS", "XOSE") and pct_move >= 0.08
                ):
                    self._mark(
                        event, QualityStatus.SUSPICIOUS, Reason.SPECIAL_QUOTE_INDICATION
                    )
                    self._bump(Reason.SPECIAL_QUOTE_INDICATION)
                else:
                    self._mark(event, QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY)
                    self._bump(Reason.PRICE_ANOMALY)
                # CRITICAL INVARIANT: The anomalous price is NOT added to stats, preserving
                # the clean baseline for subsequent evaluation.
            else:
                # Valid price: fold into rolling baseline
                stats.update(event.price)

        try:
            from rules import _USER_RULES, evaluate_user_rules
            if _USER_RULES:
                event = evaluate_user_rules(event)
        except ImportError:
            pass

        self.counts[event.quality_status.value] += 1
        return event


# Phase 5: Explicit Two-Tier Architecture Aliases
PythonQualityEngine = QualityEngine
