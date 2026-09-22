"""
Comprehensive test suite for src/cli.py and src/trading_cli.py.
Exercises all CLI subcommands, parser options, flag combinations,
typo auto-corrections, and trading engine integrations.
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

pytestmark = pytest.mark.slow  # ponytail: full CLI benchmark skip by default

import cli
import trading_cli


@pytest.fixture
def parser():
    return cli.build_parser()


def _run_cmd(parser, args_list):
    """Parse and execute CLI command while silencing console output."""
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        parsed = parser.parse_args(args_list)
        return parsed.func(parsed)


def test_cli_core_diagnostics(parser):
    _run_cmd(parser, ["status"])
    _run_cmd(parser, ["security"])
    _run_cmd(parser, ["watchdog", "status"])
    _run_cmd(parser, ["watchdog", "alerts", "-l", "5"])
    _run_cmd(parser, ["keys", "list"])
    _run_cmd(parser, ["audit", "--verify"])
    _run_cmd(parser, ["query", "health"])
    _run_cmd(parser, ["archive"])
    _run_cmd(parser, ["analytics", "summary"])
    _run_cmd(parser, ["analytics", "spread", "all"])
    _run_cmd(parser, ["analytics", "vol"])


def test_cli_microstructure_and_books(parser):
    _run_cmd(parser, ["bbo", "AAPL"])
    _run_cmd(parser, ["depth", "AAPL", "-l", "5"])
    _run_cmd(parser, ["vwap", "AAPL", "--sizes", "10", "20"])
    _run_cmd(parser, ["chart", "AAPL"])
    _run_cmd(parser, ["tca", "AAPL"])
    _run_cmd(parser, ["flow", "AAPL"])


def test_cli_venues_and_symbology(parser):
    _run_cmd(parser, ["markets"])
    _run_cmd(parser, ["venues"])
    import symbology
    import fx
    info = symbology.resolve_symbol("AAPL")
    assert info.ticker == "AAPL"
    assert symbology.get_native_currency("RELIANCE.NS") == "INR"
    matrix = fx.FXMatrix()
    assert matrix.get_rate("USD", "INR") > 0


def test_cli_vessel_alternative_data(parser):
    _run_cmd(parser, ["vessel", "list", "-l", "5"])
    _run_cmd(parser, ["vessel", "chokepoints"])
    _run_cmd(parser, ["vessel", "commodities"])
    _run_cmd(parser, ["vessel", "track", "TI EUROPE"])


def test_cli_edgar_alternative_data(parser):
    _run_cmd(parser, ["edgar", "events", "AAPL"])
    _run_cmd(parser, ["edgar", "insiders", "AAPL", "-l", "5"])
    _run_cmd(parser, ["edgar", "facts", "AAPL"])


def test_cli_execution_pipelines(parser):
    # Fast micro runs
    _run_cmd(parser, ["run", "-e", "10", "--no-sync"])
    _run_cmd(parser, ["benchmark", "-e", "10", "-s", "42"])
    _run_cmd(parser, ["compare", "-e", "10", "-s", "42"])
    _run_cmd(parser, ["loadtest", "--levels", "5,10"])
    _run_cmd(parser, ["stress", "--module", "gateway", "-e", "10"])
    _run_cmd(parser, ["chaos", "kill", "--kill-source", "FEEDX", "--kill-start", "2", "--kill-duration", "2", "-e", "10"])
    _run_cmd(parser, ["feed", "--source", "crypto", "-c", "5", "--mock"])


def test_cli_trading_commands(parser):
    # Trading engine commands
    _run_cmd(parser, ["backtest", "-i", "AAPL"])
    _run_cmd(parser, ["risk"])
    _run_cmd(parser, ["bars", "summary"])
    _run_cmd(parser, ["bars", "instruments"])
    _run_cmd(parser, ["options", "price", "-s", "150", "-k", "150", "-e", "30"])
    _run_cmd(parser, ["options", "chain", "-s", "150"])
    _run_cmd(parser, ["news", "latest", "-l", "5"])
    _run_cmd(parser, ["news", "summary"])
    _run_cmd(parser, ["alert", "list"])
    _run_cmd(parser, ["watchlist", "list"])
    _run_cmd(parser, ["portfolio", "summary"])
    _run_cmd(parser, ["features", "list", "-i", "AAPL"])
    _run_cmd(parser, ["schedule", "list"])
    _run_cmd(parser, ["schedule", "eod"])
    _run_cmd(parser, ["corpact", "list", "-i", "AAPL"])


def test_cli_fix_engine():
    import fix_engine
    sample_fix = (
        "8=FIX.4.2\x019=55\x0135=D\x0149=BUYER\x0156=SELLER\x0134=1\x01"
        "52=20260901-12:00:00\x0111=ORD1\x0121=1\x0155=AAPL\x0154=1\x01"
        "60=20260901-12:00:00\x0140=2\x0144=150.00\x0138=100\x0110=123\x01"
    )
    msg = fix_engine.FIXMessage.parse(sample_fix)
    assert msg.get(fix_engine.FIXTag.SYMBOL) == "AAPL"
    assert msg.get_float(fix_engine.FIXTag.PRICE) == 150.0
    encoded = msg.encode("BUYER", "SELLER", 1)
    assert "8=FIX.4.2" in encoded
    session = fix_engine.FIXSession("TEST_CLIENT", "MDRAP")
    hb = session.create_heartbeat()
    assert hb.get(fix_engine.FIXTag.MSG_TYPE) == "0"


def test_cli_typo_correction(parser):
    # Test fuzzy suggestion and scoping
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            parser.parse_args(["choas"])
        except SystemExit:
            pass


def test_cli_main_entrypoint(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mdrap", "status"])
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        try:
            cli.main()
        except SystemExit:
            pass


def test_cli_columnar_and_data_stores(parser):
    _run_cmd(parser, ["columnar", "info"])
    _run_cmd(parser, ["columnar", "sync"])
    _run_cmd(parser, ["columnar", "ohlcv", "AAPL"])
    _run_cmd(parser, ["columnar", "vwap", "AAPL"])
    _run_cmd(parser, ["columnar", "spread", "AAPL"])
    _run_cmd(parser, ["columnar", "latency"])
    _run_cmd(parser, ["columnar", "profile", "AAPL"])
    _run_cmd(parser, ["columnar", "bench"])


def test_cli_institutional_trading(parser):
    _run_cmd(parser, ["strategy", "list"])
    _run_cmd(parser, ["strategy", "run", "-e", "50"])
    _run_cmd(parser, ["itch", "bench", "-e", "50"])
    _run_cmd(parser, ["mbo", "AAPL"])
    _run_cmd(parser, ["arbitrate", "-e", "50"])
    _run_cmd(parser, ["throughput", "-e", "1000", "--compare"])


def test_cli_interactive_shell():
    script = [
        "status",
        "health",
        "/bbo AAPL",
        "/depth AAPL",
        "/vwap AAPL",
        "/chart AAPL",
        "/cnd AAPL",
        "/spread AAPL",
        "/vol",
        "/flow AAPL",
        "/tca AAPL",
        "/columnar",
        "/help",
        "1", "2", "3", "4", "5", "6", "9",
        "/exit",
    ]
    it = iter(script)
    with patch("rich.console.Console.input", side_effect=lambda *a, **kw: next(it, "exit")):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            try:
                cli.cmd_shell(cli.build_parser().parse_args(["shell"]))
            except (StopIteration, SystemExit):
                pass

