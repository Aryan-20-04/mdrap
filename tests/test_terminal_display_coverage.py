"""
Comprehensive tests for src/terminal_display.py.
Exercises sparklines, candlestick charts, layout building, dashboard state,
and panel renderers.
"""

from __future__ import annotations

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import terminal_display
from models import CanonicalEvent, EventType, QualityStatus


def test_sparkline_renderers():
    vals = [10.0, 15.0, 12.0, 18.0, 25.0, 20.0, 30.0]
    spark = terminal_display.render_sparkline(vals, width=10)
    assert spark is not None
    assert len(str(spark)) > 0

    flat = terminal_display.render_sparkline([5.0] * 8, width=8)
    assert flat is not None

    assert terminal_display.render_sparkline([]) == "[dim]--[/dim]"

    ascii_spark = terminal_display._generate_ascii_sparkline([int(v) for v in vals])
    assert len(ascii_spark) > 0


def test_candlestick_chart_rendering():
    candles = [
        {
            "bucket": 1000.0,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "close": 103.0,
            "volume": 5000.0,
        },
        {
            "bucket": 1060.0,
            "open": 103.0,
            "high": 107.0,
            "low": 101.0,
            "close": 106.0,
            "volume": 8000.0,
        },
        {
            "bucket": 1120.0,
            "open": 106.0,
            "high": 108.0,
            "low": 102.0,
            "close": 104.0,
            "volume": 3000.0,
        },
        {
            "bucket": 1180.0,
            "open": 104.0,
            "high": 109.0,
            "low": 103.0,
            "close": 108.0,
            "volume": 12000.0,
        },
    ]
    chart = terminal_display.render_candlestick_chart(
        candles, title="AAPL", width=50, height=12
    )
    assert chart is not None
    assert "AAPL" in str(chart)

    empty_chart = terminal_display.render_candlestick_chart([], title="AAPL")
    assert empty_chart is not None


def test_dashboard_state_and_layout():
    state = terminal_display.DashboardState()
    state.status = "CONNECTED"
    state.events_received = 1500
    state.bbo["AAPL"] = {"bid": 150.0, "ask": 150.05}
    state.vwap["AAPL"] = {"vol": 2000.0, "vol_price": 300000.0}
    state.throughput_history = [100, 200, 300, 400]

    layout = terminal_display.create_layout()
    assert layout is not None

    hdr = terminal_display.render_dashboard_header(state)
    assert hdr is not None

    ftr = terminal_display.render_dashboard_footer(state)
    assert ftr is not None

    bbo_table = terminal_display.render_dashboard_bbo_table(state)
    assert bbo_table is not None

    vwap_table = terminal_display.render_dashboard_vwap_table(state)
    assert vwap_table is not None

    full_dash = terminal_display.build_dashboard(state)
    assert full_dash is not None


def test_poll_keypress():
    key = terminal_display.poll_keypress()
    assert key is None or isinstance(key, str)
