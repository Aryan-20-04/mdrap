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

from .models import CanonicalEvent, EventType, QualityStatus, Reason

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
    lead_source: str = ""
    lead_latency_delta_ms: float = 0.0
    reconciliation_type: str = "TRADE"  # "TRADE" or "QUOTE"
    bid_disagreement: bool = False
    ask_disagreement: bool = False
    spread_disagreement: bool = False
    is_crossed_book: bool = False


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
        # Check if candidates are quote events; delegate to select_quote if so
        is_quote = any(
            ev.event_type == EventType.QUOTE
            or (ev.price is None and ev.bid_price is not None and ev.ask_price is not None)
            for ev, _ in candidates.values()
        )
        if is_quote:
            return self.select_quote(
                instrument_id=instrument_id,
                candidates=candidates,
                scores=scores,
                agreement_window_s=agreement_window_s,
                disagreement_pct_threshold=disagreement_pct_threshold,
                blocked_sources=blocked_sources,
                market_now=market_now,
            )

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

        lead_source = ""
        lead_latency_delta_ms = 0.0
        if recent_sources:
            earliest_src = min(recent_sources, key=lambda s: candidates[s][1])
            lead_source = earliest_src
            earliest_ts = candidates[earliest_src][1]
            if market_now is not None:
                lead_latency_delta_ms = max(0.0, (market_now - earliest_ts) * 1000.0)

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
            lead_source=lead_source,
            lead_latency_delta_ms=round(lead_latency_delta_ms, 3),
            reconciliation_type="TRADE",
        )

    def select_quote(
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
        active_quotes: dict[str, CanonicalEvent] = {}

        for src in sorted(candidates.keys()):
            ev, ts = candidates[src]
            if market_now is not None and abs(market_now - ts) > agreement_window_s:
                continue
            if src in blocked or ev.bid_price is None or ev.ask_price is None:
                continue
            active_quotes[src] = ev

        n_recent = len(active_quotes)
        if n_recent < 2:
            return None

        recent_sources = sorted(active_quotes.keys())
        bids = {src: ev.bid_price for src, ev in active_quotes.items() if ev.bid_price is not None}
        asks = {src: ev.ask_price for src, ev in active_quotes.items() if ev.ask_price is not None}
        spreads = {
            src: (ev.ask_price - ev.bid_price)
            for src, ev in active_quotes.items()
            if ev.ask_price is not None and ev.bid_price is not None
        }

        mean_bid = sum(bids.values()) / len(bids) if bids else 0.0
        mean_ask = sum(asks.values()) / len(asks) if asks else 0.0
        mean_spread = sum(spreads.values()) / len(spreads) if spreads else 0.0

        bid_disagreement = False
        if mean_bid > 0:
            for b in bids.values():
                if abs(b - mean_bid) / mean_bid > disagreement_pct_threshold:
                    bid_disagreement = True
                    break

        ask_disagreement = False
        if mean_ask > 0:
            for a in asks.values():
                if abs(a - mean_ask) / mean_ask > disagreement_pct_threshold:
                    ask_disagreement = True
                    break

        spread_disagreement = False
        if mean_spread > 0:
            for s in spreads.values():
                if abs(s - mean_spread) / mean_spread > disagreement_pct_threshold:
                    spread_disagreement = True
                    break

        individual_crossed = any(
            (ev.bid_price is not None and ev.ask_price is not None and ev.bid_price > ev.ask_price)
            for ev in active_quotes.values()
        )
        cross_venue_crossed = (max(bids.values()) > min(asks.values())) if bids and asks else False
        is_crossed_book = individual_crossed or cross_venue_crossed

        disagreement = bid_disagreement or ask_disagreement or is_crossed_book

        quality_reasons: list[str] = []
        if is_crossed_book:
            quality_reasons.append(Reason.CROSSED_QUOTE.value)
        if bid_disagreement or ask_disagreement:
            quality_reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)

        # Primary: highest score
        # Tie-breaker 1: tighter spread (lower ask - bid)
        # Tie-breaker 2: deeper size (bid_size + ask_size)
        # Tie-breaker 3: deterministic source name ascending
        def quote_key(src: str):
            ev = active_quotes[src]
            score = scores.get(src, 0.0)
            spread = (
                (ev.ask_price - ev.bid_price)
                if (ev.ask_price is not None and ev.bid_price is not None)
                else float("inf")
            )
            total_size = (ev.bid_size or 0.0) + (ev.ask_size or 0.0)
            return (-score, spread, -total_size, src)

        chosen_source = min(recent_sources, key=quote_key)
        chosen_event = active_quotes[chosen_source]

        if disagreement:
            reason = (
                f"quote disagreement across {recent_sources} "
                f"(bids={bids}, asks={asks}, crossed={is_crossed_book}); selected highest-reliability quote"
            )
        else:
            reason = "quote sources agree within threshold; selected highest-reliability quote"

        lead_source = ""
        lead_latency_delta_ms = 0.0
        if recent_sources:
            earliest_src = min(recent_sources, key=lambda s: candidates[s][1])
            lead_source = earliest_src
            earliest_ts = candidates[earliest_src][1]
            if market_now is not None:
                lead_latency_delta_ms = max(0.0, (market_now - earliest_ts) * 1000.0)

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
            lead_source=lead_source,
            lead_latency_delta_ms=round(lead_latency_delta_ms, 3),
            reconciliation_type="QUOTE",
            bid_disagreement=bid_disagreement,
            ask_disagreement=ask_disagreement,
            spread_disagreement=spread_disagreement,
            is_crossed_book=is_crossed_book,
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
        # Check if candidates are quote events; delegate to select_quote if so
        is_quote = any(
            ev.event_type == EventType.QUOTE
            or (ev.price is None and ev.bid_price is not None and ev.ask_price is not None)
            for ev, _ in candidates.values()
        )
        if is_quote:
            return self.select_quote(
                instrument_id=instrument_id,
                candidates=candidates,
                scores=scores,
                agreement_window_s=agreement_window_s,
                disagreement_pct_threshold=disagreement_pct_threshold,
                blocked_sources=blocked_sources,
                market_now=market_now,
            )

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

        lead_source = ""
        lead_latency_delta_ms = 0.0
        if recent_sources:
            earliest_src = min(recent_sources, key=lambda s: candidates[s][1])
            lead_source = earliest_src
            earliest_ts = candidates[earliest_src][1]
            if market_now is not None:
                lead_latency_delta_ms = max(0.0, (market_now - earliest_ts) * 1000.0)

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
            lead_source=lead_source,
            lead_latency_delta_ms=round(lead_latency_delta_ms, 3),
            reconciliation_type="TRADE",
        )

    def select_quote(
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
        active_quotes: dict[str, CanonicalEvent] = {}

        for src in sorted(candidates.keys()):
            ev, ts = candidates[src]
            if market_now is not None and abs(market_now - ts) > agreement_window_s:
                continue
            if src in blocked or ev.bid_price is None or ev.ask_price is None:
                continue
            active_quotes[src] = ev

        n_recent = len(active_quotes)
        if n_recent < 2:
            return None

        recent_sources = sorted(active_quotes.keys())
        bids = {src: ev.bid_price for src, ev in active_quotes.items() if ev.bid_price is not None}
        asks = {src: ev.ask_price for src, ev in active_quotes.items() if ev.ask_price is not None}
        spreads = {
            src: (ev.ask_price - ev.bid_price)
            for src, ev in active_quotes.items()
            if ev.ask_price is not None and ev.bid_price is not None
        }
        midpoints = {
            src: (ev.bid_price + ev.ask_price) / 2.0
            for src, ev in active_quotes.items()
            if ev.ask_price is not None and ev.bid_price is not None
        }

        mean_bid = sum(bids.values()) / len(bids) if bids else 0.0
        mean_ask = sum(asks.values()) / len(asks) if asks else 0.0
        mean_spread = sum(spreads.values()) / len(spreads) if spreads else 0.0

        bid_disagreement = False
        if mean_bid > 0:
            for b in bids.values():
                if abs(b - mean_bid) / mean_bid > disagreement_pct_threshold:
                    bid_disagreement = True
                    break

        ask_disagreement = False
        if mean_ask > 0:
            for a in asks.values():
                if abs(a - mean_ask) / mean_ask > disagreement_pct_threshold:
                    ask_disagreement = True
                    break

        spread_disagreement = False
        if mean_spread > 0:
            for s in spreads.values():
                if abs(s - mean_spread) / mean_spread > disagreement_pct_threshold:
                    spread_disagreement = True
                    break

        individual_crossed = any(
            (ev.bid_price is not None and ev.ask_price is not None and ev.bid_price > ev.ask_price)
            for ev in active_quotes.values()
        )
        cross_venue_crossed = (max(bids.values()) > min(asks.values())) if bids and asks else False
        is_crossed_book = individual_crossed or cross_venue_crossed

        disagreement = bid_disagreement or ask_disagreement or is_crossed_book

        quality_reasons: list[str] = []
        if is_crossed_book:
            quality_reasons.append(Reason.CROSSED_QUOTE.value)
        if bid_disagreement or ask_disagreement:
            quality_reasons.append(Reason.CROSS_FEED_DISAGREEMENT.value)

        # Median selection based on quote midpoint
        sorted_by_mid = sorted(active_quotes.items(), key=lambda it: (midpoints[it[0]], it[0]))
        mid_idx = len(sorted_by_mid) // 2
        if len(sorted_by_mid) % 2 == 1:
            chosen_source = sorted_by_mid[mid_idx][0]
        else:
            m1 = midpoints[sorted_by_mid[mid_idx - 1][0]]
            m2 = midpoints[sorted_by_mid[mid_idx][0]]
            med_val = (m1 + m2) / 2.0
            dist1 = abs(m1 - med_val)
            dist2 = abs(m2 - med_val)
            if dist1 < dist2:
                chosen_source = sorted_by_mid[mid_idx - 1][0]
            elif dist2 < dist1:
                chosen_source = sorted_by_mid[mid_idx][0]
            else:
                score1 = scores.get(sorted_by_mid[mid_idx - 1][0], 0.0)
                score2 = scores.get(sorted_by_mid[mid_idx][0], 0.0)
                chosen_source = sorted_by_mid[mid_idx - 1][0] if score1 >= score2 else sorted_by_mid[mid_idx][0]

        chosen_event = active_quotes[chosen_source]

        if disagreement:
            reason = (
                f"quote disagreement across {recent_sources} "
                f"(bids={bids}, asks={asks}, crossed={is_crossed_book}); selected median quote {chosen_source}"
            )
        else:
            reason = f"quote sources agree within threshold; selected median quote {chosen_source}"

        lead_source = ""
        lead_latency_delta_ms = 0.0
        if recent_sources:
            earliest_src = min(recent_sources, key=lambda s: candidates[s][1])
            lead_source = earliest_src
            earliest_ts = candidates[earliest_src][1]
            if market_now is not None:
                lead_latency_delta_ms = max(0.0, (market_now - earliest_ts) * 1000.0)

        return CanonicalDecision(
            instrument_id=instrument_id,
            chosen_source=chosen_source,
            chosen_event_id=chosen_event.event_id,
            disagreement=disagreement,
            reason=reason,
            competing_sources=recent_sources,
            quality_reasons=quality_reasons,
            lead_source=lead_source,
            lead_latency_delta_ms=round(lead_latency_delta_ms, 3),
            reconciliation_type="QUOTE",
            bid_disagreement=bid_disagreement,
            ask_disagreement=ask_disagreement,
            spread_disagreement=spread_disagreement,
            is_crossed_book=is_crossed_book,
        )


class Reconciler:
    """Arbitrates concurrent multi-source quotes and trades to establish canonical reality.

    Maintains the most recent non-invalid observation for each (instrument, source) pair,
    separating trade observations from quote observations.
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
        self._latest_trades: OrderedDict[str, dict[str, tuple[CanonicalEvent, float]]] = (
            OrderedDict()
        )
        self._latest_quotes: OrderedDict[str, dict[str, tuple[CanonicalEvent, float]]] = (
            OrderedDict()
        )
        self._latest = self._latest_trades  # Backward compatibility alias
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
        if event.quality_status == QualityStatus.INVALID:
            return None

        is_quote = (
            event.event_type == EventType.QUOTE
            or (event.bid_price is not None and event.ask_price is not None and event.price is None)
        )

        market_now = event.exchange_timestamp

        if is_quote:
            if event.bid_price is None or event.ask_price is None:
                return None  # Incomplete quotes cannot be reconciled
            store = self._latest_quotes
        else:
            if event.price is None:
                return None  # Only trade events with usable prices undergo pricing reconciliation
            store = self._latest_trades

        per_instrument = store.get(event.instrument_id)
        if per_instrument is None:
            if len(store) >= self.max_instruments:
                store.popitem(last=False)
            per_instrument = store[event.instrument_id] = {}
        else:
            store.move_to_end(event.instrument_id)
        per_instrument[event.source] = (event, market_now)

        # Fast path: multi-feed quorum cannot be established with < 2 feeds
        if len(per_instrument) < 2:
            return None

        if is_quote and hasattr(self.strategy, "select_quote"):
            return self.strategy.select_quote(
                instrument_id=event.instrument_id,
                candidates=per_instrument,
                scores=self.reliability._cached_scores,
                agreement_window_s=self.cfg.agreement_window_s,
                disagreement_pct_threshold=self.cfg.disagreement_pct_threshold,
                blocked_sources=self._blocked_sources,
                market_now=market_now,
            )

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
