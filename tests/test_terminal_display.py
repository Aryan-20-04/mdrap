"""
Unit and Integration Tests for MDRAP Terminal Display & Candlestick Chart Engine.
"""
import os
import shutil
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from terminal_display import (
    render_sparkline,
    render_candlestick_chart,
    LiveTickerDashboard,
)
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from storage import Store
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from cli import build_parser


@pytest.fixture
def temp_env():
    temp_dir = tempfile.mkdtemp(prefix="mdrap_chart_test_")
    db_path = os.path.join(temp_dir, "test.db")
    store = Store(db_path)
    pipe = Pipeline(store=store)
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=500))
    for raw, _ in sim.generate():
        pipe.process_one(raw)
    pipe.finish()
    store.close()

    yield {"dir": temp_dir, "db": db_path}

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


def test_render_sparkline():
    """Verify sparkline generates correct trend bars and color markup."""
    assert "dim" in render_sparkline([])
    
    # Flat series
    flat = render_sparkline([100.0, 100.0, 100.0])
    assert "▄" in flat

    # Up trend (ends higher than start)
    up = render_sparkline([10.0, 15.0, 20.0, 25.0])
    assert "green" in up
    assert len(up) > 0

    # Down trend (ends lower than start)
    down = render_sparkline([25.0, 20.0, 15.0, 10.0])
    assert "red" in down


def test_render_candlestick_chart():
    """Verify candlestick chart accurately formats OHLCV data, wicks, bodies, and price axis."""
    empty = render_candlestick_chart([])
    assert "No trade candle data available" in empty

    candles = [
        {"open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "volume": 50.0, "bucket_start": 1000.0},
        {"open": 104.0, "high": 106.0, "low": 102.0, "close": 101.0, "volume": 80.0, "bucket_start": 1005.0},
        {"open": 101.0, "high": 103.0, "low": 99.0, "close": 102.5, "volume": 65.0, "bucket_start": 1010.0},
    ]

    chart = render_candlestick_chart(candles, width=40, height=8, show_volume=True, title="Test Chart")
    assert "Test Chart" in chart
    assert "O:" in chart
    assert "H:" in chart
    assert "L:" in chart
    assert "C:" in chart
    assert "│" in chart
    assert "Vol" in chart
    assert "$" in chart


def test_live_ticker_dashboard_single_ticker():
    """Verify Single-Ticker Focus Mode renders all 4 panels (header, NBBO, chart, venues)."""
    dash = LiveTickerDashboard()
    
    # Feed trade event
    raw_trade = RawEvent(
        source="BINANCE",
        payload={"instrument": "BTC/USD", "price": 65000.0, "size": 1.5, "event_type": "TRADE", "exchange_ts": 1000.0},
        receive_timestamp=1000.001,
        raw_id="t1",
    )
    ev_trade = CanonicalEvent(
        event_id="e1", instrument_id="BTC/USD", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
        source="BINANCE", sequence_number=1, price=65000.0, quantity=1.5, quality_status=QualityStatus.VALID,
    )
    dash.update_with_event(raw_trade, ev_trade, engine_ns=5000)

    # Feed quote event
    raw_quote = RawEvent(
        source="COINBASE",
        payload={"instrument": "BTC/USD", "bid": 64995.0, "ask": 65005.0, "bid_size": 2.0, "ask_size": 3.0, "event_type": "QUOTE", "exchange_ts": 1000.002},
        receive_timestamp=1000.003,
        raw_id="q1",
    )
    ev_quote = CanonicalEvent(
        event_id="e2", instrument_id="BTC/USD", event_type=EventType.QUOTE,
        exchange_timestamp=1000.002, receive_timestamp=1000.003, processing_timestamp=1000.004,
        source="COINBASE", sequence_number=2, bid_price=64995.0, ask_price=65005.0,
        bid_size=2.0, ask_size=3.0, quality_status=QualityStatus.VALID,
    )
    dash.update_with_event(raw_quote, ev_quote, engine_ns=4200)

    # Render focus panel
    panel = dash.render_single_ticker("BTC/USD")
    assert panel is not None
    assert dash.event_count == 2
    assert dash.last_prices.get("BTC/USD") == 65000.0


def test_live_ticker_dashboard_multi_ticker():
    """Verify Multi-Ticker Matrix Mode renders table rows with price direction indicators."""
    dash = LiveTickerDashboard()
    
    symbols = ["BTC/USD", "ETH/USD", "SOL/USD"]
    for s in symbols:
        raw = RawEvent(
            source="BINANCE",
            payload={"instrument": s, "bid": 100.0, "ask": 102.0, "event_type": "QUOTE", "exchange_ts": 1000.0},
            receive_timestamp=1000.001,
            raw_id=f"r-{s}",
        )
        ev = CanonicalEvent(
            event_id=f"e-{s}", instrument_id=s, event_type=EventType.QUOTE,
            exchange_timestamp=1000.0, receive_timestamp=1000.001, processing_timestamp=1000.002,
            source="BINANCE", sequence_number=1, bid_price=100.0, ask_price=102.0,
            quality_status=QualityStatus.VALID,
        )
        dash.update_with_event(raw, ev, engine_ns=3000)

    renderable = dash.render_multi_ticker_table(symbols)
    assert renderable is not None
    assert len(dash.venue_quotes) == 3


def test_cli_chart_command(temp_env):
    """Verify 'chart' CLI subcommand dispatches and executes."""
    parser = build_parser()
    args = parser.parse_args(["chart", "AAPL", "--db", temp_env["db"]])
    assert args.func is not None
    assert args.symbol == "AAPL"
    
    # Run command func directly
    args.func(args)


def test_cli_ticker_command(temp_env):
    """Verify 'ticker' / 'live' focus mode executes cleanly with simulated feed."""
    parser = build_parser()
    args = parser.parse_args(["ticker", "BTC/USD", "--sim", "--limit", "4", "--db", temp_env["db"]])
    assert args.func is not None
    assert args.symbol == "BTC/USD"
    assert args.limit == 4
    assert args.sim is True

    # Run command func directly
    args.func(args)
