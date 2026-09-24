"""
Portfolio-Level Risk Management Engine for MDRAP.

This module implements institutional portfolio risk management including
Value at Risk (VaR - Historical, Parametric, Monte Carlo), Expected Shortfall (CVaR),
drawdown monitoring, correlation analysis, and position limit enforcement.
"""

import math
import statistics
import random
from dataclasses import dataclass
from typing import Any

try:
    import fastpath
except ImportError:
    fastpath = None

__stability__ = "beta"


class ReturnSeries:
    """Tracks a time series of portfolio values and computes returns."""

    def __init__(self) -> None:
        self._values: list[tuple[float, float]] = []  # (timestamp, value)

    def observe(self, timestamp: float, value: float) -> None:
        self._values.append((timestamp, value))

    def returns(self) -> list[float]:
        """Calculates simple returns."""
        if len(self._values) < 2:
            return []
        res = []
        for i in range(1, len(self._values)):
            prev_val = self._values[i - 1][1]
            curr_val = self._values[i][1]
            if prev_val != 0:
                res.append((curr_val - prev_val) / prev_val)
            else:
                res.append(0.0)
        return res

    def log_returns(self) -> list[float]:
        """Calculates log returns: ln(v_t / v_{t-1})."""
        if len(self._values) < 2:
            return []
        res = []
        for i in range(1, len(self._values)):
            prev_val = self._values[i - 1][1]
            curr_val = self._values[i][1]
            if prev_val > 0 and curr_val > 0:
                res.append(math.log(curr_val / prev_val))
            else:
                res.append(0.0)
        return res

    def values(self) -> list[tuple[float, float]]:
        return self._values


class PortfolioRiskEngine:
    def __init__(
        self,
        confidence_level: float = 0.95,
        lookback_window: int = 252,
        initial_capital: float = 100_000.0,
    ) -> None:
        self.confidence_level = confidence_level
        self.lookback_window = lookback_window
        self.initial_capital = initial_capital
        self._series = ReturnSeries()

        # For drawdown tracking
        self._hwm = 0.0
        self._hwm_timestamp = 0.0
        self._max_dd_pct = 0.0
        self._peak_timestamp = 0.0
        self._trough_timestamp = 0.0
        self._current_value = initial_capital
        self._current_timestamp = 0.0
        self._has_observed = False

    def observe(self, timestamp: float, portfolio_value: float) -> None:
        self._series.observe(timestamp, portfolio_value)
        self._current_value = portfolio_value
        self._current_timestamp = timestamp

        if not self._has_observed:
            self._hwm = portfolio_value
            self._hwm_timestamp = timestamp
            self._has_observed = True
            return

        if portfolio_value > self._hwm:
            self._hwm = portfolio_value
            self._hwm_timestamp = timestamp
        elif self._hwm > 0:
            dd = (self._hwm - portfolio_value) / self._hwm * 100.0
            if dd > self._max_dd_pct:
                self._max_dd_pct = dd
                self._peak_timestamp = self._hwm_timestamp
                self._trough_timestamp = timestamp

    def historical_var(self, confidence: float | None = None) -> float:
        """
        Compute Historical Simulation Value at Risk (VaR).

        Mathematical Approach:
            1. Extracts the historical return series over the lookback window.
            2. Sorts returns in ascending order (worst losses at the left tail).
            3. Identifies the empirical quantile rank at (1 - confidence).
            4. VaR = -R_{(1-c)} * Current Portfolio Value.

        Advantages:
            - Non-parametric: makes zero assumptions about underlying distribution.
            - Naturally captures real-world skewness, fat tails, and market crash kurtosis.
        """
        conf = confidence if confidence is not None else self.confidence_level
        returns = self._series.returns()[-self.lookback_window :]
        if not returns:
            return 0.0

        returns.sort()
        idx = int((1.0 - conf) * len(returns))
        idx = max(0, min(idx, len(returns) - 1))

        var_pct = -returns[idx]
        return max(0.0, var_pct * self._current_value)

    def parametric_var(self, confidence: float | None = None) -> float:
        """
        Compute Variance-Covariance (Parametric) Value at Risk (VaR).

        Mathematical Approach:
            Assumes returns follow a Normal distribution: R ~ N(μ, σ²).
            VaR_pct = -[μ + Z_{1-conf} * σ]
            VaR = VaR_pct * Current Portfolio Value

        Properties:
            - Fast O(1) closed-form calculation using sample mean and standard deviation.
            - Relies on Gaussian assumption; may underestimate extreme black-swan tail events.
        """
        conf = confidence if confidence is not None else self.confidence_level
        returns = self._series.returns()[-self.lookback_window :]
        if len(returns) < 2:
            return 0.0

        mu = statistics.mean(returns)
        sigma = statistics.stdev(returns)

        # Standard normal inverse CDF for the specified confidence level
        z_score = statistics.NormalDist().inv_cdf(1.0 - conf)
        var_pct = -(mu + z_score * sigma)
        return max(0.0, var_pct * self._current_value)

    def monte_carlo_var(
        self,
        n_simulations: int = 10000,
        horizon_days: int = 1,
        confidence: float | None = None,
        seed: int = 42,
    ) -> float:
        """
        Compute Monte Carlo Value at Risk (VaR) via forward stochastic simulation.

        Mathematical Approach:
            1. Estimates historical mean μ and standard deviation σ.
            2. Simulates N independent forward return paths over the horizon:
                 R_{sim, i} = ∑_{d=1}^{horizon} ε_d,  where ε_d ~ N(μ, σ)
            3. Sorts simulated paths to find the empirical (1 - conf) quantile loss.

        Invariants:
            - Uses deterministic PRNG seed (default: 42) per Principle 9 ("Deterministic experiments").
            - Hardware acceleration delegates to native C fastpath if available.
        """
        conf = confidence if confidence is not None else self.confidence_level
        returns = self._series.returns()[-self.lookback_window :]
        if len(returns) < 2:
            return 0.0

        mu = statistics.mean(returns)
        sigma = statistics.stdev(returns)

        if fastpath is not None:
            c_res = fastpath.fast_monte_carlo_var(
                mu, sigma, n_simulations, horizon_days, self._current_value, conf, seed
            )
            if c_res is not None:
                return c_res

        rng = random.Random(seed)
        simulated_returns = []
        for _ in range(n_simulations):
            sim_ret = 0.0
            for _ in range(horizon_days):
                sim_ret += rng.gauss(mu, sigma)
            simulated_returns.append(sim_ret)

        simulated_returns.sort()
        idx = int((1.0 - conf) * len(simulated_returns))
        idx = max(0, min(idx, len(simulated_returns) - 1))

        var_pct = -simulated_returns[idx]
        return max(0.0, var_pct * self._current_value)

    def expected_shortfall(self, confidence: float | None = None) -> float:
        """
        Compute Expected Shortfall (ES / Conditional VaR / CVaR).

        Mathematical Approach:
            ES_α = -E[R | R ≤ VaR_α]
            Represents the expected loss given that a tail breach beyond the VaR threshold has occurred.

        Significance:
            - ES is a mathematically 'coherent' risk measure satisfying sub-additivity:
                ES(A + B) ≤ ES(A) + ES(B) (unlike VaR, which can violate diversification benefits).
            - Always strictly greater than or equal to VaR for identical confidence levels.
        """
        conf = confidence if confidence is not None else self.confidence_level
        returns = self._series.returns()[-self.lookback_window :]
        if not returns:
            return 0.0

        returns.sort()
        idx = int((1.0 - conf) * len(returns))
        idx = max(0, min(idx, len(returns) - 1))

        # Tail partition: all returns worse than or equal to the VaR cutoff
        tail_returns = returns[: idx + 1] if idx >= 0 else []
        if not tail_returns:
            return 0.0

        es_pct = -statistics.mean(tail_returns)
        return max(0.0, es_pct * self._current_value)

    def max_drawdown(self) -> tuple[float, float, float]:
        return (self._max_dd_pct, self._peak_timestamp, self._trough_timestamp)

    def current_drawdown(self) -> float:
        if self._hwm <= 0:
            return 0.0
        return max(0.0, (self._hwm - self._current_value) / self._hwm * 100.0)

    def sharpe_ratio(
        self, risk_free_rate: float = 0.0, annualization_factor: float = 252.0
    ) -> float:
        returns = self._series.returns()
        if len(returns) < 2:
            return 0.0

        excess_returns = [r - risk_free_rate for r in returns]
        mean_excess = statistics.mean(excess_returns)
        stdev = statistics.stdev(returns)

        if stdev == 0:
            return 0.0

        return (mean_excess / stdev) * math.sqrt(annualization_factor)

    def sortino_ratio(
        self, risk_free_rate: float = 0.0, annualization_factor: float = 252.0
    ) -> float:
        returns = self._series.returns()
        if len(returns) < 2:
            return 0.0

        excess_returns = [r - risk_free_rate for r in returns]
        mean_excess = statistics.mean(excess_returns)

        downside_returns = [min(0.0, r - risk_free_rate) for r in returns]
        downside_variance = (
            sum(r**2 for r in downside_returns) / (len(downside_returns) - 1)
            if len(downside_returns) > 1
            else 0.0
        )

        if downside_variance == 0:
            return 0.0

        downside_deviation = math.sqrt(downside_variance)
        return (mean_excess / downside_deviation) * math.sqrt(annualization_factor)

    def summary(self) -> dict[str, Any]:
        return {
            "current_value": self._current_value,
            "historical_var": self.historical_var(),
            "parametric_var": self.parametric_var(),
            "monte_carlo_var": self.monte_carlo_var(),
            "expected_shortfall": self.expected_shortfall(),
            "max_drawdown": self._max_dd_pct,
            "current_drawdown": self.current_drawdown(),
            "sharpe_ratio": self.sharpe_ratio(),
            "sortino_ratio": self.sortino_ratio(),
        }


class CorrelationMatrix:
    """Computes pairwise Pearson correlation between asset return series."""

    def __init__(self) -> None:
        self._series: dict[str, list[float]] = {}

    def observe(self, symbol: str, return_value: float) -> None:
        if symbol not in self._series:
            self._series[symbol] = []
        self._series[symbol].append(return_value)

    def compute(self) -> dict[tuple[str, str], float]:
        correlations: dict[tuple[str, str], float] = {}
        symbols = list(self._series.keys())

        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                sym_a = symbols[i]
                sym_b = symbols[j]

                rets_a = self._series[sym_a]
                rets_b = self._series[sym_b]

                min_len = min(len(rets_a), len(rets_b))
                if min_len < 2:
                    continue

                a = rets_a[-min_len:]
                b = rets_b[-min_len:]

                try:
                    corr = statistics.correlation(a, b)
                    correlations[(sym_a, sym_b)] = corr
                    correlations[(sym_b, sym_a)] = corr
                except statistics.StatisticsError:
                    pass

        return correlations

    def most_correlated(self, n: int = 5) -> list[tuple[str, str, float]]:
        correlations = self.compute()
        # Deduplicate pairs
        seen = set()
        unique_corrs = []
        for (sym_a, sym_b), corr in correlations.items():
            pair = tuple(sorted([sym_a, sym_b]))
            if pair not in seen:
                seen.add(pair)
                unique_corrs.append((sym_a, sym_b, corr))

        unique_corrs.sort(key=lambda x: abs(x[2]), reverse=True)
        return unique_corrs[:n]

    def concentration_risk(self) -> float:
        top_corrs = self.most_correlated(n=10)
        if not top_corrs:
            return 0.0
        return sum(c[2] for c in top_corrs) / len(top_corrs)


@dataclass
class PositionLimits:
    max_per_symbol: float = 10_000.0  # max notional per symbol
    max_gross_exposure: float = 500_000.0  # total |long| + |short|
    max_net_exposure: float = 200_000.0  # |sum of positions|
    max_sector_pct: float = 30.0  # max % of portfolio in one sector
    max_single_name_pct: float = 15.0  # max % in any single name


class DrawdownCircuitBreaker:
    """Monitors drawdown and triggers emergency actions."""

    def __init__(
        self,
        warning_pct: float = 3.0,
        critical_pct: float = 5.0,
        kill_pct: float = 10.0,
    ) -> None:
        self.warning_pct = warning_pct
        self.critical_pct = critical_pct
        self.kill_pct = kill_pct
        self._level = "OK"

    def check(self, current_drawdown_pct: float) -> str:
        if current_drawdown_pct >= self.kill_pct:
            self._level = "KILL"
        elif current_drawdown_pct >= self.critical_pct:
            self._level = "CRITICAL"
        elif current_drawdown_pct >= self.warning_pct:
            self._level = "WARNING"
        else:
            self._level = "OK"
        return self._level

    @property
    def triggered(self) -> bool:
        return self._level in ("CRITICAL", "KILL")

    @property
    def level(self) -> str:
        return self._level
