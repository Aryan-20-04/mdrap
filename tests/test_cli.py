import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest.mock as mock

from cli import build_parser, cmd_status, cmd_query, cmd_analytics, cmd_watchdog, cmd_bbo, cmd_live
from storage import Store
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from analytics import MarketAnalytics
from bbo import BBOEngine


@pytest.fixture
def populated_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(path)
    analytics = MarketAnalytics()
    bbo = BBOEngine()
    pipeline = Pipeline(store, analytics=analytics, bbo=bbo)
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=1000))
    for raw, _label in sim.generate():
        pipeline.process_one(raw)
    pipeline.finish()
    store.write_ohlcv_batch(analytics.ohlcv.candles())
    store.write_spread_batch(analytics.spreads.summary())
    store.write_volatility_batch(analytics.volatility.summary())
    store.write_bbo_batch(list(bbo.all_bbos().values()))
    store.commit()
    store.close()
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def test_build_parser_has_all_slash_and_short_aliases():
    parser = build_parser()
    # Check that short aliases parse properly
    args_s = parser.parse_args(["s"])
    assert args_s.command == "status" or getattr(args_s, "func", None) is not None

    args_r = parser.parse_args(["r", "-e", "1000"])
    assert args_r.num_events == 1000

    args_a = parser.parse_args(["a", "ohlcv", "AAPL"])
    assert args_a.action == "ohlcv"
    assert args_a.target == "AAPL"

    args_w = parser.parse_args(["w", "status"])
    assert args_w.action == "status"

    args_q = parser.parse_args(["q", "health"])
    assert args_q.action == "health"

    args_t = parser.parse_args(["t", "-s", "123"])
    assert args_t.seed == 123


def test_cmd_status_runs_successfully(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["status", "--db", populated_db])
    cmd_status(args)
    captured = capsys.readouterr()
    assert "MDRAP Platform Status Overview" in captured.out or "Canonical Events" in captured.out


def test_cmd_analytics_positional_dispatch(populated_db, capsys):
    parser = build_parser()
    
    # 1. OHLCV
    args = parser.parse_args(["analytics", "ohlcv", "AAPL", "--db", populated_db])
    cmd_analytics(args)
    captured = capsys.readouterr()
    assert "OHLCV Candles: AAPL" in captured.out or "Open" in captured.out

    # 2. Spread
    args = parser.parse_args(["analytics", "spread", "all", "--db", populated_db])
    cmd_analytics(args)
    captured = capsys.readouterr()
    assert "Bid-Ask Spread Analysis" in captured.out or "Mean Spread" in captured.out

    # 3. Volatility
    args = parser.parse_args(["analytics", "vol", "--db", populated_db])
    cmd_analytics(args)
    captured = capsys.readouterr()
    assert "Realized Volatility by Instrument" in captured.out or "Std Dev" in captured.out


def test_cmd_query_positional_dispatch(populated_db, capsys):
    parser = build_parser()
    
    # Health
    args = parser.parse_args(["query", "health", "--db", populated_db])
    cmd_query(args)
    captured = capsys.readouterr()
    assert "score" in captured.out or "FEED" in captured.out

    # Latest
    args = parser.parse_args(["query", "latest", "AAPL", "--db", populated_db])
    cmd_query(args)
    captured = capsys.readouterr()
    assert "AAPL" in captured.out


def test_cmd_watchdog_dispatch(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["watchdog", "status", "--db", populated_db])
    cmd_watchdog(args)
    captured = capsys.readouterr()
    assert "Source Health & Live Watchdog Status" in captured.out or "HEALTHY" in captured.out


def test_cmd_bbo_dispatch(populated_db, capsys):
    parser = build_parser()
    
    # 1. Single symbol query
    args = parser.parse_args(["bbo", "AAPL", "--db", populated_db])
    cmd_bbo(args)
    captured = capsys.readouterr()
    assert "Synthetic Consolidated Best Bid & Offer" in captured.out or "AAPL" in captured.out

    # 2. All symbols query
    args = parser.parse_args(["bbo", "all", "--db", populated_db])
    cmd_bbo(args)
    captured = capsys.readouterr()
    assert "Synthetic Consolidated Best Bid & Offer" in captured.out


def test_cmd_live_dispatch(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["live", "BTC/USD", "-l", "2", "--db", populated_db])
    
    with mock.patch("live.LiveConnector.stream_ticks") as mock_stream:
        from models import RawEvent
        mock_raw = RawEvent(
            source="BINANCE",
            payload={"instrument": "BTC/USD", "event_type": "QUOTE", "exchange_ts": 1000.0, "sequence": 1, "bid": 70000.0, "ask": 70001.0},
            receive_timestamp=1000.002,
            raw_id="mock-1",
        )
        mock_stream.return_value = [mock_raw]
        cmd_live(args)
        captured = capsys.readouterr()
        assert "MDRAP Live Market Connector" in captured.out
        assert "BINANCE" in captured.out
        assert "BTC/USD" in captured.out


def test_gemini_ui_components_render_cleanly(capsys):
    from term import render_gemini_banner, render_gemini_tips, render_gemini_box_top, render_gemini_box_bottom, Console
    console = Console()
    render_gemini_banner(console)
    render_gemini_tips(console)
    render_gemini_box_top(console)
    render_gemini_box_bottom(console)
    captured = capsys.readouterr()
    assert "M D R A P" in captured.out or "███" in captured.out
    assert "Tips for getting started" in captured.out
    assert "Using 1 GEMINI.md file" in captured.out
    assert "Streaming V2" in captured.out


def test_term_stdlib_fallback_renders_clean_text(capsys):
    from term import _StdlibConsole, _StdlibTable, _StdlibPanel, strip_tags
    
    # 1. Test strip_tags removes rich markup
    assert strip_tags("[bold green]SUCCESS[/bold green]") == "SUCCESS"
    assert strip_tags("[cyan]AAPL[/cyan]") == "AAPL"

    # 2. Test stdlib table
    t = _StdlibTable(title="Fallback Table")
    t.add_column("Symbol", justify="left")
    t.add_column("Price", justify="right")
    t.add_row("AAPL", "$150.00")
    t.add_row("MSFT", "$310.50")
    tbl_str = str(t)
    assert "Fallback Table" in tbl_str
    assert "AAPL" in tbl_str
    assert "150.00" in tbl_str

    # 3. Test stdlib panel
    p = _StdlibPanel.fit("Testing Pure Stdlib Panel", title="Test")
    p_str = str(p)
    assert "Testing Pure Stdlib Panel" in p_str

    # 4. Test stdlib console printing
    c = _StdlibConsole()
    c.print(t)
    captured = capsys.readouterr()
    assert "AAPL" in captured.out


def test_fastpath_resilience_on_fault():
    from fastpath import FastQualityEngine
    from models import CanonicalEvent, EventType
    
    engine = FastQualityEngine()
    event = CanonicalEvent(
        event_id="e1", instrument_id="AAPL", event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source="FEEDA", sequence_number=1,
        price=150.0, quantity=100.0
    )
    # Evaluate normally
    res = engine.evaluate(event)
    assert res.quality_status.value in ("VALID", "SUSPICIOUS", "INVALID")
    
    # Simulate C function failure or unavailable fast eval
    import fastpath
    orig_eval = fastpath._FAST_EVAL
    try:
        fastpath._FAST_EVAL = None  # force fallback to pure Python
        engine_fallback = FastQualityEngine()
        res2 = engine_fallback.evaluate(event)
        assert res2.quality_status.value in ("VALID", "SUSPICIOUS", "INVALID")
    finally:
        fastpath._FAST_EVAL = orig_eval


def test_cmd_security_dispatch(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["security", "--db", populated_db])
    args.func(args)
    captured = capsys.readouterr()
    assert "HMAC-SHA256" in captured.out
    assert "RBAC" in captured.out


def test_cmd_audit_dispatch(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["audit", "--db", populated_db])
    args.func(args)
    captured = capsys.readouterr()
    assert "Audit Log" in captured.out


def test_cmd_audit_verify_dispatch(populated_db, capsys):
    parser = build_parser()
    args = parser.parse_args(["audit", "--verify", "--db", populated_db])
    args.func(args)
    captured = capsys.readouterr()
    assert "CRYPTOGRAPHIC AUDIT VERIFICATION PASSED" in captured.out


def test_cmd_chaos_drill_dispatch(capsys):
    parser = build_parser()
    args = parser.parse_args(["chaos", "burst"])
    args.func(args)
    captured = capsys.readouterr()
    assert "PASS" in captured.out


def test_cmd_service_parser_dispatch():
    parser = build_parser()
    args_d = parser.parse_args(["daemon", "--port", "19999", "--speed", "500.0"])
    assert args_d.port == 19999
    assert args_d.speed == 500.0

    args_s = parser.parse_args(["sub", "BTC/USD", "--json", "-l", "10"])
    assert args_s.symbol == "BTC/USD"
    assert args_s.json is True
    assert args_s.limit == 10

    args_t = parser.parse_args(["top", "-p", "19999"])
    assert args_t.port == 19999


def test_ticker_first_and_mnemonic_dispatch():
    from cli import KNOWN_SYMBOLS, MNEMONIC_MAP, QUICK_ACTIONS, render_command_palette
    from term import Console

    # Verify symbol mappings
    assert KNOWN_SYMBOLS["BTC"] == "BTC/USD"
    assert KNOWN_SYMBOLS["AAPL"] == "AAPL"

    # Verify Bloomberg mnemonics
    assert MNEMONIC_MAP["bbo"] == "bbo"
    assert MNEMONIC_MAP["cnd"] == "ohlcv"
    assert MNEMONIC_MAP["stat"] == "status"
    assert MNEMONIC_MAP["spr"] == "spread"
    assert MNEMONIC_MAP["vol"] == "vol"

    # Verify quick actions
    assert QUICK_ACTIONS["1"] == ["live", "BTC/USD"]
    assert QUICK_ACTIONS["2"] == ["bbo", "BTC/USD"]
    assert QUICK_ACTIONS["3"] == ["top"]

    # Verify palette render does not crash
    c = Console()
    render_command_palette(c)


def test_cmd_throughput_dispatch(capsys):
    from fastpath import is_available
    if not is_available():
        pytest.skip("FastPath native library not available")
    parser = build_parser()
    args = parser.parse_args(["throughput", "-e", "5000", "--compare"])
    args.func(args)
    captured = capsys.readouterr()
    assert "MDRAP Ultra-High Throughput Vectorized SBE Engine" in captured.out
    assert "Throughput (eps)" in captured.out
    assert "TARGET" in captured.out


def test_cmd_tca_dispatch(capsys):
    parser = build_parser()
    args = parser.parse_args(["tca", "AAPL", "--demo", "--count", "50"])
    args.func(args)
    captured = capsys.readouterr()
    assert "MDRAP Institutional Best Execution & TCA Slippage Engine" in captured.out
    assert "SEC Rule 606" in captured.out
    assert "Merkle Root Hash" in captured.out


def test_cmd_flow_dispatch(capsys):
    parser = build_parser()
    args = parser.parse_args(["flow", "AAPL", "--count", "100"])
    args.func(args)
    captured = capsys.readouterr()
    assert "MDRAP Institutional Order Flow & Cumulative Volume Delta" in captured.out
    assert "Institutional Broker & Market Participant Attribution" in captured.out


def test_cmd_strategy_dispatch(capsys):
    parser = build_parser()
    # 1. list
    args = parser.parse_args(["strategy", "list"])
    args.func(args)
    captured = capsys.readouterr()
    assert "MDRAP Institutional Algorithmic Strategy Catalog" in captured.out
    assert "whale_momentum" in captured.out
    assert "spread_capture" in captured.out

    # 2. run
    args = parser.parse_args(["strategy", "run", "-s", "whale_momentum", "-i", "AAPL", "-e", "100"])
    args.func(args)
    captured = capsys.readouterr()
    assert "MDRAP Paper Trading Strategy Engine" in captured.out
    assert "Performance Tear-Sheet" in captured.out


def test_edgar_routing(monkeypatch):
    import sys
    from cli import main, build_parser

    # 1. Direct parser parse tests
    parser = build_parser()
    args_edgar = parser.parse_args(["edgar", "events", "AAPL"])
    assert args_edgar.action == "events"
    assert args_edgar.ticker == "AAPL"

    # 2. Main argv smart rewrite tests: 'company AAPL' -> profile AAPL
    called_args = []
    monkeypatch.setattr(sys, "argv", ["cli.py", "company", "AAPL"])
    monkeypatch.setattr("cli.cmd_edgar", lambda a: called_args.append(a))
    main()
    assert len(called_args) == 1
    assert called_args[0].action == "profile"
    assert called_args[0].ticker == "AAPL"

    # 'edgar AAPL' -> events AAPL
    called_args.clear()
    monkeypatch.setattr(sys, "argv", ["cli.py", "edgar", "AAPL"])
    main()
    assert len(called_args) == 1
    assert called_args[0].action == "events"
    assert called_args[0].ticker == "AAPL"


def test_edgar_open_argument():
    from cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["edgar", "filings", "NVDA", "-o"])
    assert args.open_browser is True
    assert args.action == "filings"
    assert args.ticker == "NVDA"

    args_no_open = parser.parse_args(["edgar", "filings", "NVDA"])
    assert args_no_open.open_browser is False


def test_cmd_run_duckdb_sync_error_handling(tmp_path, capsys, monkeypatch):
    from cli import build_parser, cmd_run
    parser = build_parser()
    db_file = str(tmp_path / "test.db")
    duck_file = str(tmp_path / "test.duckdb")

    with open(duck_file, "w") as f:
        f.write("dummy")

    mock_store_cls = mock.MagicMock()
    mock_store_inst = mock.MagicMock()
    mock_store_inst.sync_from_sqlite.side_effect = RuntimeError("Disk I/O lock error")
    mock_store_cls.return_value.__enter__.return_value = mock_store_inst
    monkeypatch.setattr("columnar.ColumnarStore", mock_store_cls)

    # 1. Non-strict (default): warns to stderr, records divergence in JSON, exits 0
    args_default = parser.parse_args(["run", "-e", "10", "--db", db_file])
    setattr(args_default, "duckdb", duck_file)
    cmd_run(args_default)
    captured = capsys.readouterr()
    assert "DuckDB sync failed" in captured.err
    assert "Stores diverged" in captured.err
    assert '"diverged": true' in captured.out

    # 2. Strict sync: exits 1
    args_strict = parser.parse_args(["run", "-e", "10", "--db", db_file, "--strict-sync"])
    setattr(args_strict, "duckdb", duck_file)
    with pytest.raises(SystemExit) as exc_info:
        cmd_run(args_strict)
    assert exc_info.value.code == 1

    # 3. No sync: skips sync
    mock_store_cls.reset_mock()
    args_no_sync = parser.parse_args(["run", "-e", "10", "--db", db_file, "--no-sync"])
    setattr(args_no_sync, "duckdb", duck_file)
    cmd_run(args_no_sync)
    mock_store_cls.assert_not_called()


def test_no_color_flag_and_environment(monkeypatch):
    from term import is_no_color_active, Console
    from cli import build_parser

    # 1. Environment variable test
    monkeypatch.setenv("NO_COLOR", "1")
    assert is_no_color_active() is True
    con = Console()
    assert con.no_color is True
    assert con.color_system is None

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("MDRAP_NO_COLOR", raising=False)
    assert is_no_color_active() is False

    # 2. CLI flag test
    parser = build_parser()
    args = parser.parse_args(["--no-color", "status"])
    assert getattr(args, "no_color", False) is True

    args_plain = parser.parse_args(["status", "--plain"])
    assert getattr(args_plain, "no_color", False) is True


def test_colorblind_safe_formatters():
    from term import format_status, format_direction, format_num

    # Status indicators: glyph + status
    val_status = format_status("VALID")
    assert "●" in val_status
    assert "VALID" in val_status

    warn_status = format_status("SUSPICIOUS")
    assert "▲" in warn_status
    assert "SUSPICIOUS" in warn_status

    inv_status = format_status("INVALID")
    assert "✕" in inv_status
    assert "INVALID" in inv_status

    pass_status = format_status("PASS")
    assert "●" in pass_status
    assert "PASS" in pass_status

    # Direction indicators: arrows
    up_dir = format_direction("UP", 100.50)
    assert "▲" in up_dir
    assert "$100.50" in up_dir

    down_dir = format_direction("DOWN", 95.25)
    assert "▼" in down_dir
    assert "$95.25" in down_dir

    flat_dir = format_direction("FLAT", 100.00)
    assert "■" in flat_dir

    # Number formatting
    assert format_num(1000000) == "1,000,000"
    assert format_num(1234.567, decimals=2) == "1,234.57"


def test_error_path_palette_fallback(capsys):
    from cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["completely_unrecognized_choice_xyz"])
    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    # Stderr should show clean categorized palette and not raw choice list
    assert "unrecognized command or choice" in captured.err or "unrecognized command or choice" in captured.out
    assert "Available command categories:" in captured.err or "Available command categories:" in captured.out or "Market Desk" in captured.err or "Market Desk" in captured.out


def test_completion_command_generation(capsys):
    from cli import build_parser, cmd_completion

    parser = build_parser()

    # Bash completion
    args_bash = parser.parse_args(["completion", "bash"])
    cmd_completion(args_bash)
    captured = capsys.readouterr()
    assert "_mdrap_completions()" in captured.out
    assert "complete -F _mdrap_completions mdrap" in captured.out

    # Zsh completion
    args_zsh = parser.parse_args(["completion", "zsh"])
    cmd_completion(args_zsh)
    captured = capsys.readouterr()
    assert "#compdef mdrap" in captured.out
    assert "_describe -t commands" in captured.out

    # Fish completion
    args_fish = parser.parse_args(["completion", "fish"])
    cmd_completion(args_fish)
    captured = capsys.readouterr()
    assert "complete -c mdrap" in captured.out

    # PowerShell completion
    args_ps = parser.parse_args(["completion", "powershell"])
    cmd_completion(args_ps)
    captured = capsys.readouterr()
    assert "Register-ArgumentCompleter -Native -CommandName mdrap" in captured.out


def test_trimmed_aliases_and_mnemonic_backward_compatibility():
    from cli import build_parser, MNEMONIC_MAP

    parser = build_parser()

    # 1. Primary short aliases on subparser work
    assert parser.parse_args(["s"]).func is not None
    assert parser.parse_args(["r", "-e", "10"]).func is not None
    assert parser.parse_args(["a", "ohlcv"]).func is not None
    assert parser.parse_args(["l2", "BTC/USD"]).func is not None
    assert parser.parse_args(["cvd", "AAPL"]).func is not None

    # 2. Trimmed legacy mnemonics exist in MNEMONIC_MAP for transparent interception
    legacy_keys = ["navigator", "tui", "compact", "prune", "ladder", "book", "whales", "duckdb"]
    for k in legacy_keys:
        assert k in MNEMONIC_MAP





