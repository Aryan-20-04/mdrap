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

from dataclasses import dataclass

from models import CanonicalEvent, QualityStatus, Reason


@dataclass
class ReliabilityConfig:
    """Configuration parameters for feed reliability scoring and windowing."""

    alpha: float = (
        0.02  # EWMA smoothing factor: S_t = (1 - alpha) * S_{t-1} + alpha * Y_t
    )
    disagreement_pct_threshold: float = (
        0.005  # 0.5% price divergence threshold for cross-feed conflict
    )
    agreement_window_s: float = (
        0.25  # Simulated market time window for concurrent observation
    )


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
        latency_penalty = 1.0 / (1.0 + self.ewma_latency_s * 100)
        return round(
            0.4 * accuracy
            + 0.25 * completeness
            + 0.2 * dup_penalty
            + 0.15 * latency_penalty,
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
        s = self.stats.setdefault(event.source, SourceStats(source=event.source))
        s.total += 1

        is_invalid = event.quality_status == QualityStatus.INVALID
        is_suspicious = event.quality_status == QualityStatus.SUSPICIOUS
        is_dup = Reason.DUPLICATE.value in event.reasons
        is_gap = Reason.SEQUENCE_GAP.value in event.reasons

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
    ):
        resolved_cfg = cfg or config or ReliabilityConfig()
        self.cfg = resolved_cfg
        self.reliability = (
            reliability
            if reliability is not None
            else ReliabilityTracker(config=resolved_cfg)
        )
        # instrument_id -> source -> (event, exchange_timestamp)
        self._latest: dict[str, dict[str, tuple[CanonicalEvent, float]]] = {}
        self._blocked_sources: set[str] = set()

    def block_source(self, source: str) -> None:
        """Block a feed from canonical selection (invoked by automated Watchdog)."""
        self._blocked_sources.add(source)

    def unblock_source(self, source: str) -> None:
        """Unblock a restored feed after health verification."""
        self._blocked_sources.discard(source)

    def reconcile(self, event: CanonicalEvent) -> CanonicalDecision | None:
        """Evaluate cross-feed agreement and elect the canonical event.

        Temporal Windowing Invariant:
          Recency is strictly evaluated using *simulated market time* (`exchange_timestamp`)
          rather than system wall-clock processing time. During historical replays or PCAP
          burst processing, tens of thousands of ticks are processed per real wall-clock second;
          using wall-clock timestamps would erroneously compare trades executed hours apart.
        """
        if event.quality_status == QualityStatus.INVALID or event.price is None:
            return None  # Only trade events with usable prices undergo pricing reconciliation

        market_now = event.exchange_timestamp
        per_instrument = self._latest.setdefault(event.instrument_id, {})
        per_instrument[event.source] = (event, market_now)

        # Filter for competitor feeds active within the agreement time window
        recent = {
            src: (ev, ts)
            for src, (ev, ts) in per_instrument.items()
            if abs(market_now - ts) <= self.cfg.agreement_window_s
            and src not in self._blocked_sources
        }
        if len(recent) < 2:
            return None  # Multi-feed quorum not yet established for this time window

        prices = {src: ev.price for src, (ev, _ts) in recent.items()}
        mean_price = sum(prices.values()) / len(prices)
        disagreement = any(
            abs(p - mean_price) / mean_price > self.cfg.disagreement_pct_threshold
            for p in prices.values()
            if mean_price
        )

        scores = self.reliability._cached_scores
        chosen_source = max(recent.keys(), key=lambda s: scores.get(s, 0.0))
        chosen_event = recent[chosen_source][0]

        if disagreement:
            chosen_event.reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
            reason = (
                f"disagreement across {list(recent.keys())} "
                f"(prices={prices}); selected highest-reliability source"
            )
        else:
            reason = (
                "sources agree within threshold; selected highest-reliability source"
            )

        return CanonicalDecision(
            instrument_id=event.instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=list(recent.keys()),
        )


CrossFeedReconciler = Reconciler
