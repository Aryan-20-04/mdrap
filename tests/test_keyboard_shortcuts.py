import os
import sys
import unittest.mock as mock
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli import (
    KNOWN_SYMBOLS,
    MNEMONIC_MAP,
    QUICK_ACTIONS,
    ALL_CANONICAL_COMMANDS,
    build_parser,
)
from terminal_display import TerminalDisplay, poll_keypress


def test_known_symbols_has_equities_crypto_futures():
    assert "AAPL" in KNOWN_SYMBOLS
    assert "BTC" in KNOWN_SYMBOLS
    assert "ES" in KNOWN_SYMBOLS
    assert KNOWN_SYMBOLS["ES"] == "ES.c.0"
    assert "NQ" in KNOWN_SYMBOLS
    assert "SPY" in KNOWN_SYMBOLS


def test_mnemonic_map_has_wall_st_shortcuts():
    assert MNEMONIC_MAP["c"] == "chart"
    assert MNEMONIC_MAP["d"] == "depth"
    assert MNEMONIC_MAP["v"] == "vwap"
    assert MNEMONIC_MAP["p"] == "polygon"
    assert MNEMONIC_MAP["b"] == "databento"
    assert MNEMONIC_MAP["x"] == "export"
    assert MNEMONIC_MAP["f"] == "feed"


def test_quick_actions_covers_1_to_9():
    for digit in "123456789":
        assert digit in QUICK_ACTIONS
        tokens = QUICK_ACTIONS[digit]
        assert len(tokens) >= 1
    # Check specific actions
    assert QUICK_ACTIONS["1"][0] == "live"
    assert QUICK_ACTIONS["2"][0] == "bbo"
    assert QUICK_ACTIONS["3"][0] == "top"
    assert QUICK_ACTIONS["4"][0] == "chart"
    assert QUICK_ACTIONS["5"][0] == "depth"
    assert QUICK_ACTIONS["6"][0] == "vwap"
    assert "polygon" in QUICK_ACTIONS["7"]
    assert "databento" in QUICK_ACTIONS["8"]
    assert QUICK_ACTIONS["9"][0] == "status"


def test_poll_keypress_no_blocking():
    # Calling poll_keypress should never block and return None when no key is pressed
    res = poll_keypress()
    assert res is None


def test_single_ticker_render_hotkey_states():
    td = TerminalDisplay()
    # Test rendering single ticker with paused=True, show_chart=False, show_depth=True
    renderable = td.render_single_ticker(
        "AAPL", paused=True, show_chart=False, show_depth=True
    )
    assert renderable is not None


def test_multi_ticker_render_hotkey_states():
    td = TerminalDisplay()
    # Test rendering multi-ticker with paused=True
    renderable = td.render_multi_ticker_table(["AAPL", "MSFT", "BTC/USD"], paused=True)
    assert renderable is not None


def test_cli_ticker_first_and_shortcuts():
    from cli import main

    test_cases = [
        (["cli.py", "AAPL"], ["cli.py", "bbo", "AAPL"]),
        (["cli.py", "AAPL", "c"], ["cli.py", "chart", "AAPL"]),
        (["cli.py", "c", "AAPL"], ["cli.py", "chart", "AAPL"]),
        (["cli.py", "d", "BTC"], ["cli.py", "depth", "BTC/USD"]),
        (["cli.py", "v", "AAPL"], ["cli.py", "vwap", "AAPL"]),
        (
            ["cli.py", "p", "AAPL"],
            ["cli.py", "live", "AAPL", "--feed", "polygon", "--mock-feed"],
        ),
        (
            ["cli.py", "b", "ES"],
            ["cli.py", "live", "ES.c.0", "--feed", "databento", "--mock-feed"],
        ),
        (["cli.py", "x", "AAPL"], ["cli.py", "export", "AAPL"]),
        (["cli.py", "1"], ["cli.py", "live", "BTC/USD"]),
        (["cli.py", "4"], ["cli.py", "chart", "AAPL"]),
    ]

    for argv_in, argv_expected in test_cases:
        with mock.patch("sys.argv", list(argv_in)):
            with mock.patch("argparse.ArgumentParser.parse_args") as mock_parse:
                mock_args = mock.MagicMock()
                mock_args.func = mock.MagicMock()
                mock_parse.return_value = mock_args
                main()
                assert sys.argv == argv_expected, (
                    f"Failed for {argv_in}: got {sys.argv} expected {argv_expected}"
                )
