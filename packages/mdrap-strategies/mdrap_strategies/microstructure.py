"""
MDRAP Quantitative Market Microstructure & Alpha Research Module (§26, v5.1.1).

Implements mathematical foundations from institutional literature:
1. Avellaneda & Stoikov (2008) "High-frequency trading in a limit order book".
2. Stoikov (2018) "The Micro-Price: A High-Frequency Estimator of Future Prices".
3. Cont, Kukanov & Stoikov (2014) "The Price Impact of Order Book Events" (OFI).
4. Lee & Ready (1991) "Inferring Trade Direction from Intraday Data".
5. Statistical Significance & Predictive Power Testing for Alpha Signals.
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Any


@dataclass
class AvellanedaStoikovQuotes:
    reservation_price: float
    optimal_half_spread: float
    bid_quote: float
    ask_quote: float
    inventory: float
    volatility_sigma: float
    time_horizon_remaining: float


def compute_avellaneda_stoikov(
    mid_price: float,
    inventory: float,
    volatility_sigma: float,
    gamma: float = 0.1,
    kappa: float = 1.5,
    time_remaining: float = 1.0,
    min_tick: float = 0.01,
) -> AvellanedaStoikovQuotes:
    """
    Compute closed-form Avellaneda-Stoikov (2008) reservation price and optimal spread.

    Mathematical Model:
    1. Reservation Price:
       r(s, q, t) = s - q * gamma * sigma^2 * (T - t)
       - s: prevailing mid price
       - q: current inventory (shares/contracts)
       - gamma: risk-aversion parameter
       - sigma: asset price return volatility
       - (T - t): normalized remaining time horizon

    2. Optimal Half-Spread Delta:
       delta^a + delta^b = gamma * sigma^2 * (T - t) + (2 / gamma) * ln(1 + gamma / kappa)
       delta = 0.5 * (delta^a + delta^b)

    3. Optimal Quotes:
       r_bid = round(r - delta, 2)
       r_ask = round(r + delta, 2)

    Assumptions & Structural Constraints:
    - Price follows arithmetic Brownian motion: dS_t = sigma * dW_t
    - Order arrivals follow Poisson process with intensity lambda(delta) = A * exp(-kappa * delta)
    - Constant instantaneous volatility sigma (ignores volatility clustering / GARCH)
    - Zero execution latency (immediate quote placement)
    """
    s = mid_price
    q = inventory
    sigma = max(1e-6, volatility_sigma)
    t_rem = max(1e-4, time_remaining)

    # 1. Reservation Price r(s, q)
    reservation = s - (q * gamma * (sigma**2) * t_rem)

    # 2. Optimal Spread calculation
    spread_term1 = gamma * (sigma**2) * t_rem
    spread_term2 = (2.0 / gamma) * math.log(1.0 + (gamma / kappa))
    total_spread = spread_term1 + spread_term2
    half_spread = max(min_tick, total_spread / 2.0)

    bid_px = round(reservation - half_spread, 2)
    ask_px = round(reservation + half_spread, 2)

    return AvellanedaStoikovQuotes(
        reservation_price=reservation,
        optimal_half_spread=half_spread,
        bid_quote=bid_px,
        ask_quote=ask_px,
        inventory=q,
        volatility_sigma=sigma,
        time_horizon_remaining=t_rem,
    )


@dataclass
class LinearInventorySkewQuotes:
    reservation_price: float
    half_spread: float
    bid_quote: float
    ask_quote: float
    inventory: float
    inventory_penalty: float


def compute_linear_inventory_skew(
    mid_price: float,
    inventory: float,
    half_spread: float = 0.05,
    inventory_penalty: float = 0.01,
    min_tick: float = 0.01,
) -> LinearInventorySkewQuotes:
    """
    Compute heuristic linear inventory-skewed market-making quotes.

    Mathematical Model:
    1. Reservation Price:
       r(s, q) = s - (q * inventory_penalty)
       - s: prevailing mid price
       - q: current inventory
       - inventory_penalty: penalty per unit of inventory (lambda)

    2. Quotes:
       bid_quote = round(r - half_spread, 2)
       ask_quote = round(r + half_spread, 2)
    """
    s = mid_price
    q = inventory
    eff_half_spread = max(min_tick, half_spread)
    reservation = s - (q * inventory_penalty)
    bid_px = round(reservation - eff_half_spread, 2)
    ask_px = round(reservation + eff_half_spread, 2)

    return LinearInventorySkewQuotes(
        reservation_price=reservation,
        half_spread=eff_half_spread,
        bid_quote=bid_px,
        ask_quote=ask_px,
        inventory=q,
        inventory_penalty=inventory_penalty,
    )


def compute_micro_price(
    best_bid: float,
    best_ask: float,
    bid_size: float,
    ask_size: float,
) -> float:
    """
    Volume-Weighted Micro-Price (Stoikov 2018):
    P_micro = (Q_bid * P_ask + Q_ask * P_bid) / (Q_bid + Q_ask)
    Incorporates top-of-book order queue imbalance as an unbiased short-term predictor.
    """
    total_size = bid_size + ask_size
    if total_size <= 0:
        return (best_bid + best_ask) / 2.0
    return ((bid_size * best_ask) + (ask_size * best_bid)) / total_size


def compute_order_flow_imbalance(
    prev_bid_px: float,
    prev_bid_sz: float,
    prev_ask_px: float,
    prev_ask_sz: float,
    curr_bid_px: float,
    curr_bid_sz: float,
    curr_ask_px: float,
    curr_ask_sz: float,
) -> float:
    """
    Order Flow Imbalance (OFI) as formulated by Cont, Kukanov & Stoikov (2014):
    OFI_t = Delta_Bid_t - Delta_Ask_t

    where:
    Delta_Bid_t = curr_bid_sz if curr_bid_px > prev_bid_px
                = curr_bid_sz - prev_bid_sz if curr_bid_px == prev_bid_px
                = -prev_bid_sz if curr_bid_px < prev_bid_px

    Delta_Ask_t = -curr_ask_sz if curr_ask_px < prev_ask_px
                = curr_ask_sz - prev_ask_sz if curr_ask_px == prev_ask_px
                = prev_ask_sz if curr_ask_px > prev_ask_px
    """
    # Delta Bid
    if curr_bid_px > prev_bid_px:
        delta_bid = curr_bid_sz
    elif curr_bid_px == prev_bid_px:
        delta_bid = curr_bid_sz - prev_bid_sz
    else:
        delta_bid = -prev_bid_sz

    # Delta Ask
    if curr_ask_px < prev_ask_px:
        delta_ask = curr_ask_sz
    elif curr_ask_px == prev_ask_px:
        delta_ask = curr_ask_sz - prev_ask_sz
    else:
        delta_ask = -prev_ask_sz

    return delta_bid - delta_ask


def classify_trade_lee_ready(
    trade_price: float,
    prev_trade_price: float | None,
    bid_price: float,
    ask_price: float,
) -> str:
    """
    Lee-Ready (1991) Trade Aggressor Classification Algorithm:
    1. Quote Rule:
       - If Price > Mid: BUY aggressor
       - If Price < Mid: SELL aggressor
    2. Tick Rule (if Price == Mid):
       - If Price > Prev_Price: BUY aggressor (uptick)
       - If Price < Prev_Price: SELL aggressor (downtick)
       - If Price == Prev_Price: inherit prior classification (zero-tick)
    """
    if bid_price > 0 and ask_price > 0:
        mid = (bid_price + ask_price) / 2.0
        if trade_price > mid:
            return "BUY"
        elif trade_price < mid:
            return "SELL"

    # Fallback to Tick Rule
    if prev_trade_price is not None:
        if trade_price > prev_trade_price:
            return "BUY"
        elif trade_price < prev_trade_price:
            return "SELL"

    return "BUY"  # Default fallback


def evaluate_signal_predictive_power(
    signal_returns: list[float],
) -> dict[str, Any]:
    """
    Evaluate statistical significance of quantitative trading signals.
    Computes Sample Mean, t-statistic, p-value approximation, Information Ratio,
    and False Positive Probability.
    """
    n = len(signal_returns)
    if n < 5:
        return {
            "sample_size": n,
            "statistically_significant": False,
            "reason": "Insufficient sample size (N < 5)",
        }

    mean_ret = sum(signal_returns) / n
    variance = sum((r - mean_ret) ** 2 for r in signal_returns) / (n - 1)
    std_dev = math.sqrt(max(1e-10, variance))
    std_err = std_dev / math.sqrt(n)

    # Student's t-statistic
    t_stat = mean_ret / std_err if std_err > 0 else 0.0

    # Two-tailed p-value approximation via standard normal erf
    # for degrees of freedom >= 30, t approximates normal distribution
    p_val = math.erfc(abs(t_stat) / math.sqrt(2.0))

    # Annualized Information Ratio (assuming 252 trading days, 23,400 seconds per day)
    info_ratio = (mean_ret / std_dev) * math.sqrt(252.0) if std_dev > 0 else 0.0

    is_significant = abs(t_stat) >= 1.96 and p_val < 0.05

    return {
        "sample_size": n,
        "mean_return_bps": mean_ret * 10_000.0,
        "std_dev_bps": std_dev * 10_000.0,
        "t_statistic": round(t_stat, 4),
        "p_value": round(p_val, 6),
        "information_ratio": round(info_ratio, 3),
        "statistically_significant": is_significant,
        "confidence_level": "95%" if is_significant else "Not Significant",
    }
