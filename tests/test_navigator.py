"""
Tests for MDRAP Keyboard-First Modal Navigator Engine (src/navigator.py).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from navigator import (
    ConfirmationTicket,
    DataGrid,
    GridColumn,
    Key,
    KeyReader,
    MDRAPNavigator,
    NavigatorMode,
)


class TestDataGrid(unittest.TestCase):
    def setUp(self):
        self.cols = [
            GridColumn("Symbol", "symbol"),
            GridColumn("Price", "price", justify="right"),
            GridColumn("Name", "name"),
        ]
        self.rows = [
            {"symbol": "AAPL", "price": 185.0, "name": "Apple Inc"},
            {"symbol": "MSFT", "price": 442.0, "name": "Microsoft Corp"},
            {"symbol": "NVDA", "price": 118.0, "name": "Nvidia Corp"},
            {"symbol": "BTC/USD", "price": 68400.0, "name": "Bitcoin"},
            {"symbol": "ETH/USD", "price": 3500.0, "name": "Ethereum"},
        ]
        self.grid = DataGrid("Test Assets", self.cols, self.rows, row_id_key="symbol")
        self.grid.page_size = 3

    def test_initial_state(self):
        self.assertEqual(len(self.grid.all_rows), 5)
        self.assertEqual(len(self.grid.filtered_rows), 5)
        self.assertEqual(self.grid.selected_idx, 0)
        self.assertEqual(self.grid.scroll_offset, 0)
        selected = self.grid.get_selected_row()
        self.assertIsNotNone(selected)
        self.assertEqual(selected["symbol"], "AAPL")

    def test_navigation_within_bounds(self):
        self.grid.move_selection(1)
        self.assertEqual(self.grid.selected_idx, 1)
        self.assertEqual(self.grid.get_selected_id(), "MSFT")

        self.grid.move_selection(3)  # idx 4
        self.assertEqual(self.grid.selected_idx, 4)
        self.assertEqual(self.grid.get_selected_id(), "ETH/USD")

        # Clamping at bottom
        self.grid.move_selection(10)
        self.assertEqual(self.grid.selected_idx, 4)

        # Clamping at top
        self.grid.move_selection(-10)
        self.assertEqual(self.grid.selected_idx, 0)

    def test_viewport_scrolling(self):
        # Page size is 3
        self.grid.move_selection(3)  # idx 3 ("BTC/USD")
        self.assertEqual(self.grid.selected_idx, 3)
        self.assertEqual(self.grid.scroll_offset, 1)  # scrolled down by 1

        self.grid.page_down()
        self.assertEqual(self.grid.selected_idx, 4)

        self.grid.jump_top()
        self.assertEqual(self.grid.selected_idx, 0)
        self.assertEqual(self.grid.scroll_offset, 0)

        self.grid.jump_bottom()
        self.assertEqual(self.grid.selected_idx, 4)
        self.assertEqual(self.grid.scroll_offset, 2)

    def test_filter_and_clear(self):
        # Filter by "corp"
        self.grid.set_filter("corp")
        self.assertEqual(len(self.grid.filtered_rows), 2)  # MSFT, NVDA
        self.assertEqual(self.grid.filtered_rows[0]["symbol"], "MSFT")
        self.assertEqual(self.grid.filtered_rows[1]["symbol"], "NVDA")

        # Filter by price or symbol substring
        self.grid.set_filter("btc")
        self.assertEqual(len(self.grid.filtered_rows), 1)
        self.assertEqual(self.grid.filtered_rows[0]["symbol"], "BTC/USD")

        # Clear filter
        self.grid.clear_filter()
        self.assertEqual(len(self.grid.filtered_rows), 5)

    def test_column_sorting(self):
        # Sort by price ascending
        self.grid.toggle_sort("price")
        self.assertEqual(self.grid.sort_col, "price")
        self.assertFalse(self.grid.sort_desc)
        self.assertEqual(self.grid.filtered_rows[0]["symbol"], "NVDA")  # 118.0 lowest

        # Sort by price descending
        self.grid.toggle_sort("price")
        self.assertTrue(self.grid.sort_desc)
        self.assertEqual(self.grid.filtered_rows[0]["symbol"], "BTC/USD")  # 68400.0 highest

    def test_update_rows_preserves_selection(self):
        self.grid.move_selection(2)  # Selected NVDA
        self.assertEqual(self.grid.get_selected_id(), "NVDA")

        new_rows = [
            {"symbol": "TSLA", "price": 220.0, "name": "Tesla Inc"},
            {"symbol": "NVDA", "price": 122.0, "name": "Nvidia Corp (Updated)"},
            {"symbol": "AAPL", "price": 186.0, "name": "Apple Inc"},
        ]
        self.grid.update_rows(new_rows)
        # Should re-locate NVDA at idx 1
        self.assertEqual(self.grid.get_selected_id(), "NVDA")
        self.assertEqual(self.grid.selected_idx, 1)


class TestNavigatorDesk(unittest.TestCase):
    def setUp(self):
        self.console = MagicMock()
        self.nav = MDRAPNavigator(console=self.console)

    def test_default_tabs(self):
        self.assertGreaterEqual(len(self.nav.tabs), 4)
        tab_names = [t[1] for t in self.nav.tabs]
        self.assertIn("Markets", tab_names)
        self.assertIn("Fleet", tab_names)
        self.assertIn("Depth", tab_names)
        self.assertIn("EDGAR", tab_names)
        self.assertIn("Portfolio", tab_names)

    def test_tab_switching_via_hotkeys(self):
        self.assertEqual(self.nav.active_tab_idx, 0)
        self.nav.handle_key("2")  # Switch to Fleet
        self.assertEqual(self.nav.active_tab_idx, 1)
        self.assertEqual(self.nav.tabs[1][1], "Fleet")

        self.nav.handle_key("l")  # Next tab
        self.assertEqual(self.nav.active_tab_idx, 2)

        self.nav.handle_key("h")  # Prev tab
        self.assertEqual(self.nav.active_tab_idx, 1)

    def test_filter_mode_entry_and_typing(self):
        self.assertEqual(self.nav.mode, NavigatorMode.NORMAL)
        self.nav.handle_key("/")
        self.assertEqual(self.nav.mode, NavigatorMode.FILTER)

        # Type "FRONT"
        for ch in "FRONT":
            self.nav.handle_key(ch)
        self.assertEqual(self.nav.input_buffer, "FRONT")
        self.assertEqual(self.nav.active_grid.filter_query, "FRONT")

        # Backspace
        self.nav.handle_key(Key.BACKSPACE)
        self.assertEqual(self.nav.input_buffer, "FRON")

        # Lock with Enter
        self.nav.handle_key(Key.ENTER)
        self.assertEqual(self.nav.mode, NavigatorMode.NORMAL)

        # Clear with ESC in filter mode
        self.nav.handle_key("/")
        self.nav.handle_key(Key.ESC)
        self.assertEqual(self.nav.mode, NavigatorMode.NORMAL)
        self.assertEqual(self.nav.active_grid.filter_query, "")

    def test_modal_confirmation_safety_gate(self):
        # Trigger order ticket (b)
        callback_mock = MagicMock()
        ticket = ConfirmationTicket(
            title="TEST ORDER",
            message="Test buy order?",
            details={"Symbol": "AAPL"},
            action_type="TRADE",
            callback=callback_mock,
        )
        self.nav.active_modal = ticket
        self.nav.mode = NavigatorMode.MODAL

        # In modal mode, normal navigation keys must NOT execute navigation or callbacks
        self.nav.handle_key("j")
        callback_mock.assert_not_called()
        self.assertEqual(self.nav.mode, NavigatorMode.MODAL)

        # Cancel modal via ESC
        self.nav.handle_key(Key.ESC)
        callback_mock.assert_not_called()
        self.assertEqual(self.nav.mode, NavigatorMode.NORMAL)
        self.assertIsNone(self.nav.active_modal)

        # Armed modal confirmed via Enter
        self.nav.active_modal = ticket
        self.nav.mode = NavigatorMode.MODAL
        self.nav.handle_key(Key.ENTER)
        callback_mock.assert_called_once()
        self.assertEqual(self.nav.mode, NavigatorMode.NORMAL)
        self.assertIsNone(self.nav.active_modal)

    @patch("webbrowser.open")
    def test_open_url_action(self, mock_webopen):
        self.nav.handle_key("4")  # Switch to EDGAR tab
        self.nav.handle_key("o")  # Open URL on highlighted row
        mock_webopen.assert_called_once()


class TestNavigatorCLIIntegration(unittest.TestCase):
    def test_parser_desk_command_and_aliases(self):
        from cli import build_parser, cmd_desk
        parser = build_parser()

        for alias in ["desk", "nav", "navigator", "tui"]:
            args = parser.parse_args([alias])
            self.assertEqual(args.func, cmd_desk)

    def test_quick_action_0(self):
        from cli import QUICK_ACTIONS
        self.assertIn("0", QUICK_ACTIONS)
        self.assertEqual(QUICK_ACTIONS["0"], ["desk"])

    @patch("navigator.MDRAPNavigator.run")
    def test_cmd_desk_execution(self, mock_run):
        from cli import cmd_desk
        cmd_desk(MagicMock())
        mock_run.assert_called_once()


class TestNavigatorRenderingStability(unittest.TestCase):
    def test_all_tabs_render_without_jitter_or_overflow(self):
        from rich.console import Console
        c = Console(width=100, height=24)
        nav = MDRAPNavigator(console=c)

        for i, tab in enumerate(nav.tabs):
            nav.active_tab_idx = i
            # Rendering should succeed without exception
            nav.render()
            # Column definitions must have no_wrap set to prevent vertical row expansion
            for col in nav.active_grid.columns:
                self.assertIsNotNone(col.width)


class TestLiveResilience(unittest.TestCase):
    def setUp(self):
        from live import LiveConnector
        self.conn = LiveConnector(timeout=1.0)

    def test_fetch_equity_events_fallback_on_unlisted_symbol(self):
        # TMPV does not exist on Yahoo Finance, so _get_json returns None
        with patch.object(self.conn, "_get_json", return_value=None):
            # When fallback_sim=False, returns empty list safely
            self.assertEqual(self.conn.fetch_equity_events("TMPV", fallback_sim=False), [])
            # When fallback_sim=True (streaming mode), synthesizes ticks to prevent freeze
            events = self.conn.fetch_equity_events("TMPV", fallback_sim=True)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0].source, "EQUITIES (SIM)")
            self.assertEqual(events[1].source, "EQUITIES (SIM)")
            self.assertTrue(events[0].payload.get("is_simulated"))
            self.assertGreater(events[0].payload.get("price", 0), 0)

    def test_stream_ticks_completes_with_unlisted_symbol(self):
        # Stream 4 ticks of TMPV without blocking or hanging when fallback_sim=True
        with patch.object(self.conn, "_get_json", return_value=None):
            ticks = list(self.conn.stream_ticks(["TMPV"], limit=4, poll_interval_s=0.01, fallback_sim=True))
            self.assertEqual(len(ticks), 4)
            for t in ticks:
                self.assertIn("SIM", t.source)

            # Enforces strict zero fake data policy when fallback_sim=False
            strict_ticks = list(self.conn.stream_ticks(["TMPV"], limit=4, poll_interval_s=0.01, fallback_sim=False, max_empty_polls=2))
            self.assertEqual(len(strict_ticks), 0)

    def test_stream_ticks_crypto_fallback_on_unknown_pair(self):
        # When all crypto venues return None, synthesize ticks when fallback_sim=True
        with patch.object(self.conn, "fetch_quote", return_value=None):
            ticks = list(self.conn.stream_ticks(["UNKNOWN/USD"], limit=2, poll_interval_s=0.01, fallback_sim=True))
            self.assertEqual(len(ticks), 2)
            self.assertIn("SIM", ticks[0].source)

            # Strict policy: no fake data if fallback_sim=False
            strict_ticks = list(self.conn.stream_ticks(["UNKNOWN/USD"], limit=2, poll_interval_s=0.01, fallback_sim=False, max_empty_polls=2))
            self.assertEqual(len(strict_ticks), 0)


if __name__ == "__main__":
    unittest.main()

