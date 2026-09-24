from __future__ import annotations

import math

from models import CanonicalEvent, EventType, QualityStatus

__stability__ = "beta"


class OHLCVAggregator:
    """
    Incremental real-time OHLCV candlestick aggregator (Spec §14).

    Rolls up streaming trade ticks into fixed-window temporal candles (e.g. 5s, 60s).
    Design Invariants:
    1. Zero Allocation Leaks: Updates bucket stats in-place without storing raw tick arrays.
    2. Out-of-Order Timestamp Robustness: Tracks `_first_ts` and `_last_ts` per bucket to ensure
       that `open` and `close` accurately reflect the earliest and latest exchange events,
       even if network packets arrive with minor arrival jitter.
    3. Strict Quality Isolation: Rejects INVALID events to ensure analytical metrics
       remain untainted by corrupt feed frames.
    """

    def __init__(self, interval_s: float = 5.0):
        self.interval_s = interval_s
        # Dict key: (instrument_id, bucket_start_epoch)
        # Value: dict holding the raw OHLCV calculations
        self._buckets: dict[tuple[str, float], dict] = {}

    def observe(self, event: CanonicalEvent) -> None:
        """Process incoming trade event into the appropriate time bucket."""
        if event.event_type != EventType.TRADE or event.price is None:
            return
        if event.quality_status == QualityStatus.INVALID:
            return

        # Discrete temporal bucketing aligned to standard epoch boundaries
        bucket_start = float(
            int(event.exchange_timestamp // self.interval_s) * self.interval_s
        )
        key = (event.instrument_id, bucket_start)

        if key not in self._buckets:
            self._buckets[key] = {
                "instrument_id": event.instrument_id,
                "bucket_start": bucket_start,
                "interval_s": self.interval_s,
                "open": event.price,
                "high": event.price,
                "low": event.price,
                "close": event.price,
                "volume": event.quantity if event.quantity is not None else 0.0,
                "event_count": 1,
                "_first_ts": event.exchange_timestamp,
                "_last_ts": event.exchange_timestamp,
            }
        else:
            b = self._buckets[key]
            first_ts = b.get("_first_ts", b.get("bucket_start", 0.0))
            last_ts = b.get("_last_ts", b.get("bucket_start", 0.0))
            if event.exchange_timestamp < first_ts:
                b["open"] = event.price
                b["_first_ts"] = event.exchange_timestamp
            if event.exchange_timestamp >= last_ts:
                b["close"] = event.price
                b["_last_ts"] = event.exchange_timestamp

            b["high"] = max(b["high"], event.price)
            b["low"] = min(b["low"], event.price)
            if event.quantity is not None:
                b["volume"] += event.quantity
            b["event_count"] += 1

    def _format_candle(self, b: dict) -> dict:
        return {
            "instrument_id": b["instrument_id"],
            "bucket_start": b["bucket_start"],
            "interval_s": b["interval_s"],
            "open": b["open"],
            "high": b["high"],
            "low": b["low"],
            "close": b["close"],
            "volume": b["volume"],
            "event_count": b["event_count"],
        }

    def candles(self) -> list[dict]:
        sorted_keys = sorted(self._buckets.keys())
        return [self._format_candle(self._buckets[k]) for k in sorted_keys]

    def candles_for(self, instrument_id: str) -> list[dict]:
        res = []
        for k in sorted(self._buckets.keys()):
            if k[0] == instrument_id:
                res.append(self._format_candle(self._buckets[k]))
        return res


class SpreadAnalyzer:
    """
    Market microstructure bid-ask spread analytics engine.

    Monitors liquidity health and quote anomalies in real time:
    - Quoted Spread: Spread = P_ask - P_bid
    - Crossed Market Detection: P_bid > P_ask (indicates latency divergence or venue arbitrage)
    - Spread Distribution: Tracks min, max, and running mean spread per instrument.
    """

    def __init__(self):
        self._stats: dict[str, dict] = {}

    def observe(self, event: CanonicalEvent) -> None:
        """Evaluate top-of-book quote event and update running spread statistics."""
        if (
            event.event_type != EventType.QUOTE
            or event.bid_price is None
            or event.ask_price is None
        ):
            return
        if not math.isfinite(event.bid_price) or not math.isfinite(event.ask_price):
            return

        spread = event.ask_price - event.bid_price
        crossed = 1 if event.bid_price > event.ask_price else 0

        if event.instrument_id not in self._stats:
            self._stats[event.instrument_id] = {
                "quote_count": 1,
                "sum_spread": spread,
                "min_spread": spread,
                "max_spread": spread,
                "crossed_count": crossed,
            }
        else:
            s = self._stats[event.instrument_id]
            s["quote_count"] += 1
            s["sum_spread"] += spread
            s["min_spread"] = min(s["min_spread"], spread)
            s["max_spread"] = max(s["max_spread"], spread)
            s["crossed_count"] += crossed

    def summary(self) -> list[dict]:
        """Compile aggregate spread analytics across all tracked instruments."""
        res = []
        for instr, s in self._stats.items():
            count = s["quote_count"]
            mean_spread = s["sum_spread"] / count if count > 0 else 0.0
            crossed_pct = (s["crossed_count"] / count * 100) if count > 0 else 0.0
            res.append(
                {
                    "instrument_id": instr,
                    "quote_count": count,
                    "mean_spread": mean_spread,
                    "min_spread": s["min_spread"],
                    "max_spread": s["max_spread"],
                    "crossed_count": s["crossed_count"],
                    "crossed_pct": crossed_pct,
                }
            )
        return res


class VolatilityTracker:
    """
    Streaming realized volatility tracker using Welford's algorithm (1962).

    Mathematical Foundation:
        Traditional two-pass or naive one-pass algorithms (sum(x²) - sum(x)²/n) suffer from
        catastrophic floating-point cancellation when prices are large (e.g. BTC at $60,000+).
        Welford's algorithm computes variance recurrence relations online in a single pass:
            M_{1, n} = M_{1, n-1} + (x_n - M_{1, n-1}) / n
            M_{2, n} = M_{2, n-1} + (x_n - M_{1, n-1}) * (x_n - M_{1, n})
            Sample Variance = M_{2, n} / n
            Sample StdDev = √(M_{2, n} / n)

    Guarantees:
        - O(1) time and O(1) space per update.
        - High numerical precision without risk of negative variance due to roundoff errors.
    """

    def __init__(self, window: int = 100):
        self.window = window
        self._stats: dict[str, dict] = {}

    def observe(self, event: CanonicalEvent) -> None:
        """Feed a canonical trade tick into the streaming Welford estimator."""
        if event.event_type != EventType.TRADE or event.price is None:
            return
        if not math.isfinite(event.price) or event.price <= 0:
            return

        p = event.price
        instr = event.instrument_id

        if instr not in self._stats:
            self._stats[instr] = {
                "count": 1,
                "mean": p,
                "M2": 0.0,
                "min_price": p,
                "max_price": p,
            }
        else:
            s = self._stats[instr]
            s["count"] += 1
            # Step 1: Compute deviation from prior mean
            delta = p - s["mean"]
            # Step 2: Update mean incrementally
            s["mean"] += delta / s["count"]
            # Step 3: Compute deviation from new mean and accumulate M2
            delta2 = p - s["mean"]
            s["M2"] += delta * delta2
            s["min_price"] = min(s["min_price"], p)
            s["max_price"] = max(s["max_price"], p)

    def summary(self) -> list[dict]:
        res = []
        for instr, s in self._stats.items():
            count = s["count"]
            mean = s["mean"]
            std_dev = math.sqrt(s["M2"] / count) if count > 0 else 0.0
            price_range_pct = (
                ((s["max_price"] - s["min_price"]) / mean * 100) if mean > 0 else 0.0
            )

            res.append(
                {
                    "instrument_id": instr,
                    "trade_count": count,
                    "mean_price": mean,
                    "std_dev": std_dev,
                    "min_price": s["min_price"],
                    "max_price": s["max_price"],
                    "price_range_pct": price_range_pct,
                }
            )
        return res


class MarketAnalytics:
    def __init__(self, ohlcv_interval_s: float = 5.0, volatility_window: int = 100):
        self.ohlcv = OHLCVAggregator(interval_s=ohlcv_interval_s)
        self.spreads = SpreadAnalyzer()
        self.volatility = VolatilityTracker(window=volatility_window)

    def observe(self, event: CanonicalEvent) -> None:
        self.ohlcv.observe(event)
        self.spreads.observe(event)
        self.volatility.observe(event)

    def full_summary(self) -> dict:
        return {
            "ohlcv": self.ohlcv.candles(),
            "spreads": self.spreads.summary(),
            "volatility": self.volatility.summary(),
        }
