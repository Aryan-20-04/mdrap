"""
Options & Derivatives Pricing Engine for MDRAP.

This module provides pure-Python implementations of options pricing models,
including BSM European, CRR Binomial American, full Greeks chain (Delta through Volga),
Newton-Raphson IV solver, volatility surface construction, and options chain analysis.
"""

import enum
import math
import statistics
from dataclasses import dataclass
from typing import Optional

try:
    import fastpath
except ImportError:
    fastpath = None


class OptionType(str, enum.Enum):
    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(str, enum.Enum):
    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


@dataclass(slots=True)
class OptionContract:
    underlying: str  # e.g. 'AAPL'
    strike: float  # strike price
    expiry_days: float  # days to expiration
    option_type: OptionType  # CALL or PUT
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN


@dataclass(slots=True)
class Greeks:
    delta: float  # dV/dS
    gamma: float  # d²V/dS²
    theta: float  # dV/dt (per day)
    vega: float  # dV/dσ (per 1% vol change)
    rho: float  # dV/dr (per 1% rate change)
    vanna: float  # d²V/dSdσ
    volga: float  # d²V/dσ² (a.k.a. vomma)


@dataclass(slots=True)
class OptionPrice:
    theoretical: float
    intrinsic: float
    time_value: float
    greeks: Greeks
    model: str  # 'BSM' or 'BINOMIAL'


_STD_NORM = statistics.NormalDist()
_norm_cdf = _STD_NORM.cdf
_norm_pdf = _STD_NORM.pdf


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Compute standardized Black-Scholes distance metric d1:
        d1 = [ln(S / K) + (r + 0.5 * σ²) * T] / (σ * √T)

    Represents the expected moneyness under the risk-neutral measure, adjusted for drift
    and normalized by volatility. Governs the hedge ratio (Delta) for call options.
    """
    if T <= 0.0 or sigma <= 0.0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Compute standardized Black-Scholes probability distance metric d2:
        d2 = d1 - σ * √T

    Represents the probability of exercise in the risk-neutral world: N(d2) is the
    risk-neutral probability that a European call option expires in the money (S_T > K).
    """
    if T <= 0.0 or sigma <= 0.0:
        return 0.0
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bsm_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType
) -> float:
    """
    Compute theoretical European option price via the Black-Scholes-Merton formula:
        Call: C = S * N(d1) - K * e^(-rT) * N(d2)
        Put:  P = K * e^(-rT) * N(-d2) - S * N(-d1)

    Mathematical Assumptions:
        - Geometric Brownian Motion (GBM) with constant volatility σ and drift r.
        - Frictionless market with continuous trading, no dividends, and borrowing at rate r.
        - European exercise style (exercise allowed only at expiration T).

    Parameters:
        S: Current underlying asset spot price (e.g., 100.0).
        K: Strike price of the option contract (e.g., 100.0).
        T: Time to expiration in years (e.g., 30 days = 30 / 365.0).
        r: Annualized risk-free interest rate (e.g., 0.05 for 5%).
        sigma: Annualized implied volatility (e.g., 0.20 for 20%).
        option_type: OptionType.CALL or OptionType.PUT.

    Boundary Invariants:
        - At expiration (T <= 0), price converges strictly to intrinsic value max(0, S - K) or max(0, K - S).
        - Zero volatility (σ <= 0) collapses to discounted forward payout: max(0, S - K * e^(-rT)).
    """
    if fastpath is not None:
        c_val = fastpath.fast_bsm_price(
            S, K, T, r, sigma, option_type == OptionType.CALL
        )
        if c_val is not None:
            return c_val

    # Expiration boundary: option collapses to pure intrinsic payoff
    if T <= 0.0:
        return max(0.0, S - K) if option_type == OptionType.CALL else max(0.0, K - S)

    # Zero-volatility deterministic limit: discounted forward intrinsic
    if sigma <= 0.0:
        return (
            max(0.0, S - K * math.exp(-r * T))
            if option_type == OptionType.CALL
            else max(0.0, K * math.exp(-r * T) - S)
        )

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)

    if option_type == OptionType.CALL:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bsm_greeks(
    S: float, K: float, T: float, r: float, sigma: float, option_type: OptionType
) -> Greeks:
    """
    Compute complete first-order and second-order risk sensitivities (Greeks):

    1. Delta (Δ = ∂V / ∂S):
       - Call: N(d1) ∈ [0, 1]. Hedge ratio: shares of underlying needed to hedge 1 call.
       - Put: N(d1) - 1 ∈ [-1, 0]. Negative exposure to spot movements.
    2. Gamma (Γ = ∂²V / ∂S²):
       - Identical for Calls and Puts: n(d1) / (S * σ * √T). Measures convexity and rate of Delta change.
    3. Theta (Θ = ∂V / ∂t):
       - Negative decay of option value per calendar day (1/365th of annualized derivative).
    4. Vega (V = ∂V / ∂σ):
       - Sensitivity of option price to a 1% absolute shift in annualized volatility (∂V / ∂σ * 0.01).
    5. Rho (ρ = ∂V / ∂r):
       - Sensitivity of option price to a 1% absolute shift in risk-free interest rate (∂V / ∂r * 0.01).
    6. Vanna (∂²V / ∂S∂σ = ∂Δ / ∂σ):
       - Cross-derivative of Delta with respect to volatility, critical for dynamic smile hedging.
    7. Volga / Vomma (∂²V / ∂σ²):
       - Convexity of Vega with respect to volatility, measuring exposure to large vol shocks.
    """
    if fastpath is not None:
        c_greeks = fastpath.fast_bsm_greeks(
            S, K, T, r, sigma, option_type == OptionType.CALL
        )
        if c_greeks is not None:
            return Greeks(*c_greeks)

    # Degenerate boundary conditions (expired or zero vol contracts)
    if T <= 0.0 or sigma <= 0.0:
        delta = 0.0
        if option_type == OptionType.CALL and S > K:
            delta = 1.0
        elif option_type == OptionType.PUT and S < K:
            delta = -1.0
        return Greeks(
            delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0, vanna=0.0, volga=0.0
        )

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)

    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    n_d1 = _norm_pdf(d1)

    # Directional first-order Greeks depending on contract side
    if option_type == OptionType.CALL:
        delta = nd1
        theta_base = (-S * n_d1 * sigma) / (2 * math.sqrt(T)) - r * K * math.exp(
            -r * T
        ) * nd2
        rho = K * T * math.exp(-r * T) * nd2 / 100.0
    else:
        delta = nd1 - 1.0
        theta_base = (-S * n_d1 * sigma) / (2 * math.sqrt(T)) + r * K * math.exp(
            -r * T
        ) * _norm_cdf(-d2)
        rho = -K * T * math.exp(-r * T) * _norm_cdf(-d2) / 100.0

    # Curvature and cross Greeks
    gamma = n_d1 / (S * sigma * math.sqrt(T))
    theta = theta_base / 365.0  # Normalized to 1 calendar day decay
    vega = S * n_d1 * math.sqrt(T) / 100.0  # Scaled to 1% volatility step

    vanna = -n_d1 * d2 / sigma
    volga = vega * 100.0 * d1 * d2 / sigma

    return Greeks(
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        vanna=vanna,
        volga=volga,
    )


def binomial_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    steps: int = 200,
) -> float:
    """
    Price an American option via the Cox-Ross-Rubinstein (CRR) Binomial Lattice Tree.

    Algorithmic Workflow:
    1. Recombining tree parameters:
       - Time step: dt = T / steps
       - Up factor: u = exp(σ * √dt)
       - Down factor: d = 1 / u (ensuring recombinant nodes S_t * u * d = S_t)
       - Risk-neutral probability: p = (exp(r * dt) - d) / (u - d)
    2. Terminal Payoff:
       - Construct leaf nodes at maturity: S_T(i) = S * u^(steps - i) * d^i
       - Payoff = max(0, S_T - K) for Calls, max(0, K - S_T) for Puts
    3. Backward Dynamic Programming Induction:
       - For each time step j from (steps - 1) down to 0:
           V_hold = exp(-r * dt) * [p * V_up + (1 - p) * V_down]
           V_exercise = max(0, S_j - K) or max(0, K - S_j)
           V_j = max(V_hold, V_exercise)  # American early exercise check
    """
    if fastpath is not None:
        c_price = fastpath.fast_binomial_price(
            S, K, T, r, sigma, option_type == OptionType.CALL, steps
        )
        if c_price is not None:
            return c_price

    if T <= 0.0:
        return max(0.0, S - K) if option_type == OptionType.CALL else max(0.0, K - S)
    if sigma <= 0.0:
        return bsm_price(S, K, T, r, sigma, option_type)

    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    a = math.exp(r * dt)
    p = (a - d) / (u - d)

    # Precompute terminal prices at leaf nodes (t = T)
    prices = [0.0] * (steps + 1)
    for i in range(steps + 1):
        spot_t = S * (u ** (steps - i)) * (d**i)
        if option_type == OptionType.CALL:
            prices[i] = max(0.0, spot_t - K)
        else:
            prices[i] = max(0.0, K - spot_t)

    # Backward induction: discount expectation vs. immediate exercise
    discount = math.exp(-r * dt)
    for j in range(steps - 1, -1, -1):
        for i in range(j + 1):
            hold_val = discount * (p * prices[i] + (1 - p) * prices[i + 1])
            spot_t = S * (u ** (j - i)) * (d**i)
            if option_type == OptionType.CALL:
                exercise_val = max(0.0, spot_t - K)
            else:
                exercise_val = max(0.0, K - spot_t)
            prices[i] = max(hold_val, exercise_val)

    return prices[0]


def price_option(
    contract: OptionContract,
    spot: float,
    risk_free_rate: float = 0.05,
    volatility: float = 0.20,
) -> OptionPrice:
    """
    Unified derivatives pricing entrypoint.
    Dispatches to closed-form BSM for European exercise, and CRR Binomial lattice for American contracts.
    """
    T = contract.expiry_days / 365.0
    if contract.option_type == OptionType.CALL:
        intrinsic = max(0.0, spot - contract.strike)
    else:
        intrinsic = max(0.0, contract.strike - spot)

    greeks = bsm_greeks(
        spot, contract.strike, T, risk_free_rate, volatility, contract.option_type
    )

    if contract.exercise_style == ExerciseStyle.EUROPEAN:
        theoretical = bsm_price(
            spot, contract.strike, T, risk_free_rate, volatility, contract.option_type
        )
        model = "BSM"
    else:
        theoretical = binomial_price(
            spot, contract.strike, T, risk_free_rate, volatility, contract.option_type
        )
        model = "BINOMIAL"

    time_value = max(0.0, theoretical - intrinsic)

    return OptionPrice(
        theoretical=theoretical,
        intrinsic=intrinsic,
        time_value=time_value,
        greeks=greeks,
        model=model,
    )


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """
    Solve for annualized Implied Volatility (IV) using the Newton-Raphson method:
        σ_{k+1} = σ_k - [BSM(σ_k) - P_market] / Vega(σ_k)

    Convergence & Invariants:
        - Achieves quadratic local convergence when Vega is non-zero.
        - Initial seed is chosen at σ_0 = 0.30 (30% vol).
        - If market price is below intrinsic value, no real solution exists (returns 0.0).
        - Clamped to safe range [0.001, 5.0] to prevent divergent numerical instability in wings.
    """
    if T <= 0.0:
        return 0.0

    # No-arbitrage bound check: option market price cannot be below intrinsic value
    intrinsic = max(0.0, S - K) if option_type == OptionType.CALL else max(0.0, K - S)
    if market_price < intrinsic:
        return 0.0

    if fastpath is not None:
        c_iv = fastpath.fast_implied_volatility(
            market_price, S, K, T, r, option_type == OptionType.CALL, tol, max_iter
        )
        if c_iv is not None and c_iv > 0.0:
            return c_iv

    sigma = 0.3
    for _ in range(max_iter):
        price = bsm_price(S, K, T, r, sigma, option_type)
        diff = price - market_price
        if abs(diff) < tol:
            return sigma

        # Vega is the first derivative with respect to volatility: ∂V / ∂σ
        vega = S * _norm_pdf(_d1(S, K, T, r, sigma)) * math.sqrt(T)
        if vega < 1e-8:  # Vanishing gradient in deep wings
            break

        sigma = sigma - diff / vega

        # Guardrails against non-physical negative or extreme volatility solutions
        if sigma <= 0.0:
            sigma = 0.001
        elif sigma > 5.0:
            sigma = 5.0

    return sigma


def put_call_parity_check(
    call_price: float,
    put_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    tolerance_pct: float = 1.0,
) -> dict:
    """Check put-call parity: C - P = S - K*exp(-rT).
    Returns dict with 'theoretical_diff', 'actual_diff', 'violation_pct', 'is_violated'."""
    actual_diff = call_price - put_price
    theoretical_diff = S - K * math.exp(-r * T)
    diff = abs(actual_diff - theoretical_diff)

    base = max(S, 1.0)
    violation_pct = (diff / base) * 100.0
    is_violated = violation_pct > tolerance_pct

    return {
        "theoretical_diff": theoretical_diff,
        "actual_diff": actual_diff,
        "violation_pct": violation_pct,
        "is_violated": is_violated,
    }


class VolatilitySurface:
    """Implied volatility surface indexed by (strike, expiry)."""

    def __init__(self):
        self._points: list[tuple[float, float, float]] = []  # (strike, expiry_days, iv)

    def add_point(self, strike: float, expiry_days: float, iv: float):
        self._points.append((strike, expiry_days, iv))

    def get_iv(self, strike: float, expiry_days: float) -> Optional[float]:
        """Linear interpolation of IV at (strike, expiry). Returns None if insufficient data."""
        if not self._points:
            return None

        # Simple nearest-neighbor/interpolation logic
        # For a full implementation, you'd use a 2D interpolator like SciPy's griddata.
        # Since we're stdlib-only, we'll find exact matches or nearest neighbors.
        exact = [
            p
            for p in self._points
            if abs(p[0] - strike) < 1e-4 and abs(p[1] - expiry_days) < 1e-4
        ]
        if exact:
            return exact[0][2]

        # Very basic fallback to nearest Euclidean point (normalized)
        best_iv = None
        min_dist = float("inf")
        for s, e, iv in self._points:
            # normalized distance assuming $100 stock and 365 days
            dist = ((s - strike) / max(strike, 1.0)) ** 2 + (
                (e - expiry_days) / max(expiry_days, 1.0)
            ) ** 2
            if dist < min_dist:
                min_dist = dist
                best_iv = iv

        return best_iv

    def smile(self, expiry_days: float) -> list[tuple[float, float]]:
        """Return the volatility smile at a given expiry: [(strike, iv), ...]."""
        pts = [(s, iv) for s, e, iv in self._points if abs(e - expiry_days) < 1e-4]
        return sorted(pts)

    def term_structure(self, strike: float) -> list[tuple[float, float]]:
        """Return term structure at a given strike: [(expiry_days, iv), ...]."""
        pts = [(e, iv) for s, e, iv in self._points if abs(s - strike) < 1e-4]
        return sorted(pts)

    def skew(self, expiry_days: float) -> float:
        """Compute 25-delta risk reversal skew approximation at the given expiry."""
        smile_pts = self.smile(expiry_days)
        if len(smile_pts) < 2:
            return 0.0

        # Simplistic approximation: IV of lowest strike - IV of highest strike
        return smile_pts[0][1] - smile_pts[-1][1]

    def to_dict(self) -> dict:
        return {"points": self._points}


class OptionsChain:
    """Represents a full options chain for a single underlying."""

    def __init__(self, underlying: str, spot: float, risk_free_rate: float = 0.05):
        self.underlying = underlying
        self.spot = spot
        self.risk_free_rate = risk_free_rate
        self._chain: list[dict] = []

    def add_expiry(
        self, expiry_days: float, strikes: list[float], volatility: float = 0.20
    ):
        """Generate theoretical prices and Greeks for all strikes at an expiry."""
        for K in strikes:
            for opt_type in (OptionType.CALL, OptionType.PUT):
                contract = OptionContract(self.underlying, K, expiry_days, opt_type)
                price_info = price_option(
                    contract, self.spot, self.risk_free_rate, volatility
                )

                self._chain.append(
                    {
                        "strike": K,
                        "expiry_days": expiry_days,
                        "option_type": opt_type.value,
                        "price": price_info.theoretical,
                        "iv": volatility,
                        "delta": price_info.greeks.delta,
                        "gamma": price_info.greeks.gamma,
                        "theta": price_info.greeks.theta,
                        "vega": price_info.greeks.vega,
                        "rho": price_info.greeks.rho,
                        "vanna": price_info.greeks.vanna,
                        "volga": price_info.greeks.volga,
                    }
                )

    def chain(self, expiry_days: Optional[float] = None) -> list[dict]:
        """Return the chain as a list of dicts with strike, type, price, IV, and all Greeks."""
        if expiry_days is not None:
            return [
                c for c in self._chain if abs(c["expiry_days"] - expiry_days) < 1e-4
            ]
        return self._chain

    def find_atm(self, expiry_days: float) -> dict:
        """Find the at-the-money option (strike closest to spot)."""
        expiry_chain = self.chain(expiry_days)
        if not expiry_chain:
            return {}
        return min(expiry_chain, key=lambda x: abs(x["strike"] - self.spot))

    def max_pain(self, expiry_days: float) -> float:
        """Calculate max pain strike (where option writers lose the least)."""
        expiry_chain = self.chain(expiry_days)
        if not expiry_chain:
            return 0.0

        strikes = sorted(list(set(c["strike"] for c in expiry_chain)))
        best_strike = strikes[0]
        min_pain = float("inf")

        for trial_strike in strikes:
            pain = 0.0
            for opt in expiry_chain:
                # Value of option at expiration if trial_strike is the settlement price
                if opt["option_type"] == OptionType.CALL.value:
                    pain += max(0.0, trial_strike - opt["strike"])
                else:
                    pain += max(0.0, opt["strike"] - trial_strike)

            if pain < min_pain:
                min_pain = pain
                best_strike = trial_strike

        return best_strike
