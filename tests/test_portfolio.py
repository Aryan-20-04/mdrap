import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from portfolio import WatchlistManager, PortfolioTracker


def test_watchlist_crud():
    wm = WatchlistManager()

    # Create
    wl = wm.create("Tech", symbols=["AAPL", "MSFT"], description="Tech stocks")
    assert wl.name == "Tech"
    assert wl.symbols == ["AAPL", "MSFT"]
    assert wl.description == "Tech stocks"

    # Get
    assert wm.get("Tech").symbols == ["AAPL", "MSFT"]

    # Add symbols
    wm.add_symbols("Tech", ["GOOG"])
    assert wm.get("Tech").symbols == ["AAPL", "MSFT", "GOOG"]

    # Remove symbols
    wm.remove_symbols("Tech", ["MSFT"])
    assert wm.get("Tech").symbols == ["AAPL", "GOOG"]

    # Delete
    wm.delete("Tech")
    assert wm.get("Tech") is None


def test_watchlist_search():
    wm = WatchlistManager()
    wm.create("Tech", symbols=["AAPL", "MSFT"])
    wm.create("BlueChip", symbols=["AAPL", "JNJ"])

    res = wm.search("AAPL")
    names = [w.name for w in res]
    assert "Tech" in names
    assert "BlueChip" in names

    res = wm.search("MSFT")
    names = [w.name for w in res]
    assert "Tech" in names
    assert "BlueChip" not in names


def test_portfolio_buy_and_hold():
    tracker = PortfolioTracker()
    pos = tracker.add_trade("AAPL", 100, 150.0)
    assert pos.quantity == 100
    assert pos.avg_cost == 150.0

    tracker.update_price("AAPL", 160.0)
    pos = tracker.get_position("AAPL")
    assert pos.unrealized_pnl == 1000.0


def test_portfolio_buy_then_sell():
    tracker = PortfolioTracker()
    tracker.add_trade("AAPL", 100, 150.0)
    tracker.add_trade("AAPL", -50, 160.0)

    pos = tracker.get_position("AAPL")
    assert pos.quantity == 50
    assert pos.realized_pnl == 500.0


def test_portfolio_short_sell():
    tracker = PortfolioTracker()
    # Short 100 at 150
    tracker.add_trade("AAPL", -100, 150.0)

    # Cover 100 at 140
    tracker.add_trade("AAPL", 100, 140.0)

    pos = tracker.get_position("AAPL")
    assert pos.quantity == 0
    assert pos.realized_pnl == 1000.0


def test_portfolio_cash_tracking():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    tracker.add_trade("AAPL", 100, 150.0)
    assert tracker.cash == 85_000.0


def test_portfolio_total_equity():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    tracker.add_trade("AAPL", 100, 150.0)
    tracker.update_price("AAPL", 160.0)

    assert tracker.market_value == 16_000.0
    assert tracker.cash == 85_000.0
    assert tracker.total_equity == 101_000.0


def test_pnl_by_sector():
    tracker = PortfolioTracker()
    tracker.add_trade("AAPL", 100, 150.0, sector="Tech")
    tracker.add_trade("XOM", 100, 100.0, sector="Energy")

    tracker.update_prices({"AAPL": 160.0, "XOM": 110.0})

    pnl = tracker.pnl_by_sector()
    assert pnl["Tech"]["unrealized_pnl"] == 1000.0
    assert pnl["Energy"]["unrealized_pnl"] == 1000.0


def test_pnl_by_strategy():
    tracker = PortfolioTracker()
    tracker.add_trade("AAPL", 100, 150.0, strategy="momentum")
    tracker.add_trade("XOM", 100, 100.0, strategy="mean_reversion")

    tracker.update_prices({"AAPL": 160.0, "XOM": 110.0})

    pnl = tracker.pnl_by_strategy()
    assert pnl["momentum"]["unrealized_pnl"] == 1000.0
    assert pnl["mean_reversion"]["unrealized_pnl"] == 1000.0


def test_portfolio_snapshot():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    tracker.add_trade("AAPL", 100, 150.0)
    tracker.update_price("AAPL", 160.0)

    snap = tracker.snapshot()
    assert snap.total_equity == 101_000.0
    assert snap.cash == 85_000.0
    assert snap.market_value == 16_000.0
    assert snap.unrealized_pnl == 1000.0


def test_benchmark_comparison():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    tracker.add_trade("AAPL", 100, 150.0)
    tracker.update_price("AAPL", 165.0)

    # +1500 / 100_000 = 0.015 (1.5%)
    # Let benchmark_return_pct be 1.0%
    comp = tracker.benchmark_comparison(1.0)

    # alpha should be ~0.5%
    assert abs(comp["portfolio_return_pct"] - 1.5) < 1e-6
    assert abs(comp["benchmark_return_pct"] - 1.0) < 1e-6
    assert abs(comp["alpha"] - 0.5) < 1e-6


def test_portfolio_summary():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    tracker.add_trade("AAPL", 100, 150.0)

    summary = tracker.summary()
    assert "total_equity" in summary
    assert "cash" in summary
    assert "market_value" in summary
    assert "total_realized_pnl" in summary
    assert "total_unrealized_pnl" in summary


def test_portfolio_multi_currency_nav():
    tracker = PortfolioTracker(initial_cash=100_000.0, base_currency="USD")
    # Add RELIANCE (INR) position: 100 shares at 2800 INR = 280,000 INR
    # fx.py converts INR -> USD (approx 280,000 / 83.5 = ~3,353 USD)
    pos = tracker.add_trade("RELIANCE.NS", 100, 2800.0)
    assert pos.currency == "INR"
    assert pos.market_value == 280000.0

    # In USD, market_value should be positive and less than 10,000 USD
    mv_usd = tracker.market_value
    assert 3000.0 < mv_usd < 4000.0
    assert tracker.total_equity > 0


def test_portfolio_short_borrow_cost():
    tracker = PortfolioTracker(initial_cash=100_000.0)
    # Short sell with 500 bps (5%) annual borrow fee
    pos = tracker.add_trade("GME", -100, 20.0, borrow_rate_bps=500.0)
    assert pos.quantity == -100
    assert pos.borrow_rate_bps == 500.0

    # Daily borrow fee on $2,000 short: 2000 * 0.05 / 365 = ~0.274 USD
    daily_cost = tracker.total_daily_borrow_cost()
    assert 0.25 < daily_cost < 0.30

    snap = tracker.snapshot()
    assert snap.borrow_cost == daily_cost


def test_cmd_report_13f_and_rts28(capsys):
    from cli import cmd_report
    from types import SimpleNamespace

    # 1. Test 13F JSON report
    args_13f = SimpleNamespace(
        report_type="13f",
        db=":memory:",
        json=True,
    )
    cmd_report(args_13f)
    captured = capsys.readouterr()
    assert "SEC_FORM_13F" in captured.out

    # 2. Test RTS 28 JSON report
    args_rts28 = SimpleNamespace(
        report_type="rts28",
        symbol="AAPL",
        seed=42,
        json=True,
    )
    cmd_report(args_rts28)
    captured = capsys.readouterr()
    assert "MIFID_II_RTS_28" in captured.out
    assert "top_execution_venues" in captured.out
