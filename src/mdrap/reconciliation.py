"""Cross-Feed Reconciliation, Source Reliability Scoring, and Conflict Resolution.

In institutional market data infrastructure, multiple feeds (e.g. direct exchange ITCH,
SIP consolidated, proprietary vendor feeds) stream concurrent observations for the same
tradable instrument. Due to distinct physical network paths, switch queueing, or vendor
processing lags, prices and timestamps can diverge.

Architectural Objectives (MDRAP Spec §12 & §26):
  1. Real-Time Disagreement Detection:
     Compare concurrent observations across sources within an exchange-time temporal window.
     If relative price variance exceeds `disagreement_pct_threshold` (default 0.5%), a
     cross-feed conflict is flagged.
  2. Dynamic Reliability Attribution:
     Maintain continuous Exponential Weighted Moving Average (EWMA) scores for each feed
     tracking packet loss, invalid schemas, duplicates, and ingress latency.
  3. Deterministic Canonical Selection:
     When multiple competing feeds offer valid observations, the feed with the highest
     composite reliability score is selected as the canonical source.
  4. Immutable Audit Lineage:
     Every reconciliation decision explicitly documents participating sources, competing
     prices, and the selection rationale for auditability and compliance reporting.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .models import CanonicalEvent, QualityStatus, Reason

__stability__ = "stable"


# ---------------------------------------------------------------------------
# Feed Reliability Scoring Weights & Windows (Spec §12 & §26)
# ---------------------------------------------------------------------------
DEFAULT_RELIABILITY_ALPHA: float = (
    0.02  # EWMA smoothing factor: S_t = (1 - alpha) * S_{t-1} + alpha * Y_t
)
DEFAULT_DISAGREEMENT_PCT_THRESHOLD: float = (
    0.005  # 0.5% price divergence threshold for cross-feed conflict
)
DEFAULT_AGREEMENT_WINDOW_S: float = (
    0.25  # Simulated market time window for concurrent observation
)

WEIGHT_ACCURACY: float = 0.40
WEIGHT_COMPLETENESS: float = 0.25
WEIGHT_DEDUP: float = 0.20
WEIGHT_LATENCY: float = 0.15
LATENCY_PENALTY_FACTOR: float = 100.0


@dataclass
class ReliabilityConfig:
    """Configuration parameters for feed reliability scoring and windowing."""

    alpha: float = DEFAULT_RELIABILITY_ALPHA
    disagreement_pct_threshold: float = DEFAULT_DISAGREEMENT_PCT_THRESHOLD
    agreement_window_s: float = DEFAULT_AGREEMENT_WINDOW_S


ReconciliationConfig = ReliabilityConfig


@dataclass
class SourceStats:
    """Continuous reliability metrics and EWMA telemetry for an upstream feed."""

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
    def error_rate(self) -> float:
        """Current smoothed EWMA error rate."""
        return self.ewma_error_rate if self.total > 0 else 0.0

    @property
    def score(self) -> float:
        """Composite reliability score normalized to [0.0, 1.0].

        Mathematical Derivation:
          The composite score balances accuracy, sequence continuity, deduplication,
          and network latency:
            $$Score = 0.40 \\cdot A + 0.25 \\cdot C + 0.20 \\cdot D + 0.15 \\cdot L$$

          Where:
            - Accuracy ($A$): $1.0 - \\min(1.0, \\text{EWMA}_{\\text{error}})$
            - Completeness ($C$): $1.0 - \\min(1.0, \\text{EWMA}_{\\text{gap}})$
            - Dedup Quality ($D$): $1.0 - \\min(1.0, \\text{EWMA}_{\\text{dup}})$
            - Latency Penalty ($L$): $\\frac{1.0}{1.0 + 100 \\cdot \\text{EWMA}_{\\text{latency}}}$

        Why EWMA instead of Cumulative Counters:
          Using EWMA with $\\alpha = 0.02$ provides an effective memory horizon of
          roughly $1 / \\alpha = 50$ ticks. If a feed suddenly experiences packet loss
          or corruption, its score drops immediately rather than being masked by millions
          of historic clean ticks.
        """
        if self.total == 0:
            return 1.0
        completeness = max(0.0, 1.0 - min(1.0, self.ewma_gap_rate))
        accuracy = max(0.0, 1.0 - min(1.0, self.ewma_error_rate))
        dup_penalty = max(0.0, 1.0 - min(1.0, self.ewma_dup_rate))
        latency_penalty = 1.0 / (1.0 + self.ewma_latency_s * LATENCY_PENALTY_FACTOR)
        return round(
            WEIGHT_ACCURACY * accuracy
            + WEIGHT_COMPLETENESS * completeness
            + WEIGHT_DEDUP * dup_penalty
            + WEIGHT_LATENCY * latency_penalty,
            4,
        )


class ReliabilityTracker:
    """Tracks running quality statistics and dynamic reliability scores per feed."""

    def __init__(self, config: ReliabilityConfig | None = None):
        self.cfg = config or ReliabilityConfig()
        self.stats: dict[str, SourceStats] = {}
        self._cached_scores: dict[str, float] = {}

    def observe(self, event: CanonicalEvent) -> None:
        """Update EWMA telemetry and score cache with the latest CanonicalEvent."""
        s = self.stats.get(event.source)
        if s is None:
            s = self.stats[event.source] = SourceStats(source=event.source)
        s.total += 1

        is_invalid = event.quality_status == QualityStatus.INVALID
        is_suspicious = event.quality_status == QualityStatus.SUSPICIOUS
        if event.reasons:
            is_dup = Reason.DUPLICATE.value in event.reasons
            is_gap = Reason.SEQUENCE_GAP.value in event.reasons
        else:
            is_dup = False
            is_gap = False

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

    def scores(self) -> dict[str, float]:
        """Return a copy of the latest feed reliability score map."""
        return self._cached_scores.copy()


@dataclass
class CanonicalDecision:
    """Provenance record documenting a multi-feed canonical choice."""

    instrument_id: str
    chosen_source: str
    chosen_event_id: str
    disagreement: bool
    reason: str
    competing_sources: list
    quality_reasons: list[str] = field(default_factory=list)


@runtime_checkable
class ReconciliationStrategy(Protocol):
    """Protocol defining multi-feed arbitration and canonical selection strategies."""

    def select(
        self,
        instrument_id: str,
        candidates: dict[str, tuple[CanonicalEvent, float]],
        scores: dict[str, float],
        agreement_window_s: float,
        disagreement_pct_threshold: float,
        blocked_sources: set[str] | None = None,
        market_now: float | None = None,
    ) -> CanonicalDecision | None:
        """Arbitrate between competing candidate observations to select canonical reality."""
        ...


class HighestReliabilityStrategy:
    """Canonical selection strategy electing the feed with highest composite reliability score.

    Default production strategy in MDRAP.
    """

    def select(
        self,
        instrument_id: str,
        candidates: dict[str, tuple[CanonicalEvent, float]],
        scores: dict[str, float],
        agreement_window_s: float,
        disagreement_pct_threshold: float,
        blocked_sources: set[str] | None = None,
        market_now: float | None = None,
    ) -> CanonicalDecision | None:
        blocked = blocked_sources or set()
        recent_sources: list[str] = []
        recent_prices: dict[str, float] = {}
        chosen_source = ""
        chosen_event = None
        best_score = -1.0
        total_price = 0.0

        for src in sorted(candidates.keys()):
            ev, ts = candidates[src]
            if market_now is not None and abs(market_now - ts) > agreement_window_s:
                continue
            if src in blocked or ev.price is None:
                continue
            p = ev.price
            recent_sources.append(src)
            recent_prices[src] = p
            total_price += p
            sc = scores.get(src, 0.0)
            if sc > best_score or (
                sc == best_score and (chosen_source == "" or src < chosen_source)
            ):
                best_score = sc
                chosen_source = src
                chosen_event = ev

        n_recent = len(recent_sources)
        if n_recent < 2 or chosen_event is None:
            return None

        mean_price = total_price / n_recent
        disagreement = False
        if mean_price > 0:
            for p in recent_prices.values():
                if abs(p - mean_price) / mean_price > disagreement_pct_threshold:
                    disagreement = True
                    break

        quality_reasons: list[str] = []
        if disagreement:
            quality_reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
            reason = (
                f"disagreement across {recent_sources} "
                f"(prices={recent_prices}); selected highest-reliability source"
            )
        else:
            reason = (
                "sources agree within threshold; selected highest-reliability source"
            )

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
        )


class MedianPriceStrategy:
    """Outlier-resistant canonical selection strategy electing the feed with the median price.

    Particularly effective in hostile or noisy multi-feed environments where a single
    rogue feed may broadcast erroneous or manipulated price spikes. Selects the observation
    whose price is closest to the multi-feed median.
    """

    def select(
        self,
        instrument_id: str,
        candidates: dict[str, tuple[CanonicalEvent, float]],
        scores: dict[str, float],
        agreement_window_s: float,
        disagreement_pct_threshold: float,
        blocked_sources: set[str] | None = None,
        market_now: float | None = None,
    ) -> CanonicalDecision | None:
        blocked = blocked_sources or set()
        active_items: list[tuple[str, CanonicalEvent, float]] = []

        for src in sorted(candidates.keys()):
            ev, ts = candidates[src]
            if market_now is not None and abs(market_now - ts) > agreement_window_s:
                continue
            if src in blocked or ev.price is None:
                continue
            active_items.append((src, ev, ev.price))

        n_recent = len(active_items)
        if n_recent < 2:
            return None

        # Sort by price ascending; tie-break by source name
        active_items.sort(key=lambda item: (item[2], item[0]))
        recent_sources = [item[0] for item in active_items]
        recent_prices = {item[0]: item[2] for item in active_items}

        prices = [item[2] for item in active_items]
        mid = n_recent // 2
        if n_recent % 2 == 1:
            median_price = prices[mid]
            chosen_idx = mid
        else:
            median_price = (prices[mid - 1] + prices[mid]) / 2.0
            dist_left = abs(prices[mid - 1] - median_price)
            dist_right = abs(prices[mid] - median_price)
            if dist_left < dist_right:
                chosen_idx = mid - 1
            elif dist_right < dist_left:
                chosen_idx = mid
            else:
                score_left = scores.get(active_items[mid - 1][0], 0.0)
                score_right = scores.get(active_items[mid][0], 0.0)
                chosen_idx = mid - 1 if score_left >= score_right else mid

        chosen_source, chosen_event, _ = active_items[chosen_idx]

        disagreement = False
        if median_price > 0:
            for p in prices:
                if abs(p - median_price) / median_price > disagreement_pct_threshold:
                    disagreement = True
                    break

        quality_reasons: list[str] = []
        if disagreement:
            quality_reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
            reason = (
                f"disagreement across {recent_sources} "
                f"(prices={recent_prices}); selected median-price source {chosen_source}"
            )
        else:
            reason = f"sources agree within threshold; selected median-price source {chosen_source}"

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
        )


class Reconciler:
    """Arbitrates concurrent multi-source quotes and trades to establish canonical reality.

    Maintains the most recent non-invalid observation for each (instrument, source) pair.
    When a new event arrives, recent observations from competing sources within the
    configured agreement window are compared.
    """

    def __init__(
        self,
        reliability: ReliabilityTracker | None = None,
        config: ReliabilityConfig | None = None,
        cfg: ReliabilityConfig | None = None,
        max_instruments: int = 100_000,
        strategy: ReconciliationStrategy | None = None,
    ):
        resolved_cfg = cfg or config or ReliabilityConfig()
        self.cfg = resolved_cfg
        self.reliability = (
            reliability
            if reliability is not None
            else ReliabilityTracker(config=resolved_cfg)
        )
        self.max_instruments = max_instruments
        self.strategy: ReconciliationStrategy = (
            strategy if strategy is not None else HighestReliabilityStrategy()
        )
        # instrument_id -> source -> (event, exchange_timestamp)
        self._latest: OrderedDict[str, dict[str, tuple[CanonicalEvent, float]]] = (
            OrderedDict()
        )
        self._blocked_sources: set[str] = set()

    def block_source(self, source: str) -> None:
        """Block a feed from canonical selection (invoked by automated Watchdog)."""
        self._blocked_sources.add(source)

    def unblock_source(self, source: str) -> None:
        """Unblock a restored feed after health verification."""
        self._blocked_sources.discard(source)

    def reconcile(self, event: CanonicalEvent) -> CanonicalDecision | None:
        """Evaluate cross-feed agreement and elect the canonical event using configured strategy.

        Temporal Windowing Invariant:
          Recency is strictly evaluated using *simulated market time* (`exchange_timestamp`)
          rather than system wall-clock processing time. During historical replays or PCAP
          burst processing, tens of thousands of ticks are processed per real wall-clock second;
          using wall-clock timestamps would erroneously compare trades executed hours apart.
        """
        if event.quality_status == QualityStatus.INVALID or event.price is None:
            return None  # Only trade events with usable prices undergo pricing reconciliation

        market_now = event.exchange_timestamp
        per_instrument = self._latest.get(event.instrument_id)
        if per_instrument is None:
            if len(self._latest) >= self.max_instruments:
                self._latest.popitem(last=False)
            per_instrument = self._latest[event.instrument_id] = {}
        else:
            self._latest.move_to_end(event.instrument_id)
        per_instrument[event.source] = (event, market_now)

        # Fast path: multi-feed quorum cannot be established with < 2 feeds
        if len(per_instrument) < 2:
            return None

        return self.strategy.select(
            instrument_id=event.instrument_id,
            candidates=per_instrument,
            scores=self.reliability._cached_scores,
            agreement_window_s=self.cfg.agreement_window_s,
            disagreement_pct_threshold=self.cfg.disagreement_pct_threshold,
            blocked_sources=self._blocked_sources,
            market_now=market_now,
        )


CrossFeedReconciler = Reconciler
