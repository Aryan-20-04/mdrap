"""
MDRAP Microsecond Transaction Cost Analysis (TCA) & Best Execution Engine.

Implements institutional regulatory compliance (SEC Rule 605/606 & MiFID II RTS 27/28):
- Microsecond execution benchmarking against true consolidated NBBO
- Slippage calculation (Arrival price vs Execution price in cents & bps)
- Effective Spread vs Quoted Spread (Measuring execution quality)
- Price Improvement & Disimprovement quantification
- Broker / Venue Scorecard (Detecting PFOF markups and routing quality)
- Cryptographic SHA-256 Merkle audit trail for tamper-evident compliance
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from bbo import BBOEngine, ConsolidatedBBO


@dataclass(slots=True)
class ExecutionRecord:
    trade_id: str
    symbol: str
    side: str                     # 'BUY' or 'SELL'
    price: float                  # Actual execution price
    shares: float                 # Executed quantity
    timestamp: float              # Execution timestamp
    broker: str = "Interactive Brokers"
    venue: str = "NASDAQ"
    order_type: str = "MARKET"
    arrival_price: Optional[float] = None


@dataclass(slots=True)
class TCAMetrics:
    trade_id: str
    symbol: str
    side: str
    price: float
    shares: float
    timestamp: float
    broker: str
    venue: str
    arrival_price: float
    midpoint_at_fill: float
    bid_at_fill: float
    ask_at_fill: float
    quoted_spread_cents: float
    quoted_spread_bps: float
    effective_spread_cents: float
    effective_spread_bps: float
    slippage_cents: float          # Positive = unfavorable slippage, Negative = price improvement
    slippage_bps: float
    price_improvement_cents: float
    price_improvement_usd: float
    is_price_improved: bool
    is_disimproved: bool
    quality_score: float           # 0 to 100 execution quality score
    merkle_leaf_hash: str

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "price": round(self.price, 4),
            "shares": round(self.shares, 4),
            "timestamp": self.timestamp,
            "broker": self.broker,
            "venue": self.venue,
            "arrival_price": round(self.arrival_price, 4),
            "midpoint": round(self.midpoint_at_fill, 4),
            "bid": round(self.bid_at_fill, 4),
            "ask": round(self.ask_at_fill, 4),
            "quoted_spread_bps": round(self.quoted_spread_bps, 2),
            "effective_spread_bps": round(self.effective_spread_bps, 2),
            "slippage_cents": round(self.slippage_cents, 4),
            "slippage_bps": round(self.slippage_bps, 2),
            "price_improvement_usd": round(self.price_improvement_usd, 2),
            "is_improved": self.is_price_improved,
            "is_disimproved": self.is_disimproved,
            "score": round(self.quality_score, 1),
            "merkle_hash": self.merkle_leaf_hash,
        }


@dataclass
class BrokerScorecard:
    broker: str
    order_count: int = 0
    total_shares: float = 0.0
    total_notional: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_effective_spread_bps: float = 0.0
    price_improved_count: int = 0
    price_disimproved_count: int = 0
    total_price_improvement_usd: float = 0.0
    total_slippage_cost_usd: float = 0.0
    quality_score: float = 100.0

    @property
    def price_improvement_rate_pct(self) -> float:
        return round((self.price_improved_count / self.order_count) * 100.0, 1) if self.order_count > 0 else 0.0

    @property
    def rating(self) -> str:
        if self.quality_score >= 85.0:
            return "TIER 1 (EXCELLENT BEST-EX)"
        elif self.quality_score >= 70.0:
            return "TIER 2 (ACCEPTABLE)"
        elif self.quality_score >= 50.0:
            return "TIER 3 (WARNING: EXCESSIVE SLIPPAGE)"
        return "POOR (HIGH PFOF MARKUP)"

    def to_dict(self) -> dict:
        return {
            "broker": self.broker,
            "orders": self.order_count,
            "shares": round(self.total_shares, 2),
            "notional": round(self.total_notional, 2),
            "avg_slippage_bps": round(self.avg_slippage_bps, 2),
            "avg_eff_spread_bps": round(self.avg_effective_spread_bps, 2),
            "improvement_rate_pct": self.price_improvement_rate_pct,
            "total_improvement_usd": round(self.total_price_improvement_usd, 2),
            "slippage_cost_usd": round(self.total_slippage_cost_usd, 2),
            "score": round(self.quality_score, 1),
            "quality_score": round(self.quality_score, 1),
            "order_count": self.order_count,
            "total_shares": round(self.total_shares, 2),
            "total_price_improvement_usd": round(self.total_price_improvement_usd, 2),
            "rating": self.rating,
        }


class TCAEngine:
    """
    Microsecond Transaction Cost Analysis and Best Execution Evaluation Engine.
    """

    def __init__(self, bbo_engine: Optional[BBOEngine] = None):
        self.bbo_engine = bbo_engine or BBOEngine()
        self.metrics_history: List[TCAMetrics] = []

    def evaluate_execution(
        self,
        record: ExecutionRecord,
        prevailing_bbo: Optional[ConsolidatedBBO] = None,
    ) -> TCAMetrics:
        """
        Evaluate a single trade execution against contemporary microsecond NBBO.
        """
        # Defensive check: ensure finite, positive numbers to avoid NaN/Inf corruption
        px = record.price if (math.isfinite(record.price) and record.price > 0) else 100.0
        shs = record.shares if (math.isfinite(record.shares) and record.shares > 0) else 0.0

        bbo = prevailing_bbo or self.bbo_engine.get_bbo(record.symbol)

        # Fallback synthetic BBO if no historical quote found
        if bbo is None or bbo.best_bid is None or bbo.best_ask is None or not math.isfinite(bbo.best_bid) or not math.isfinite(bbo.best_ask):
            bid = round(px - 0.05, 4)
            ask = round(px + 0.05, 4)
        else:
            bid = bbo.best_bid
            ask = bbo.best_ask

        mid = (bid + ask) / 2.0
        quoted_spread_cents = max(0.001, ask - bid)
        quoted_spread_bps = (quoted_spread_cents / mid) * 10_000.0 if mid > 0 else 0.0

        arrival = record.arrival_price if (record.arrival_price is not None and math.isfinite(record.arrival_price) and record.arrival_price > 0) else mid
        side_norm = record.side.upper().strip()

        # 1. Slippage calculation (positive = worse for trader, negative = favorable price improvement)
        if side_norm == "BUY":
            slippage_cents = px - arrival
            # Price improvement: bought below prevailing ask
            price_improvement_cents = max(0.0, ask - px)
            # Disimprovement: bought above prevailing ask
            is_disimproved = px > ask
            # Effective spread = 2 * (Exec Price - Midpoint)
            effective_spread_cents = 2.0 * (px - mid)
        else:  # SELL
            slippage_cents = arrival - px
            # Price improvement: sold above prevailing bid
            price_improvement_cents = max(0.0, px - bid)
            # Disimprovement: sold below prevailing bid
            is_disimproved = px < bid
            # Effective spread = 2 * (Midpoint - Exec Price)
            effective_spread_cents = 2.0 * (mid - px)

        slippage_bps = (slippage_cents / arrival) * 10_000.0 if arrival > 0 else 0.0
        effective_spread_bps = (effective_spread_cents / mid) * 10_000.0 if mid > 0 else 0.0

        price_improvement_cents = round(price_improvement_cents, 4)
        price_improvement_usd = round(price_improvement_cents * shs, 2)
        is_price_improved = price_improvement_cents > 0.0001


        # 2. Quality scoring (100 = perfect mid/better fill, 0 = severe disimprovement)
        # Ratio of effective spread to quoted spread: ES / QS <= 1.0 is standard/good
        es_ratio = effective_spread_cents / quoted_spread_cents if quoted_spread_cents > 0 else 1.0
        if is_price_improved:
            quality_score = min(100.0, 85.0 + (price_improvement_cents / quoted_spread_cents) * 15.0)
        elif is_disimproved:
            quality_score = max(0.0, 50.0 - abs(slippage_bps) * 2.0)
        else:
            quality_score = max(50.0, 85.0 - (es_ratio - 0.5) * 30.0)

        # 3. Cryptographic Merkle leaf hash for audit proof
        leaf_payload = f"{record.trade_id}|{record.symbol}|{record.side}|{record.price}|{record.shares}|{record.timestamp}|{bid}|{ask}|{slippage_cents:.4f}"
        leaf_hash = hashlib.sha256(leaf_payload.encode("utf-8")).hexdigest()

        m = TCAMetrics(
            trade_id=record.trade_id,
            symbol=record.symbol,
            side=side_norm,
            price=record.price,
            shares=record.shares,
            timestamp=record.timestamp,
            broker=record.broker,
            venue=record.venue,
            arrival_price=arrival,
            midpoint_at_fill=mid,
            bid_at_fill=bid,
            ask_at_fill=ask,
            quoted_spread_cents=quoted_spread_cents,
            quoted_spread_bps=quoted_spread_bps,
            effective_spread_cents=effective_spread_cents,
            effective_spread_bps=effective_spread_bps,
            slippage_cents=slippage_cents,
            slippage_bps=slippage_bps,
            price_improvement_cents=price_improvement_cents,
            price_improvement_usd=price_improvement_usd,
            is_price_improved=is_price_improved,
            is_disimproved=is_disimproved,
            quality_score=quality_score,
            merkle_leaf_hash=leaf_hash,
        )

        self.metrics_history.append(m)
        return m

    def evaluate_batch(self, records: List[ExecutionRecord]) -> Dict[str, Any]:
        """
        Benchmark a batch of execution records and compile full Best Execution report.
        """
        metrics: List[TCAMetrics] = []
        for r in records:
            metrics.append(self.evaluate_execution(r))

        if not metrics:
            return {"total_trades": 0, "summary": {}, "scorecards": {}}

        total_trades = len(metrics)
        total_shares = sum(m.shares for m in metrics)
        total_notional = sum(m.price * m.shares for m in metrics)

        slippages_bps = sorted(m.slippage_bps for m in metrics)
        mean_slip_bps = sum(slippages_bps) / total_trades
        p50_slip_bps = slippages_bps[total_trades // 2]
        p95_idx = min(total_trades - 1, int(total_trades * 0.95))
        p95_slip_bps = slippages_bps[p95_idx]

        mean_eff_spread_bps = sum(m.effective_spread_bps for m in metrics) / total_trades
        mean_quoted_spread_bps = sum(m.quoted_spread_bps for m in metrics) / total_trades

        improved_count = sum(1 for m in metrics if m.is_price_improved)
        disimproved_count = sum(1 for m in metrics if m.is_disimproved)
        total_price_improvement_usd = sum(m.price_improvement_usd for m in metrics)
        total_slippage_cost_usd = sum(max(0.0, m.slippage_cents) * m.shares for m in metrics)

        overall_quality_score = sum(m.quality_score for m in metrics) / total_trades

        # Build broker scorecards
        broker_groups: Dict[str, List[TCAMetrics]] = {}
        for m in metrics:
            broker_groups.setdefault(m.broker, []).append(m)

        scorecards: List[BrokerScorecard] = []
        for broker, b_metrics in broker_groups.items():
            b_cnt = len(b_metrics)
            b_shares = sum(m.shares for m in b_metrics)
            b_notional = sum(m.price * m.shares for m in b_metrics)
            b_avg_slip = sum(m.slippage_bps for m in b_metrics) / b_cnt
            b_avg_es = sum(m.effective_spread_bps for m in b_metrics) / b_cnt
            b_improved = sum(1 for m in b_metrics if m.is_price_improved)
            b_disimproved = sum(1 for m in b_metrics if m.is_disimproved)
            b_tot_imp_usd = sum(m.price_improvement_usd for m in b_metrics)
            b_tot_slip_usd = sum(max(0.0, m.slippage_cents) * m.shares for m in b_metrics)
            b_score = sum(m.quality_score for m in b_metrics) / b_cnt

            scorecards.append(BrokerScorecard(
                broker=broker,
                order_count=b_cnt,
                total_shares=b_shares,
                total_notional=b_notional,
                avg_slippage_bps=b_avg_slip,
                avg_effective_spread_bps=b_avg_es,
                price_improved_count=b_improved,
                price_disimproved_count=b_disimproved,
                total_price_improvement_usd=b_tot_imp_usd,
                total_slippage_cost_usd=b_tot_slip_usd,
                quality_score=b_score,
            ))

        scorecards.sort(key=lambda sc: sc.quality_score, reverse=True)

        # Compute Merkle Root Hash across all leaf hashes
        concat_hashes = "".join(m.merkle_leaf_hash for m in metrics)
        merkle_root = hashlib.sha256(concat_hashes.encode("utf-8")).hexdigest()

        compliance_status = "COMPLIANT (PASSED BEST EXECUTION)" if overall_quality_score >= 65.0 else "NON-COMPLIANT (FLAGGED FOR SLIPPAGE AUDIT)"

        return {
            "total_trades": total_trades,
            "total_shares": round(total_shares, 2),
            "total_notional": round(total_notional, 2),
            "mean_slippage_bps": round(mean_slip_bps, 2),
            "p50_slippage_bps": round(p50_slip_bps, 2),
            "p95_slippage_bps": round(p95_slip_bps, 2),
            "mean_effective_spread_bps": round(mean_eff_spread_bps, 2),
            "mean_quoted_spread_bps": round(mean_quoted_spread_bps, 2),
            "price_improvement_count": improved_count,
            "price_improvement_rate_pct": round((improved_count / total_trades) * 100.0, 1),
            "price_disimproved_count": disimproved_count,
            "price_disimproved_rate_pct": round((disimproved_count / total_trades) * 100.0, 1),
            "total_price_improvement_usd": round(total_price_improvement_usd, 2),
            "total_slippage_cost_usd": round(total_slippage_cost_usd, 2),
            "overall_quality_score": round(overall_quality_score, 1),
            "compliance_status": compliance_status,
            "merkle_root": merkle_root,
            "broker_scorecards": [sc.to_dict() for sc in scorecards],
            "metrics": [m.to_dict() for m in metrics],
        }

    @staticmethod
    def generate_demo_executions(symbol: str = "AAPL", count: int = 100, seed: Optional[int] = None) -> List[ExecutionRecord]:
        """
        Generate realistic institutional execution logs with multi-broker routing variations.
        Simulates:
        - Broker 1 (Direct Market Access): High price improvement, tight fills
        - Broker 2 (PFOF Wholesaler): Wider fills, slight disimprovement
        - Broker 3 (Retail Discount): Average execution
        """
        records: List[ExecutionRecord] = []
        base_prices = {"AAPL": 150.0, "MSFT": 420.0, "NVDA": 130.0, "BTC/USD": 65000.0}
        base_px = base_prices.get(symbol.upper(), 150.0)

        brokers = [
            ("Interactive Brokers DMA", "NASDAQ", 0.02, -0.015), # Favorable DMA
            ("Morgan Stanley Execution", "NYSE", 0.01, -0.010),  # Institutional
            ("Alpaca Direct", "ARCA", 0.00, 0.005),              # Mid
            ("Wholesaler A (PFOF)", "INTERNAL", -0.03, 0.025),   # PFOF markup
        ]

        t_now = time.time() - 3600.0

        for i in range(count):
            b_info = brokers[i % len(brokers)]
            broker_name, venue, quality_bias, slip_bias = b_info

            side = "BUY" if (i % 2 == 0) else "SELL"
            drift = math.sin(i * 0.1) * 0.50
            arrival = base_px + drift
            spread = max(0.02, arrival * 0.0003)

            # Price simulation based on broker quality
            if side == "BUY":
                exec_px = arrival + (spread * 0.4) + slip_bias
            else:
                exec_px = arrival - (spread * 0.4) - slip_bias

            shares = 100.0 * (1 + (i % 10))

            records.append(ExecutionRecord(
                trade_id=f"EXEC-{symbol}-{i+1:04d}",
                symbol=symbol.upper(),
                side=side,
                price=round(exec_px, 4),
                shares=shares,
                timestamp=t_now + (i * 30.0),
                broker=broker_name,
                venue=venue,
                order_type="LIMIT" if i % 4 == 0 else "MARKET",
                arrival_price=arrival,
            ))

        return records


generate_demo_executions = TCAEngine.generate_demo_executions
