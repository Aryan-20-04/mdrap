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


# ---------------------------------------------------------------------------
# Feed Reliability Scoring Weights & Windows (Spec §12 & §26)
# ---------------------------------------------------------------------------
DEFAULT_RELIABILITY_ALPHA: float = 0.02  # EWMA smoothing factor: S_t = (1 - alpha) * S_{t-1} + alpha * Y_t
DEFAULT_DISAGREEMENT_PCT_THRESHOLD: float = 0.005  # 0.5% price divergence threshold for cross-feed conflict
DEFAULT_AGREEMENT_WINDOW_S: float = 0.25  # Simulated market time window for concurrent observation

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
        per_instrument = self._latest.get(event.instrument_id)
        if per_instrument is None:
            per_instrument = self._latest[event.instrument_id] = {}
        per_instrument[event.source] = (event, market_now)

        # Fast path: multi-feed quorum cannot be established with < 2 feeds
        if len(per_instrument) < 2:
            return None

        window = self.cfg.agreement_window_s
        blocked = self._blocked_sources
        scores = self.reliability._cached_scores

        recent_sources: list[str] = []
        recent_prices: dict[str, float] = {}
        chosen_source = ""
        chosen_event = None
        best_score = -1.0
        total_price = 0.0

        for src, (ev, ts) in per_instrument.items():
            if abs(market_now - ts) <= window and src not in blocked:
                p = ev.price
                recent_sources.append(src)
                recent_prices[src] = p
                total_price += p
                sc = scores.get(src, 0.0)
                if sc > best_score:
                    best_score = sc
                    chosen_source = src
                    chosen_event = ev

        n_recent = len(recent_sources)
        if n_recent < 2 or chosen_event is None:
            return None

        mean_price = total_price / n_recent
        thresh = self.cfg.disagreement_pct_threshold
        disagreement = False
        if mean_price > 0:
            for p in recent_prices.values():
                if abs(p - mean_price) / mean_price > thresh:
                    disagreement = True
                    break

        if disagreement:
            chosen_event.reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)
            reason = (
                f"disagreement across {recent_sources} "
                f"(prices={recent_prices}); selected highest-reliability source"
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
            competing_sources=recent_sources,
        )


CrossFeedReconciler = Reconciler
