"""
ML/AI Feature Store for MDRAP.

This module provides point-in-time calculation of technical indicators and
microstructure features for quantitative research and machine learning models.
"""

import math
import statistics
from typing import Callable, Any

try:
    import fastpath
except ImportError:
    fastpath = None

__stability__ = "beta"


def sma(prices: list[float], period: int) -> list[float]:
    """Simple Moving Average (O(N) running sum)"""
    if not prices or period <= 0:
        return []
    result = [math.nan] * min(len(prices), period - 1)
    if len(prices) < period:
        return result
    window_sum = sum(prices[:period])
    result.append(window_sum / period)
    for i in range(period, len(prices)):
        if i % 10000 == 0:
            window_sum = sum(prices[i - period + 1 : i + 1])
        else:
            window_sum += prices[i] - prices[i - period]
        result.append(window_sum / period)
    return result


def ema(prices: list[float], period: int) -> list[float]:
    """Exponential Moving Average"""
    if fastpath is not None and prices:
        c_ema = fastpath.fast_calc_ema(prices, period)
        if c_ema is not None:
            return c_ema

    result = []
    multiplier = 2.0 / (period + 1)
    current_ema = None
    for i, price in enumerate(prices):
        if i < period - 1:
            result.append(math.nan)
        elif i == period - 1:
            current_ema = sum(prices[0:period]) / period
            result.append(current_ema)
        else:
            current_ema = (price - current_ema) * multiplier + current_ema
            result.append(current_ema)
    return result


def rsi(prices: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index (O(1) memory scalar recurrence)"""
    if not prices:
        return []

    if fastpath is not None:
        c_rsi = fastpath.fast_calc_rsi(prices, period)
        if c_rsi is not None:
            return c_rsi

    result = [math.nan]
    if len(prices) == 1:
        return result

    init_gain = 0.0
    init_loss = 0.0
    avg_gain = 0.0
    avg_loss = 0.0

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0

        if i < period:
            init_gain += gain
            init_loss += loss
            result.append(math.nan)
        elif i == period:
            init_gain += gain
            init_loss += loss
            avg_gain = init_gain / period
            avg_loss = init_loss / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100.0 - (100.0 / (1.0 + rs)))
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100.0 - (100.0 / (1.0 + rs)))
    return result


def macd(
    prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]]:
    """MACD line, Signal line, Histogram"""
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = []
    for f, s in zip(ema_fast, ema_slow):
        if math.isnan(f) or math.isnan(s):
            macd_line.append(math.nan)
        else:
            macd_line.append(f - s)

    macd_valid_idx = slow - 1
    if macd_valid_idx >= len(macd_line):
        return macd_line, [math.nan] * len(prices), [math.nan] * len(prices)

    macd_valid = macd_line[macd_valid_idx:]
    sig = ema(macd_valid, signal)

    signal_line = [math.nan] * macd_valid_idx + sig
    histogram = []
    for m, s in zip(macd_line, signal_line):
        if math.isnan(m) or math.isnan(s):
            histogram.append(math.nan)
        else:
            histogram.append(m - s)

    return macd_line, signal_line, histogram


def bollinger_bands(
    prices: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float], list[float], list[float]]:
    """Bollinger Bands (Upper, Middle/SMA, Lower)"""
    if fastpath is not None and prices:
        c_bb = fastpath.fast_calc_bollinger(prices, period, num_std)
        if c_bb is not None:
            return c_bb

    middle = sma(prices, period)
    upper = []
    lower = []
    for i in range(len(prices)):
        if i < period - 1:
            upper.append(math.nan)
            lower.append(math.nan)
        else:
            window = prices[i - period + 1 : i + 1]
            stdev = statistics.stdev(window) if len(window) > 1 else 0.0
            upper.append(middle[i] + num_std * stdev)
            lower.append(middle[i] - num_std * stdev)
    return upper, middle, lower


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float]:
    """Average True Range"""
    if fastpath is not None and highs and len(highs) == len(lows) == len(closes):
        c_atr = fastpath.fast_calc_atr(highs, lows, closes, period)
        if c_atr is not None:
            return c_atr

    tr = []
    for i in range(len(highs)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            tr.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

    result = []
    current_atr = None
    for i in range(len(tr)):
        if i < period - 1:
            result.append(math.nan)
        elif i == period - 1:
            current_atr = sum(tr[0:period]) / period
            result.append(current_atr)
        else:
            current_atr = (current_atr * (period - 1) + tr[i]) / period
            result.append(current_atr)
    return result


def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """On-Balance Volume"""
    if not closes:
        return []
    result = [volumes[0]]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            result.append(result[-1] - volumes[i])
        else:
            result.append(result[-1])
    return result


def vpin(trades: list[tuple[float, float]], bucket_volume: float = 1000.0) -> float:
    """Volume-Synchronized Probability of Toxicity from price & qty"""
    if not trades:
        return math.nan
    buy_vol = 0.0
    sell_vol = 0.0
    prev_price = trades[0][0]
    for price, qty in trades:
        if price > prev_price:
            buy_vol += qty
        elif price < prev_price:
            sell_vol += qty
        else:
            buy_vol += qty / 2.0
            sell_vol += qty / 2.0
        prev_price = price

    total_vol = buy_vol + sell_vol
    if total_vol == 0:
        return 0.0
    return abs(buy_vol - sell_vol) / total_vol


def order_book_imbalance(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]], levels: int = 5
) -> float:
    """Order book imbalance from bids and asks"""
    bid_vol = sum(qty for price, qty in bids[:levels])
    ask_vol = sum(qty for price, qty in asks[:levels])
    total = bid_vol + ask_vol
    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def realized_volatility(prices: list[float], window: int = 20) -> float:
    """Realized volatility of prices"""
    if len(prices) < 2:
        return math.nan
    returns = []
    prices_window = prices[-window - 1 :] if len(prices) > window else prices
    for i in range(1, len(prices_window)):
        returns.append(math.log(prices_window[i] / prices_window[i - 1]))
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns)


class FeatureRegistry:
    """Registry of computable features with metadata."""

    def __init__(self):
        self._features: dict[str, dict] = {}

    def register(
        self,
        name: str,
        compute_fn: Callable,
        description: str = "",
        lag_periods: int = 1,
    ):
        self._features[name] = {
            "compute_fn": compute_fn,
            "description": description,
            "lag_periods": lag_periods,
        }

    def get(self, name: str) -> dict | None:
        return self._features.get(name)

    def list_all(self) -> list[dict]:
        return [{"name": k, **v} for k, v in self._features.items()]


class FeatureStore:
    """Point-in-time feature computation and storage engine."""

    def __init__(self, db_path: str | None = None):
        self.registry = FeatureRegistry()
        self.db_path = db_path
        self._init_defaults()

    def _init_defaults(self):
        self.registry.register(
            "rsi_14",
            lambda bars: rsi([self._get_field(b, "close") for b in bars], 14),
            "RSI 14",
        )
        self.registry.register(
            "macd",
            lambda bars: macd([self._get_field(b, "close") for b in bars]),
            "MACD",
        )
        self.registry.register(
            "bb_20",
            lambda bars: bollinger_bands(
                [self._get_field(b, "close") for b in bars], 20
            ),
            "Bollinger Bands 20",
        )
        self.registry.register(
            "atr_14",
            lambda bars: atr(
                [self._get_field(b, "high") for b in bars],
                [self._get_field(b, "low") for b in bars],
                [self._get_field(b, "close") for b in bars],
                14,
            ),
            "ATR 14",
        )
        self.registry.register(
            "obv",
            lambda bars: obv(
                [self._get_field(b, "close") for b in bars],
                [self._get_field(b, "volume") for b in bars],
            ),
            "OBV",
        )

    def _get_field(self, bar: Any, field: str) -> float:
        if isinstance(bar, dict):
            return float(bar.get(field, 0.0))
        return float(getattr(bar, field, 0.0))

    def _get_timestamp(self, bar: Any) -> Any:
        if isinstance(bar, dict):
            return bar.get("timestamp") or bar.get("time")
        return getattr(bar, "timestamp", getattr(bar, "time", None))

    def compute_features(self, bars: list[dict | Any]) -> dict[str, list[float]]:
        """Computes all registered features and returns dict {feature_name: [values]}"""
        results = {}
        for feature in self.registry.list_all():
            name = feature["name"]
            compute_fn = feature["compute_fn"]

            val = compute_fn(bars)

            # Handle tuple returns (like MACD, BB)
            if isinstance(val, tuple) and name == "macd":
                results["macd"], results["macd_signal"], results["macd_hist"] = val
            elif isinstance(val, tuple) and name == "bb_20":
                results["bb_upper"], results["bb_middle"], results["bb_lower"] = val
            else:
                results[name] = val

        return results

    def to_records(self, bars: list[dict | Any]) -> list[dict]:
        """Returns list of dicts suitable for DataFrame creation or scikit-learn"""
        if not bars:
            return []

        features = self.compute_features(bars)
        records = []

        for i in range(len(bars)):
            record = {
                "timestamp": self._get_timestamp(bars[i]),
                "open": self._get_field(bars[i], "open"),
                "high": self._get_field(bars[i], "high"),
                "low": self._get_field(bars[i], "low"),
                "close": self._get_field(bars[i], "close"),
                "volume": self._get_field(bars[i], "volume"),
            }

            for k, v_list in features.items():
                if i < len(v_list):
                    record[k] = v_list[i]
                else:
                    record[k] = math.nan

            records.append(record)

        return records
