"""
Comprehensive unit tests for src/navigator.py to expand code coverage.
Tests DataGrid manipulation, sorting, filtering, viewport scrolling,
ModalNavigator state machine transitions, key handling, and drilldown actions.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from navigator import (
    ConfirmationTicket,
    DataGrid,
    GridColumn,
    Key,
    MDRAPNavigator,
    NavigatorMode,
)


def _sample_grid():
    cols = [
        GridColumn(name="Symbol", key="symbol"),
        GridColumn(name="Price", key="price", justify="right"),
        GridColumn(name="Volume", key="volume", justify="right"),
    ]
    rows = [
        {"id": "1", "symbol": "AAPL", "price": 150.25, "volume": 1000},
        {"id": "2", "symbol": "MSFT", "price": 400.50, "volume": 500},
        {"id": "3", "symbol": "NVDA", "price": 120.75, "volume": 2500},
        {"id": "4", "symbol": "GOOGL", "price": 180.00, "volume": 800},
        {"id": "5", "symbol": "AMZN", "price": 175.50, "volume": 1200},
    ]
    return DataGrid("Test Grid", cols, rows, row_id_key="id")


def test_datagrid_navigation_and_bounds():
    grid = _sample_grid()
    grid.page_size = 3

    assert grid.selected_idx == 0
    assert grid.get_selected_row()["symbol"] == "AAPL"
    assert grid.get_selected_id() == "1"

    # Move down
    grid.move_selection(2)
    assert grid.selected_idx == 2
    assert grid.get_selected_row()["symbol"] == "NVDA"

    # Move down past end
    grid.move_selection(10)
    assert grid.selected_idx == 4
    assert grid.get_selected_row()["symbol"] == "AMZN"

    # Move up
    grid.move_selection(-2)
    assert grid.selected_idx == 2

    # Move up past start
    grid.move_selection(-10)
    assert grid.selected_idx == 0

    # Page up and down
    grid.page_down()
    assert grid.selected_idx == 2
    grid.page_up()
    assert grid.selected_idx == 0

    # Jump top and bottom
    grid.jump_bottom()
    assert grid.selected_idx == 4
    grid.jump_top()
    assert grid.selected_idx == 0


def test_datagrid_filtering_and_restore():
    grid = _sample_grid()

    grid.set_filter("NV")
    assert len(grid.filtered_rows) == 1
    assert grid.filtered_rows[0]["symbol"] == "NVDA"
    assert grid.selected_idx == 0

    grid.clear_filter()
    assert len(grid.filtered_rows) == 5

    # Update rows preserving selection
    grid.move_selection(1)  # MSFT
    new_rows = [
        {"id": "2", "symbol": "MSFT", "price": 405.00, "volume": 550},
        {"id": "1", "symbol": "AAPL", "price": 151.00, "volume": 1050},
    ]
    grid.update_rows(new_rows)
    assert grid.get_selected_id() == "2"


def test_datagrid_sorting():
    grid = _sample_grid()

    # Sort ascending by symbol
    grid.toggle_sort("symbol")
    assert grid.sort_col == "symbol"
    assert not grid.sort_desc
    assert grid.filtered_rows[0]["symbol"] == "AAPL"

    # Sort descending by symbol
    grid.toggle_sort("symbol")
    assert grid.sort_desc
    assert grid.filtered_rows[0]["symbol"] == "NVDA"

    # Numeric sorting by price
    grid.toggle_sort("price")
    assert grid.sort_col == "price"
    assert not grid.sort_desc
    assert grid.filtered_rows[0]["symbol"] == "NVDA"  # 120.75 is lowest

    grid.toggle_sort("price")
    assert grid.sort_desc
    assert grid.filtered_rows[0]["symbol"] == "MSFT"  # 400.50 is highest


def test_datagrid_empty_rows():
    grid = DataGrid("Empty", [], [])
    grid.move_selection(1)
    assert grid.selected_idx == 0
    assert grid.get_selected_row() is None
    assert grid.get_selected_id() is None
    grid.jump_bottom()
    assert grid.selected_idx == 0


def test_modal_navigator_modes_and_keys():
    nav = MDRAPNavigator()
    grid = _sample_grid()
    nav.tabs = [("1", "Equities", grid)]
    nav.active_tab_idx = 0

    # Normal mode motions
    nav.handle_key("j")
    assert grid.selected_idx == 1
    nav.handle_key("k")
    assert grid.selected_idx == 0

    nav.handle_key(Key.DOWN)
    assert grid.selected_idx == 1
    nav.handle_key(Key.UP)
    assert grid.selected_idx == 0

    nav.handle_key("G")
    assert grid.selected_idx == 4
    nav.handle_key("g")
    assert grid.selected_idx == 0

    nav.handle_key(Key.PAGE_DOWN)
    assert grid.selected_idx > 0
    nav.handle_key(Key.PAGE_UP)
    assert grid.selected_idx == 0

    # Filter mode transition
    nav.handle_key("/")
    assert nav.mode == NavigatorMode.FILTER

    nav.handle_key("N")
    nav.handle_key("V")
    assert grid.filter_query == "NV"

    nav.handle_key(Key.BACKSPACE)
    assert grid.filter_query == "N"

    nav.handle_key(Key.ENTER)
    assert nav.mode == NavigatorMode.NORMAL

    nav.handle_key("/")
    nav.handle_key(Key.ESC)
    assert nav.mode == NavigatorMode.NORMAL
    assert grid.filter_query == ""


def test_modal_confirmation_ticket_execution():
    nav = MDRAPNavigator()
    grid = _sample_grid()
    nav.tabs = [("1", "Equities", grid)]
    nav.active_tab_idx = 0

    executed = []

    def on_confirm():
        executed.append(True)

    ticket = ConfirmationTicket(
        title="BUY ORDER",
        message="Confirm buy 100 AAPL",
        details={"Symbol": "AAPL", "Qty": "100"},
        callback=on_confirm,
    )

    nav.active_modal = ticket
    nav.mode = NavigatorMode.MODAL

    # Confirm with 'y'
    nav.handle_key("y")
    assert len(executed) == 1
    assert nav.mode == NavigatorMode.NORMAL
    assert nav.active_modal is None

    # Cancel with 'n'
    nav.active_modal = ticket
    nav.mode = NavigatorMode.MODAL
    nav.handle_key("n")
    assert len(executed) == 1  # Not executed again
    assert nav.mode == NavigatorMode.NORMAL

    # Confirm with ENTER
    nav.active_modal = ticket
    nav.mode = NavigatorMode.MODAL
    nav.handle_key(Key.ENTER)
    assert len(executed) == 2

    # Cancel with ESC
    nav.active_modal = ticket
    nav.mode = NavigatorMode.MODAL
    nav.handle_key(Key.ESC)
    assert len(executed) == 2


def test_modal_navigator_actions():
    nav = MDRAPNavigator()
    grid = _sample_grid()
    nav.tabs = [("1", "Equities", grid), ("2", "Crypto", _sample_grid())]
    nav.active_tab_idx = 0

    # Tab cycling
    nav.handle_key("l")
    assert nav.active_tab_idx == 1
    nav.handle_key("h")
    assert nav.active_tab_idx == 0
    nav.handle_key("2")
    assert nav.active_tab_idx == 1
    nav.handle_key("1")
    assert nav.active_tab_idx == 0

    # Order ticket prompts
    nav.handle_key("b")
    assert nav.mode == NavigatorMode.MODAL
    assert nav.active_modal is not None
    assert "BUY" in nav.active_modal.title
    nav.handle_key(Key.ESC)

    nav.handle_key("s")
    assert nav.mode == NavigatorMode.MODAL
    assert nav.active_modal is not None
    assert "SELL" in nav.active_modal.title
    nav.handle_key(Key.ESC)

    # Status setting and cheatsheet
    nav.set_status("Custom Status", "bold blue")
    assert "Custom Status" in nav.status_message

    nav.handle_key("?")
    assert "Vim motions" in nav.status_message

    # Row drilldowns & visual actions
    nav.handle_key("c")
    assert "candlestick chart" in nav.status_message

    nav.handle_key("d")
    assert "market depth" in nav.status_message

    nav.handle_key("v")
    assert "VWAP curve" in nav.status_message

    nav.handle_key("x")
    assert "Exporting active" in nav.status_message

    nav.handle_key(Key.ENTER)
    nav.handle_key("o")

    # Quit key
    nav._running = True
    nav.handle_key("q")
    assert not nav._running


def test_modal_navigator_render():
    nav = MDRAPNavigator()
    grid = _sample_grid()
    nav.tabs = [("1", "Equities", grid)]
    nav.active_tab_idx = 0

    # Render normal mode
    nav.render()

    # Render filter mode
    nav.mode = NavigatorMode.FILTER
    nav.input_buffer = "TEST"
    nav.render()

    # Render modal mode
    nav.mode = NavigatorMode.MODAL
    nav.active_modal = ConfirmationTicket("TEST", "Message", {"Key": "Val"})
    nav.render()


@pytest.mark.skipif(sys.platform != "win32", reason="msvcrt is Windows-only")
def test_key_reader_all_keys():
    from navigator import KeyReader, Key

    kr = KeyReader()

    test_cases = [
        ([b"\r"], Key.ENTER),
        ([b"\x1b"], Key.ESC),
        ([b"\t"], Key.TAB),
        ([b"\x08"], Key.BACKSPACE),
        ([b"j"], "j"),
        ([b"\xe0", b"H"], Key.UP),
        ([b"\xe0", b"P"], Key.DOWN),
        ([b"\xe0", b"K"], Key.LEFT),
        ([b"\xe0", b"M"], Key.RIGHT),
        ([b"\xe0", b"I"], Key.PAGE_UP),
        ([b"\xe0", b"Q"], Key.PAGE_DOWN),
        ([b"\xe0", b"G"], Key.HOME),
        ([b"\xe0", b"O"], Key.END),
        ([b"\xe0", b"S"], Key.DELETE),
    ]
    for key_bytes, expected in test_cases:
        it = iter(key_bytes)
        with patch(
            "msvcrt.kbhit",
            side_effect=[True, True, False] if len(key_bytes) > 1 else [True, False],
        ):
            with patch("msvcrt.getch", side_effect=lambda: next(it)):
                assert kr.read_key(timeout_s=0.05) == expected
