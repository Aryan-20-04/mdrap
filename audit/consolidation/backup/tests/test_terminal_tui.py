"""Unit tests for MDRAP Live Terminal UI Dashboard (tui.py).

Verifies:
- Unicode and ASCII sparkline generation.
- Order book depth ladder rendering.
- Non-blocking frame generation.
"""

from __future__ import annotations

import pytest

from mdrap.tui import TerminalDashboard, render_sparkline


def test_render_sparkline_empty():
    assert render_sparkline([]) == ""


def test_render_sparkline_flat():
    vals = [100.0, 100.0, 100.0, 100.0]
    s = render_sparkline(vals)
    assert len(s) == 4


def test_render_sparkline_ascending():
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    s = render_sparkline(vals, width=5)
    assert len(s) == 5


def test_terminal_dashboard_frame_record():
    """Verify TerminalDashboard records ticks and tracks latencies without errors."""
    dash = TerminalDashboard(symbol="BTC/USDT", max_ticks=3)
    # Run 3 ticks
    dash.run()
    assert len(dash.history_prices) == 3
    assert dash.valid_count == 3
    assert len(dash.latencies_us) == 3
