import sys
import os
import unittest
import tempfile
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backtest import BacktestEngine, BacktestResult, load_events_from_store
from strategy_sdk import Strategy
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store


def _make_events(
    instrument="AAPL", n=100, start_price=100.0, end_price=110.0, start_ts=1000.0
):
    """Generate n TRADE CanonicalEvents with linearly interpolated prices."""
    events = []
    for i in range(n):
        t = start_ts + i * 1.0
        price = start_price + (end_price - start_price) * i / max(n - 1, 1)
        events.append(
            CanonicalEvent(
                event_id=f"evt-{i}",
                instrument_id=instrument,
                event_type=EventType.TRADE,
                exchange_timestamp=t,
                receive_timestamp=t,
                processing_timestamp=t,
                source="TEST",
                sequence_number=i,
                price=price,
                quantity=100.0,
                quality_status=QualityStatus.VALID,
            )
        )
    return events


class BuyOnceStrategy(Strategy):
    def __init__(self, symbol="AAPL", buy_qty=100):
        super().__init__(name="BuyOnce", symbols=[symbol])
        self._symbol = symbol
        self._qty = buy_qty
        self._bought = False

    def on_tick(self, event):
        if not self._bought and event.instrument_id == self._symbol:
            self.buy(self._symbol, self._qty, reason="INITIAL_BUY")
            self._bought = True


class MultiTradeStrategy(Strategy):
    def __init__(self, symbol="AAPL"):
        super().__init__(name="MultiTrade", symbols=[symbol])
        self._symbol = symbol
        self._tick_count = 0

    def on_tick(self, event):
        self._tick_count += 1
        if self._tick_count == 10:
            self.buy(self._symbol, 100, reason="BUY_1")
        elif self._tick_count == 50:
            self.sell(self._symbol, 100, reason="SELL_1")
        elif self._tick_count == 60:
            self.buy(self._symbol, 100, reason="BUY_2")
        elif self._tick_count == 90:
            self.sell(self._symbol, 100, reason="SELL_2")


class ShortStrategy(Strategy):
    def __init__(self, symbol="AAPL"):
        super().__init__(name="ShortStrat", symbols=[symbol])
        self._symbol = symbol
        self._tick_count = 0

    def on_tick(self, event):
        self._tick_count += 1
        if self._tick_count == 10:
            self.sell(self._symbol, 100, reason="SHORT")
        elif self._tick_count == 90:
            self.buy(self._symbol, 100, reason="COVER")


class NoTradeStrategy(Strategy):
    def __init__(self, symbol="AAPL"):
        super().__init__(name="NoTrade", symbols=[symbol])

    def on_tick(self, event):
        pass


class TestBacktestEngine(unittest.TestCase):
    def test_backtest_basic_buy_and_hold(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = _make_events(n=100, start_price=100.0, end_price=110.0)
        strategy = BuyOnceStrategy()

        result = engine.run(strategy, events)

        self.assertGreater(result.final_equity, 100_000.0)
        self.assertGreater(result.total_return_pct, 0.0)
        self.assertEqual(result.total_trades, 1)

    def test_backtest_metrics_correctness(self):
        engine = BacktestEngine(initial_capital=100_000.0)

        events = []
        for i in range(100):
            tick_idx = i + 1
            price = 100.0
            if tick_idx == 10:
                price = 100.0
            elif tick_idx == 50:
                price = 110.0
            elif tick_idx == 60:
                price = 110.0
            elif tick_idx == 90:
                price = 100.0

            events.append(
                CanonicalEvent(
                    event_id=f"evt-{i}",
                    instrument_id="AAPL",
                    event_type=EventType.TRADE,
                    exchange_timestamp=1000.0 + i,
                    receive_timestamp=1000.0 + i,
                    processing_timestamp=1000.0 + i,
                    source="TEST",
                    sequence_number=i,
                    price=price,
                    quantity=100.0,
                    quality_status=QualityStatus.VALID,
                )
            )

        strategy = MultiTradeStrategy()
        result = engine.run(strategy, events)

        self.assertEqual(result.total_trades, 2)
        self.assertEqual(result.winning_trades, 1)
        self.assertEqual(result.losing_trades, 1)
        self.assertEqual(result.win_rate, 50.0)
        self.assertAlmostEqual(result.profit_factor, 1.0)

    def test_backtest_max_drawdown(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = []
        prices = [100.0] * 10 + [120.0] * 10 + [102.0] * 10 + [120.0] * 10
        for i, p in enumerate(prices):
            events.append(
                CanonicalEvent(
                    event_id=f"evt-{i}",
                    instrument_id="AAPL",
                    event_type=EventType.TRADE,
                    exchange_timestamp=1000.0 + i,
                    receive_timestamp=1000.0 + i,
                    processing_timestamp=1000.0 + i,
                    source="TEST",
                    sequence_number=i,
                    price=p,
                    quantity=100.0,
                    quality_status=QualityStatus.VALID,
                )
            )

        strategy = BuyOnceStrategy()
        result = engine.run(strategy, events)

        self.assertTrue(0.0 < result.max_drawdown_pct < 5.0)

    def test_backtest_equity_curve(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = _make_events(n=10, start_price=100.0, end_price=110.0)
        strategy = BuyOnceStrategy()

        result = engine.run(strategy, events)

        self.assertIsInstance(result.equity_curve, list)
        self.assertGreater(len(result.equity_curve), 0)
        self.assertIsInstance(result.equity_curve[0], tuple)
        self.assertEqual(len(result.equity_curve[0]), 2)

        ts = [t for t, e in result.equity_curve]
        self.assertEqual(ts, sorted(ts))

    def test_backtest_sharpe_ratio(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = _make_events(n=100, start_price=100.0, end_price=150.0)
        strategy = BuyOnceStrategy()

        result = engine.run(strategy, events)

        if result.sharpe_ratio is not None and not math.isnan(result.sharpe_ratio):
            self.assertGreaterEqual(result.sharpe_ratio, 0.0)

    def test_backtest_no_trades(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = _make_events(n=50)
        strategy = NoTradeStrategy()

        result = engine.run(strategy, events)

        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.total_return_pct, 0.0)
        self.assertEqual(result.final_equity, 100_000.0)

    def test_backtest_benchmark_comparison(self):
        engine = BacktestEngine(initial_capital=100_000.0, benchmark_symbol="AAPL")
        events = _make_events(n=100, start_price=100.0, end_price=110.0)
        strategy = BuyOnceStrategy()

        result = engine.run(strategy, events)

        self.assertIsNotNone(result.benchmark_return_pct)
        self.assertAlmostEqual(result.benchmark_return_pct, 10.0, places=1)
        self.assertIsNotNone(result.alpha_pct)

    def test_walkforward_splits(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = _make_events(n=500, start_price=100.0, end_price=150.0)

        def strategy_factory():
            return BuyOnceStrategy()

        results = engine.run_walkforward(
            strategy_factory, events, n_splits=3, train_pct=0.6
        )

        self.assertEqual(len(results), 3)
        for res in results:
            self.assertGreaterEqual(res.duration_s, 0.0)
            self.assertIsInstance(res, BacktestResult)

    def test_backtest_short_selling(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = []
        for i in range(100):
            tick_idx = i + 1
            price = 100.0 - (i * 0.5)
            events.append(
                CanonicalEvent(
                    event_id=f"evt-{i}",
                    instrument_id="AAPL",
                    event_type=EventType.TRADE,
                    exchange_timestamp=1000.0 + i,
                    receive_timestamp=1000.0 + i,
                    processing_timestamp=1000.0 + i,
                    source="TEST",
                    sequence_number=i,
                    price=price,
                    quantity=100.0,
                    quality_status=QualityStatus.VALID,
                )
            )

        strategy = ShortStrategy()
        result = engine.run(strategy, events)

        self.assertGreater(result.final_equity, 100_000.0)
        self.assertEqual(result.total_trades, 1)

    def test_load_events_from_store(self):
        temp_dir = tempfile.mkdtemp()
        db_path = os.path.join(temp_dir, "test_store.db")

        try:
            store = Store(db_path)
            events_to_insert = _make_events(n=10)
            store.write_canonical_batch(events_to_insert)
            store.close()

            loaded = load_events_from_store(db_path, instrument_id="AAPL")

            self.assertEqual(len(loaded), 10)
            self.assertEqual(loaded[0].instrument_id, "AAPL")
            self.assertEqual(loaded[0].event_id, "evt-0")
            self.assertEqual(loaded[9].event_id, "evt-9")
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)
            os.rmdir(temp_dir)

    def test_order_book_depth_during_quote_stream(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        events = [
            CanonicalEvent(
                event_id=f"evt-q-{i}",
                instrument_id="AAPL",
                event_type=EventType.QUOTE,
                exchange_timestamp=1000.0 + i,
                receive_timestamp=1000.0 + i,
                processing_timestamp=1000.0 + i,
                source="TEST",
                sequence_number=i,
                bid_price=150.0 - i * 0.05,
                bid_size=200.0,
                ask_price=150.10 + i * 0.05,
                ask_size=300.0,
                quality_status=QualityStatus.VALID,
            )
            for i in range(5)
        ]
        strategy = BuyOnceStrategy()
        result = engine.run(strategy, events)
        book = strategy.get_order_book("AAPL")
        bbo = strategy._current_bbo.get("AAPL")
        self.assertIsNotNone(bbo)
        self.assertEqual(bbo["bid"], 150.0 - 4 * 0.05)
        self.assertEqual(bbo["ask"], 150.10 + 4 * 0.05)
        self.assertEqual(bbo["bid_size"], 200.0)
        self.assertEqual(bbo["ask_size"], 300.0)

    def test_annualized_return_on_complete_capital_loss(self):
        engine = BacktestEngine(initial_capital=100_000.0)
        # Strategy that buys 1000 shares at 100, and price crashes to 0
        events = [
            CanonicalEvent(
                event_id="evt-1",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0,
                receive_timestamp=1000.0,
                processing_timestamp=1000.0,
                source="TEST",
                sequence_number=1,
                price=100.0,
                quantity=1000.0,
                quality_status=QualityStatus.VALID,
            ),
            CanonicalEvent(
                event_id="evt-2",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0 + 86400.0 * 10,  # 10 days later
                receive_timestamp=1000.0 + 86400.0 * 10,
                processing_timestamp=1000.0 + 86400.0 * 10,
                source="TEST",
                sequence_number=2,
                price=0.001,  # 99.999% loss
                quantity=1000.0,
                quality_status=QualityStatus.VALID,
            ),
        ]
        strategy = BuyOnceStrategy(buy_qty=1000)
        result = engine.run(strategy, events)
        self.assertLess(result.total_return_pct, -90.0)
        self.assertEqual(result.annualized_return_pct, -100.0)


if __name__ == "__main__":
    unittest.main()
