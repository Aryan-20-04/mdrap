"""
Historical Backtesting Engine for MDRAP (Spec §18, §26).

Implements event-driven point-in-time historical backtesting:
- Replays canonical trade/quote event streams through algorithmic strategies (`StrategyRunner` / `PaperExecutor`).
- Point-in-time market state reconstruction (synthesizing BBO, depth rungs, and whale print detection).
- Periodic equity curve sampling and drawdown tracking.
- Risk-adjusted return analytics:
    * Annualized Return (compounded or linear depending on horizon)
    * Annualized Sharpe Ratio: (mean(R) / stdev(R)) * sqrt(annualization_factor)
    * Annualized Sortino Ratio: (mean(R) / downside_stdev(R)) * sqrt(annualization_factor)
    * Calmar Ratio: Annualized Return / Max Drawdown
    * Profit Factor: Gross Profit / Gross Loss
- Anchored Walk-Forward out-of-sample cross-validation splits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import math
import statistics
import time

from models import CanonicalEvent, EventType, QualityStatus
from strategy_sdk import PaperExecutor, Strategy, StrategyRunner

__stability__ = "experimental"


@dataclass
class BacktestResult:
    strategy_name: str
    start_time: float
    end_time: float
    duration_s: float
    initial_capital: float
    final_equity: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    max_drawdown_duration_s: float
    sharpe_ratio: float  # annualized, rf=0
    sortino_ratio: float  # annualized, rf=0
    calmar_ratio: float  # annualized return / max drawdown
    win_rate: float  # percentage of winning trades
    profit_factor: float  # gross profit / gross loss
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_trade_duration_s: float
    equity_curve: list[tuple[float, float]]  # (timestamp, equity)
    drawdown_curve: list[tuple[float, float]]  # (timestamp, drawdown_pct)
    trades: list[dict]  # execution ledger from strategy
    benchmark_return_pct: float  # buy-and-hold return
    alpha_pct: float  # strategy return - benchmark return


class BacktestEngine:
    def __init__(
        self, initial_capital: float = 100_000.0, benchmark_symbol: str | None = None
    ):
        self.initial_capital = initial_capital
        self.benchmark_symbol = benchmark_symbol

    def run(self, strategy: Strategy, events: list[CanonicalEvent]) -> BacktestResult:
        if not events:
            raise ValueError("No events provided for backtest")

        # Sort events chronologically
        events = sorted(events, key=lambda e: e.exchange_timestamp)
        start_time = events[0].exchange_timestamp
        end_time = events[-1].exchange_timestamp

        # Setup paper executor
        strategy.executor = PaperExecutor(initial_cash=self.initial_capital)

        # Track benchmark
        first_benchmark_price = None
        last_benchmark_price = None

        equity_curve = []
        last_sample_time = 0.0
        duration_range = end_time - start_time
        sample_interval = (
            max(0.1, duration_range / 200.0) if duration_range > 0 else 1.0
        )

        runner = StrategyRunner(strategy)
        runner.strategy.on_start()

        real_start_time = time.time()
        latest_prices: dict[str, float] = {}
        has_quote_feed: set[str] = set()

        for event in events:
            if self.benchmark_symbol and event.instrument_id == self.benchmark_symbol:
                if first_benchmark_price is None:
                    first_benchmark_price = event.price
                last_benchmark_price = event.price

            # Track latest prices and update market state for equity and order execution
            if event.event_type == EventType.TRADE and event.price is not None:
                latest_prices[event.instrument_id] = event.price
                runner.strategy._current_mid[event.instrument_id] = event.price
                if event.instrument_id not in has_quote_feed:
                    runner.strategy._current_bbo[event.instrument_id] = {
                        "bid": event.price,
                        "ask": event.price,
                        "bid_size": event.quantity or 1000.0,
                        "ask_size": event.quantity or 1000.0,
                    }
                    runner.strategy.get_order_book(event.instrument_id).update_quote(
                        event.price,
                        event.price,
                        event.quantity or 1000.0,
                        event.quantity or 1000.0,
                        event.exchange_timestamp,
                    )
                # Dispatch tick
                runner.strategy.on_tick(event)

                # Check for whale block print
                if event.quantity:
                    notional = event.price * event.quantity
                    if notional >= 100_000.0 or event.quantity >= 500:
                        mid_ref = runner.strategy._current_mid.get(
                            event.instrument_id, event.price
                        )
                        whale_info = {
                            "instrument": event.instrument_id,
                            "price": event.price,
                            "quantity": event.quantity,
                            "notional": notional,
                            "side": "BUY" if event.price >= mid_ref else "SELL",
                            "timestamp": event.exchange_timestamp,
                        }
                        runner.strategy.on_whale(whale_info)

            elif event.event_type == EventType.QUOTE:
                if event.bid_price is not None and event.ask_price is not None:
                    has_quote_feed.add(event.instrument_id)
                    mid = (event.bid_price + event.ask_price) / 2.0
                    latest_prices[event.instrument_id] = mid
                    runner.strategy._current_mid[event.instrument_id] = mid
                    runner.strategy._current_bbo[event.instrument_id] = {
                        "bid": event.bid_price,
                        "ask": event.ask_price,
                        "bid_size": event.bid_size or 1000.0,
                        "ask_size": event.ask_size or 1000.0,
                    }
                    runner.strategy.get_order_book(event.instrument_id).update_quote(
                        event.bid_price,
                        event.ask_price,
                        event.bid_size or 1000.0,
                        event.ask_size or 1000.0,
                        event.exchange_timestamp,
                    )
                runner.strategy.on_quote(event)

            # Sample equity
            if event.exchange_timestamp - last_sample_time >= sample_interval:
                equity = runner.strategy.executor.total_equity(latest_prices)
                equity_curve.append((event.exchange_timestamp, equity))
                last_sample_time = event.exchange_timestamp

        runner.strategy.on_stop()

        real_duration = time.time() - real_start_time

        # Get trades and final equity
        trades = runner.strategy.get_execution_ledger()
        final_equity = runner.strategy.executor.total_equity(latest_prices)
        if not equity_curve or equity_curve[-1][0] != end_time:
            equity_curve.append((end_time, final_equity))

        # Compute metrics
        total_return = (final_equity - self.initial_capital) / self.initial_capital
        duration_days = (end_time - start_time) / 86400.0

        if duration_days >= 1.0:
            if 1.0 + total_return <= 0.0:
                annualized_return = -1.0
            else:
                try:
                    annualized_return = (
                        (1.0 + total_return) ** (365.25 / duration_days)
                    ) - 1.0
                except OverflowError:
                    annualized_return = total_return * (365.25 / duration_days)
        elif duration_days > 0:
            annualized_return = total_return * (365.25 / duration_days)
        else:
            annualized_return = 0.0

        # Drawdown and returns for Sharpe/Sortino
        drawdown_curve = []
        max_drawdown = 0.0
        peak_equity = self.initial_capital
        current_dd_start = start_time
        max_dd_duration = 0.0

        returns = []
        prev_equity = self.initial_capital

        for ts, eq in equity_curve:
            if eq > prev_equity:
                ret = (eq - prev_equity) / prev_equity
            else:
                ret = (eq - prev_equity) / prev_equity if prev_equity > 0 else 0.0
            returns.append(ret)
            prev_equity = eq

            if eq > peak_equity:
                peak_equity = eq
                current_dd_start = ts

            dd = (peak_equity - eq) / peak_equity if peak_equity > 0 else 0.0
            drawdown_curve.append((ts, dd * 100))

            if dd > max_drawdown:
                max_drawdown = dd
                max_dd_duration = max(max_dd_duration, ts - current_dd_start)

        # Sharpe / Sortino
        annualize_factor = math.sqrt(252 * 6.5 * 3600 / sample_interval)

        if len(returns) > 1 and statistics.stdev(returns) > 0:
            sharpe_ratio = (
                statistics.mean(returns) / statistics.stdev(returns)
            ) * annualize_factor
            downside_returns = [r for r in returns if r < 0]
            if len(downside_returns) >= 2 and statistics.stdev(downside_returns) > 0:
                sortino_ratio = (
                    statistics.mean(returns) / statistics.stdev(downside_returns)
                ) * annualize_factor
            elif len(downside_returns) == 1 and abs(downside_returns[0]) > 0:
                sortino_ratio = (
                    statistics.mean(returns) / abs(downside_returns[0])
                ) * annualize_factor
            else:
                sortino_ratio = float("inf") if statistics.mean(returns) > 0 else 0.0
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0

        calmar_ratio = (
            (annualized_return * 100) / (max_drawdown * 100)
            if max_drawdown > 0
            else float("inf")
        )

        # Trade metrics
        winning_trades = 0
        losing_trades = 0
        gross_profit = 0.0
        gross_loss = 0.0
        largest_win = 0.0
        largest_loss = 0.0

        trade_pnls = []
        last_realized = 0.0
        for t in trades:
            curr_realized = t.get("realized_pnl", 0.0)
            diff = curr_realized - last_realized
            if abs(diff) > 1e-9:
                trade_pnls.append(diff)
                last_realized = curr_realized

        if trade_pnls:
            for pnl in trade_pnls:
                if pnl > 0:
                    winning_trades += 1
                    gross_profit += pnl
                    largest_win = max(largest_win, pnl)
                else:
                    losing_trades += 1
                    gross_loss += abs(pnl)
                    largest_loss = max(largest_loss, abs(pnl))

        total_closed_trades = winning_trades + losing_trades
        total_trades = total_closed_trades if total_closed_trades > 0 else len(trades)
        win_rate = (
            (winning_trades / total_closed_trades * 100)
            if total_closed_trades > 0
            else 0.0
        )
        profit_factor = (
            (gross_profit / gross_loss)
            if gross_loss > 0
            else (float("inf") if gross_profit > 0 else 0.0)
        )

        avg_win = gross_profit / winning_trades if winning_trades > 0 else 0.0
        avg_loss = gross_loss / losing_trades if losing_trades > 0 else 0.0

        benchmark_return = 0.0
        if first_benchmark_price and last_benchmark_price:
            benchmark_return = (
                last_benchmark_price - first_benchmark_price
            ) / first_benchmark_price

        alpha = (total_return - benchmark_return) * 100

        return BacktestResult(
            strategy_name=strategy.__class__.__name__,
            start_time=start_time,
            end_time=end_time,
            duration_s=real_duration,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            total_return_pct=total_return * 100,
            annualized_return_pct=annualized_return * 100,
            max_drawdown_pct=max_drawdown * 100,
            max_drawdown_duration_s=max_dd_duration,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            calmar_ratio=calmar_ratio,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            avg_trade_duration_s=0.0,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            trades=trades,
            benchmark_return_pct=benchmark_return * 100,
            alpha_pct=alpha,
        )

    def run_walkforward(
        self,
        strategy_factory: Callable[[], Strategy],
        events: list[CanonicalEvent],
        n_splits: int = 5,
        train_pct: float = 0.6,
    ) -> list[BacktestResult]:
        if not events:
            return []

        events = sorted(events, key=lambda e: e.exchange_timestamp)
        total_events = len(events)
        window_size = total_events // n_splits

        results = []
        for i in range(n_splits):
            start_idx = i * window_size
            end_idx = start_idx + window_size if i < n_splits - 1 else total_events
            window_events = events[start_idx:end_idx]

            if not window_events:
                continue

            train_size = int(len(window_events) * train_pct)
            test_events = window_events[train_size:]

            if not test_events:
                continue

            strategy = strategy_factory()
            res = self.run(strategy, test_events)
            results.append(res)

        return results


def load_events_from_store(
    db_path: str, instrument_id: str | None = None
) -> list[CanonicalEvent]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        query = (
            "SELECT event_id, instrument_id, event_type, exchange_timestamp, "
            "receive_timestamp, processing_timestamp, source, sequence_number, "
            "price, quantity, bid_price, bid_size, ask_price, ask_size, "
            "quality_status, reasons, raw_id FROM canonical_events"
        )
        params: list[str] = []
        if instrument_id:
            query += " WHERE instrument_id = ?"
            params.append(instrument_id)
        query += " ORDER BY exchange_timestamp ASC"

        cursor = conn.execute(query, params)
        events: list[CanonicalEvent] = []
        for row in cursor.fetchall():
            reasons = json.loads(row[15]) if row[15] else []
            events.append(
                CanonicalEvent(
                    event_id=row[0],
                    instrument_id=row[1],
                    event_type=EventType(row[2]),
                    exchange_timestamp=row[3],
                    receive_timestamp=row[4],
                    processing_timestamp=row[5],
                    source=row[6],
                    sequence_number=row[7],
                    price=row[8],
                    quantity=row[9],
                    bid_price=row[10],
                    bid_size=row[11],
                    ask_price=row[12],
                    ask_size=row[13],
                    quality_status=QualityStatus(row[14]),
                    reasons=reasons,
                    raw_id=row[16] or "",
                )
            )
        return events
    finally:
        conn.close()
