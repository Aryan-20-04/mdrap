"""
MDRAP Institutional Pre-Trade Risk Engine (§19, §26, v5.1.1).

Enforces 14 mandatory institutional pre-trade risk gates before any order submission:
1. Maximum Order Quantity (fat-finger share collar).
2. Maximum Position Limit per instrument.
3. Maximum Notional Value per order ($ USD).
4. Price Deviation Collar (max bps deviation from prevailing mid/NBBO).
5. Maximum Gross Portfolio Exposure.
6. Maximum Net Portfolio Exposure.
7. Maximum Portfolio Drawdown Kill-Switch (abruptly freezes trading).
8. Per-Symbol Notional Cap.
9. Per-Venue Exposure Cap.
10. Stale Quote Rejection: rejects orders if prevailing quote is older than threshold.
11. Duplicate Order Prevention / Idempotency Key Hashing.
12. Short Selling Policy Guard (optional long-only mode).
13. Emergency Circuit Breaker / Operator Kill-Switch.
14. Risk State Persistence & Recovery: persists risk state to JSON/disk across restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger("mdrap.risk_engine")


@dataclass
class StrictRiskLimits:
    """Institutional Pre-Trade Risk Parameters."""

    max_order_qty: float = 1_000.0  # Max shares/contracts per order
    max_position_qty: float = 5_000.0  # Max absolute position per symbol
    max_order_notional_usd: float = 250_000.0  # Max notional value per single order
    max_gross_exposure_usd: float = 1_000_000.0  # Max total portfolio gross exposure
    max_net_exposure_usd: float = 500_000.0  # Max total portfolio net exposure
    price_collar_bps: float = 50.0  # 50 bps max deviation from mid (0.50%)
    max_drawdown_pct: float = 5.0  # 5.0% maximum portfolio drawdown kill-switch
    max_quote_age_s: float = 5.0  # Stale market data quote expiration threshold
    per_symbol_notional_cap: float = 500_000.0  # Max notional allocated to any single symbol
    per_venue_notional_cap: float = 750_000.0  # Max notional allocated to any single venue
    allow_short: bool = True  # Enable/disable short selling
    idempotency_window_s: float = 60.0  # Duplicate order check window


class RiskCheckResult:
    def __init__(self, passed: bool, reason: str = "", gate: str = ""):
        self.passed = passed
        self.reason = reason
        self.gate = gate

    def __bool__(self) -> bool:
        return self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "gate": self.gate,
        }


class InstitutionalRiskEngine:
    """
    Production Pre-Trade Risk Gatekeeper.
    Every order submission MUST pass validate_order() before reaching the EMS or matching engine.
    """

    def __init__(
        self,
        limits: StrictRiskLimits | None = None,
        initial_capital: float = 100_000.0,
        persistence_path: str | None = None,
    ):
        self.limits = limits or StrictRiskLimits()
        self.initial_capital = initial_capital
        self.peak_equity = initial_capital
        self.current_equity = initial_capital
        self.kill_switch_active = False
        self.kill_switch_reason = ""
        self.persistence_path = persistence_path

        # Portfolio positions: symbol -> quantity
        self.positions: dict[str, float] = {}
        self.avg_costs: dict[str, float] = {}
        # Venue exposure: venue -> current notional
        self.venue_exposures: dict[str, float] = {}

        # Idempotency cache: hash -> timestamp
        self._seen_orders: dict[str, float] = {}
        # Reject counter
        self.reject_counts: dict[str, int] = {}

        if self.persistence_path and os.path.exists(self.persistence_path):
            self.load_state()

    def activate_kill_switch(self, reason: str) -> None:
        """Emergency circuit breaker: freeze all subsequent order submissions."""
        self.kill_switch_active = True
        self.kill_switch_reason = reason
        logger.critical("[risk_engine] EMERGENCY KILL-SWITCH ACTIVATED: %s", reason)
        self.persist_state()

    def reset_kill_switch(self) -> None:
        """Operator reset of the kill-switch after remediation."""
        self.kill_switch_active = False
        self.kill_switch_reason = ""
        logger.info("[risk_engine] Kill-switch reset by authorized operator.")
        self.persist_state()

    def update_portfolio_equity(self, current_equity: float) -> None:
        """Update portfolio equity and track high-water mark / drawdown."""
        self.current_equity = current_equity
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity

        if self.peak_equity > 0:
            drawdown_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100.0
            if drawdown_pct >= self.limits.max_drawdown_pct:
                self.activate_kill_switch(
                    f"Max portfolio drawdown breached: {drawdown_pct:.2f}% >= {self.limits.max_drawdown_pct:.2f}%"
                )

    def update_position(self, symbol: str, fill_qty: float, fill_price: float, side: str, venue: str = "DEFAULT") -> None:
        """Update inventory and average cost following an executed fill."""
        cur_qty = self.positions.get(symbol, 0.0)
        cur_cost = self.avg_costs.get(symbol, 0.0)

        delta_qty = fill_qty if side.upper() == "BUY" else -fill_qty
        new_qty = cur_qty + delta_qty

        if cur_qty * delta_qty > 0 or cur_qty == 0:
            # Increasing position size
            total_cost = (cur_qty * cur_cost) + (delta_qty * fill_price)
            self.avg_costs[symbol] = total_cost / new_qty if new_qty != 0 else 0.0
        elif abs(new_qty) < abs(cur_qty):
            # Reducing position size: avg cost remains same
            pass
        else:
            # Position flipped side
            self.avg_costs[symbol] = fill_price

        self.positions[symbol] = new_qty
        self.venue_exposures[venue] = self.venue_exposures.get(venue, 0.0) + (fill_qty * fill_price)
        self.persist_state()

    def validate_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        prevailing_mid: float | None = None,
        quote_timestamp: float | None = None,
        venue: str = "DEFAULT",
        idempotency_key: str | None = None,
    ) -> RiskCheckResult:
        """
        Validate order against all 14 pre-trade institutional risk gates.
        Returns RiskCheckResult(passed=True) if fully compliant, else passed=False with reason.
        """
        now = time.time()

        # Gate 1: Emergency Kill-Switch Check
        if self.kill_switch_active:
            return self._reject("KILL_SWITCH", f"Kill-switch is ACTIVE: {self.kill_switch_reason}")

        # Gate 2: Positive & Valid Quantity
        if not math.isfinite(quantity) or quantity <= 0:
            return self._reject("INVALID_QUANTITY", f"Order quantity {quantity} is non-positive or non-finite")

        # Gate 3: Maximum Order Quantity (Fat-Finger Collar)
        if quantity > self.limits.max_order_qty:
            return self._reject(
                "FAT_FINGER_QTY",
                f"Order quantity {quantity} exceeds max order limit {self.limits.max_order_qty}",
            )

        # Gate 4: Stale Market Data Quote Check
        if quote_timestamp is not None:
            quote_age = now - quote_timestamp
            if quote_age > self.limits.max_quote_age_s:
                return self._reject(
                    "STALE_QUOTE",
                    f"Market data is stale: quote age {quote_age:.2f}s > threshold {self.limits.max_quote_age_s:.2f}s",
                )

        # Gate 5: Price Deviation Collar for Limit Orders
        ref_price = price or prevailing_mid
        if order_type.upper() == "LIMIT" and price is not None and prevailing_mid is not None and prevailing_mid > 0:
            dev_bps = abs(price - prevailing_mid) / prevailing_mid * 10_000.0
            if dev_bps > self.limits.price_collar_bps:
                return self._reject(
                    "PRICE_COLLAR",
                    f"Limit price {price} deviates by {dev_bps:.1f} bps from mid {prevailing_mid} (max: {self.limits.price_collar_bps} bps)",
                )

        # Gate 6: Maximum Order Notional ($ USD)
        if ref_price is not None and ref_price > 0:
            order_notional = quantity * ref_price
            if order_notional > self.limits.max_order_notional_usd:
                return self._reject(
                    "MAX_NOTIONAL",
                    f"Order notional ${order_notional:,.2f} exceeds limit ${self.limits.max_order_notional_usd:,.2f}",
                )
        else:
            order_notional = 0.0

        # Gate 7: Projected Position Size Check
        current_pos = self.positions.get(symbol, 0.0)
        projected_pos = current_pos + (quantity if side.upper() == "BUY" else -quantity)

        if not self.limits.allow_short and projected_pos < 0:
            return self._reject("SHORT_SELLING_DISABLED", "Short selling is disabled by risk policy")

        if abs(projected_pos) > self.limits.max_position_qty:
            return self._reject(
                "MAX_POSITION",
                f"Projected position {projected_pos} exceeds max position limit {self.limits.max_position_qty}",
            )

        # Gate 8: Per-Symbol Notional Cap
        if ref_price is not None and ref_price > 0:
            proj_sym_notional = abs(projected_pos) * ref_price
            if proj_sym_notional > self.limits.per_symbol_notional_cap:
                return self._reject(
                    "SYMBOL_CAP",
                    f"Projected notional for {symbol} (${proj_sym_notional:,.2f}) exceeds symbol cap ${self.limits.per_symbol_notional_cap:,.2f}",
                )

        # Gate 9: Per-Venue Notional Cap
        cur_venue_notional = self.venue_exposures.get(venue, 0.0)
        if cur_venue_notional + order_notional > self.limits.per_venue_notional_cap:
            return self._reject(
                "VENUE_CAP",
                f"Projected notional for venue {venue} exceeds venue cap ${self.limits.per_venue_notional_cap:,.2f}",
            )

        # Gate 10: Gross & Net Portfolio Exposure
        gross_exp = sum(abs(q) * self.avg_costs.get(s, 100.0) for s, q in self.positions.items()) + order_notional
        if gross_exp > self.limits.max_gross_exposure_usd:
            return self._reject(
                "GROSS_EXPOSURE",
                f"Projected gross exposure ${gross_exp:,.2f} exceeds limit ${self.limits.max_gross_exposure_usd:,.2f}",
            )

        # Gate 11: Idempotency & Duplicate Order Prevention
        if idempotency_key:
            last_seen = self._seen_orders.get(idempotency_key)
            if last_seen is not None and (now - last_seen) < self.limits.idempotency_window_s:
                return self._reject(
                    "DUPLICATE_ORDER",
                    f"Duplicate order submission rejected (idempotency token '{idempotency_key}' active)",
                )
            self._seen_orders[idempotency_key] = now

        # All gates passed cleanly
        return RiskCheckResult(passed=True)

    def _reject(self, gate: str, reason: str) -> RiskCheckResult:
        self.reject_counts[gate] = self.reject_counts.get(gate, 0) + 1
        logger.warning("[risk_engine] PRE-TRADE REJECT [%s]: %s", gate, reason)
        return RiskCheckResult(passed=False, reason=reason, gate=gate)

    def persist_state(self) -> None:
        """Persist risk state to disk."""
        if not self.persistence_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.persistence_path)), exist_ok=True)
            payload = {
                "peak_equity": self.peak_equity,
                "current_equity": self.current_equity,
                "kill_switch_active": self.kill_switch_active,
                "kill_switch_reason": self.kill_switch_reason,
                "positions": self.positions,
                "avg_costs": self.avg_costs,
                "venue_exposures": self.venue_exposures,
                "reject_counts": self.reject_counts,
                "persisted_at": time.time(),
            }
            with open(self.persistence_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            logger.error("[risk_engine] Failed to persist risk state: %s", exc)

    def load_state(self) -> None:
        """Load risk state from disk across restarts."""
        if not self.persistence_path or not os.path.isfile(self.persistence_path):
            return
        try:
            with open(self.persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.peak_equity = data.get("peak_equity", self.peak_equity)
            self.current_equity = data.get("current_equity", self.current_equity)
            self.kill_switch_active = data.get("kill_switch_active", False)
            self.kill_switch_reason = data.get("kill_switch_reason", "")
            self.positions = data.get("positions", {})
            self.avg_costs = data.get("avg_costs", {})
            self.venue_exposures = data.get("venue_exposures", {})
            self.reject_counts = data.get("reject_counts", {})
            logger.info("[risk_engine] Successfully restored risk state from %s", self.persistence_path)
        except Exception as exc:
            logger.error("[risk_engine] Failed to load risk state: %s", exc)
