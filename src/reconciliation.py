"""
Cross-Feed Reconciliation, Source Reliability Scoring, Conflict Resolution.

For each instrument, multiple sources publish observations of "the
same" market. This module compares recent observations across sources,
flags disagreement, and picks a canonical value using a live-updated
per-source reliability score. Every canonical decision records *why*
it was made -- that record is what lineage.py persists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from models import CanonicalEvent, QualityStatus, Reason


@dataclass
class ReliabilityConfig:
    # Exponential-moving-average smoothing for each component metric.
    alpha: float = 0.02
    disagreement_pct_threshold: float = 0.005  # 0.5% price divergence flags disagreement
    agreement_window_s: float = 0.25            # how recent a cross-source obs must be to compare


@dataclass
class SourceStats:
    source: str
    total: int = 0
    invalid: int = 0
    suspicious: int = 0
    duplicate: int = 0
    gap: int = 0
    ewma_latency_s: float = 0.0
    ewma_error_rate: float = 0.0
    ewma_gap_rate: float = 0.0
    ewma_dup_rate: float = 0.0

    @property
    def cumulative_error_rate(self) -> float:
        return 0.0 if self.total == 0 else (self.invalid + self.suspicious) / self.total

    @property
    def error_rate(self) -> float:
        return self.ewma_error_rate if self.total > 0 else 0.0

    @property
    def score(self) -> float:
        """Composite reliability score in [0, 1]. Higher is better.
        Uses EWMA rates to ensure responsiveness to sudden degradation
        rather than diluting over large cumulative historical counts."""
        if self.total == 0:
            return 1.0
        completeness = max(0.0, 1.0 - min(1.0, self.ewma_gap_rate))
        accuracy = max(0.0, 1.0 - min(1.0, self.ewma_error_rate))
        dup_penalty = max(0.0, 1.0 - min(1.0, self.ewma_dup_rate))
        latency_penalty = 1.0 / (1.0 + self.ewma_latency_s * 100)
        return round(0.4 * accuracy + 0.25 * completeness + 0.2 * dup_penalty + 0.15 * latency_penalty, 4)


class ReliabilityTracker:
    def __init__(self, config: Optional[ReliabilityConfig] = None):
        self.cfg = config or ReliabilityConfig()
        self.stats: Dict[str, SourceStats] = {}
        self._cached_scores: Dict[str, float] = {}

    def observe(self, event: CanonicalEvent):
        s = self.stats.setdefault(event.source, SourceStats(source=event.source))
        s.total += 1

        is_invalid = (event.quality_status == QualityStatus.INVALID)
        is_suspicious = (event.quality_status == QualityStatus.SUSPICIOUS)
        is_dup = (Reason.DUPLICATE.value in event.reasons)
        is_gap = (Reason.SEQUENCE_GAP.value in event.reasons)

        if is_invalid:
            s.invalid += 1
            if is_dup:
                s.duplicate += 1
        if is_suspicious:
            s.suspicious += 1
            if is_gap:
                s.gap += 1

        is_error = 1.0 if (is_invalid or is_suspicious) else 0.0
        gap_val = 1.0 if is_gap else 0.0
        dup_val = 1.0 if is_dup else 0.0

        latency = max(0.0, event.receive_timestamp - event.exchange_timestamp)
        a = self.cfg.alpha
        if s.total == 1:
            s.ewma_latency_s = latency
            s.ewma_error_rate = is_error
            s.ewma_gap_rate = gap_val
            s.ewma_dup_rate = dup_val
        else:
            s.ewma_latency_s = (1.0 - a) * s.ewma_latency_s + a * latency
            s.ewma_error_rate = (1.0 - a) * s.ewma_error_rate + a * is_error
            s.ewma_gap_rate = (1.0 - a) * s.ewma_gap_rate + a * gap_val
            s.ewma_dup_rate = (1.0 - a) * s.ewma_dup_rate + a * dup_val

        self._cached_scores[event.source] = s.score

    def scores(self) -> Dict[str, float]:
        return self._cached_scores.copy()


@dataclass
class CanonicalDecision:
    instrument_id: str
    chosen_source: str
    chosen_event_id: str
    disagreement: bool
    reason: str
    competing_sources: list


class Reconciler:
    """Keeps the latest VALID/SUSPICIOUS observation per (instrument,
    source) and, on each new event, decides the canonical value for
    that instrument across all currently-agreeing/disagreeing sources.
    """

    def __init__(
        self,
        reliability: Optional[ReliabilityTracker] = None,
        config: Optional[ReliabilityConfig] = None,
        cfg: Optional[ReliabilityConfig] = None,
    ):
        resolved_cfg = cfg or config or ReliabilityConfig()
        self.cfg = resolved_cfg
        self.reliability = reliability if reliability is not None else ReliabilityTracker(config=resolved_cfg)
        # instrument -> source -> (event, observed_at_wallclock)
        self._latest: Dict[str, Dict[str, tuple]] = {}
        self._blocked_sources: set[str] = set()

    def block_source(self, source: str) -> None:
        """Block a source from being chosen as canonical."""
        self._blocked_sources.add(source)

    def unblock_source(self, source: str) -> None:
        """Unblock a previously blocked source."""
        self._blocked_sources.discard(source)

    def reconcile(self, event: CanonicalEvent) -> Optional[CanonicalDecision]:
        if event.quality_status == QualityStatus.INVALID or event.price is None:
            return None  # only trades with a usable price get reconciled in this MVP

        # Recency is judged by *simulated market time* (exchange_timestamp),
        # not wall-clock processing time -- at replay speed, thousands of
        # events can be processed per real second, so wall-clock windowing
        # would treat far-apart market moments as "concurrent".
        market_now = event.exchange_timestamp
        per_instrument = self._latest.setdefault(event.instrument_id, {})
        per_instrument[event.source] = (event, market_now)

        # Consider only sources heard from recently (in simulated time),
        # excluding any sources blocked by the watchdog.
        recent = {
            src: (ev, ts) for src, (ev, ts) in per_instrument.items()
            if abs(market_now - ts) <= self.cfg.agreement_window_s
            and src not in self._blocked_sources
        }
        if len(recent) < 2:
            return None  # nothing to reconcile against yet

        prices = {src: ev.price for src, (ev, _ts) in recent.items()}
        mean_price = sum(prices.values()) / len(prices)
        disagreement = any(
            abs(p - mean_price) / mean_price > self.cfg.disagreement_pct_threshold
            for p in prices.values() if mean_price
        )

        scores = self.reliability._cached_scores
        chosen_source = max(recent.keys(), key=lambda s: scores.get(s, 0.0))
        chosen_event = recent[chosen_source][0]

        if disagreement:
            chosen_event.reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
            reason = (f"disagreement across {list(recent.keys())} "
                      f"(prices={prices}); selected highest-reliability source")
        else:
            reason = f"sources agree within threshold; selected highest-reliability source"

        return CanonicalDecision(
            instrument_id=event.instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=list(recent.keys()),
        )


CrossFeedReconciler = Reconciler
