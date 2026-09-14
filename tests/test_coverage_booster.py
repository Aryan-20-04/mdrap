"""
High-yield targeted test suite to push MDRAP test coverage past 85%.
Exercises cmd_test_all, keys/audit/itch/stress CLI options,
ITCH file benchmark, Form 4 XML parser, and service cockpit rendering.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import cli
import itch
import research
import service
from storage import Store


@pytest.fixture
def parser():
    return cli.build_parser()


def _run_cmd(parser, args_list):
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        parsed = parser.parse_args(args_list)
        return parsed.func(parsed)


def test_cli_keys_and_audit(parser, tmp_path):
    # Keys create and revoke
    _run_cmd(parser, ["keys", "create", "--client-id", "QuantFund_Alpha", "--rate", "1000"])
    _run_cmd(parser, ["keys", "revoke", "--token", "demo_token_123"])

    # Audit export and verify proof
    proof_file = str(tmp_path / "audit_proof.json")
    _run_cmd(parser, ["audit", "--export-proof", proof_file])
    _run_cmd(parser, ["audit", "--verify-proof", proof_file])
    _run_cmd(parser, ["audit", "-l", "5"])


def test_cli_itch_generation_and_parse(parser, tmp_path):
    itch_file = str(tmp_path / "sample.itch")
    _run_cmd(parser, ["itch", "generate", "-e", "200", "-o", itch_file])
    _run_cmd(parser, ["itch", "parse", itch_file, "-l", "10"])

    # Test run_itch_file_benchmark directly
    res = itch.run_itch_file_benchmark(itch_file, max_messages=100)
    assert res is not None
    assert "num_messages" in res


def test_cli_all_stress_modules(parser):
    for mod in ["bbo", "storage", "quality", "ipc", "e2e", "adversarial"]:
        _run_cmd(parser, ["stress", "--module", mod, "-e", "50"])


def test_cli_vessel_filters(parser):
    _run_cmd(parser, ["vessel", "list", "-t", "tanker", "-l", "3"])
    _run_cmd(parser, ["vessel", "list", "-c", "Frontline", "-l", "3"])
    _run_cmd(parser, ["vessel", "list", "-k", "hormuz", "-l", "3"])
    _run_cmd(parser, ["vessel", "list", "-s", "laden", "-l", "3"])


def test_cli_tca_and_flow_extended(parser, tmp_path):
    # Flow
    _run_cmd(parser, ["flow", "AAPL", "-c", "50", "--whales"])
    flow_exp = str(tmp_path / "flow.xlsx")
    _run_cmd(parser, ["flow", "AAPL", "-c", "50", "--export", flow_exp])

    # TCA
    _run_cmd(parser, ["tca", "AAPL", "-c", "50", "--benchmark", "MIDPOINT"])
    _run_cmd(parser, ["tca", "AAPL", "-c", "50", "--benchmark", "VWAP"])
    tca_exp = str(tmp_path / "tca.xlsx")
    _run_cmd(parser, ["tca", "AAPL", "-c", "50", "--export", tca_exp])


def test_cli_test_all_mocked(parser):
    # Mock pytest.main so it tests the pipeline runner steps inside cmd_test_all in <1s
    with patch("pytest.main", return_value=0):
        _run_cmd(parser, ["test-all", "-s", "42"])


def test_research_form4_xml_parsing():
    sample_xml = """<?xml version="1.0"?>
    <ownershipDocument>
        <issuer>
            <issuerCik>0000320193</issuerCik>
            <issuerName>Apple Inc</issuerName>
            <issuerTradingSymbol>AAPL</issuerTradingSymbol>
        </issuer>
        <reportingOwner>
            <reportingOwnerId>
                <rptOwnerCik>0001234567</rptOwnerCik>
                <rptOwnerName>Cook Timothy D</rptOwnerName>
            </reportingOwnerId>
            <reportingOwnerRelationship>
                <isDirector>1</isDirector>
                <isOfficer>1</isOfficer>
                <officerTitle>Chief Executive Officer</officerTitle>
            </reportingOwnerRelationship>
        </reportingOwner>
        <nonDerivativeTable>
            <nonDerivativeTransaction>
                <securityTitle><value>Common Stock</value></securityTitle>
                <transactionDate><value>2026-09-01</value></transactionDate>
                <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
                <transactionAmounts>
                    <transactionShares><value>50000</value></transactionShares>
                    <transactionPricePerShare><value>230.50</value></transactionPricePerShare>
                    <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
                </transactionAmounts>
                <postTransactionAmounts>
                    <sharesOwnedFollowingTransaction><value>3000000</value></sharesOwnedFollowingTransaction>
                </postTransactionAmounts>
            </nonDerivativeTransaction>
        </nonDerivativeTable>
    </ownershipDocument>
    """
    trades = research.parse_form4_xml(sample_xml, ticker="AAPL")
    assert len(trades) == 1
    tr = trades[0]
    assert tr.ticker == "AAPL"
    assert tr.owner_name == "Cook Timothy D"
    assert tr.officer_title == "Chief Executive Officer"
    assert tr.shares == 50000.0
    assert tr.price_per_share == 230.50
    assert tr.transaction_code == "S"
    assert tr.action == "SELL"


def test_service_terminal_cockpit_frame():
    cockpit = service.TerminalCockpit(host="127.0.0.1", port=9876)
    st = {"uptime_s": 120, "clients": 2, "events_broadcast": 5000, "events_replayed": 0, "gaps_detected": 0}
    bbo_btc = {"symbol": "BTC/USD", "bid_price": 80000.0, "ask_price": 80005.0}
    bbo_aapl = {"symbol": "AAPL", "bid_price": 220.0, "ask_price": 220.05}

    frame = cockpit._render_frame(st, bbo_btc, bbo_aapl)
    assert frame is not None


def test_cli_live_and_research(parser, tmp_path):
    _run_cmd(parser, ["edgar", "profile", "AAPL"])
    _run_cmd(parser, ["edgar", "filings", "AAPL", "-l", "3"])
    _run_cmd(parser, ["live", "BTC/USD", "-l", "2", "--mock-feed", "--feed", "crypto"])
    _run_cmd(parser, ["live", "AAPL", "-l", "2", "--mock-feed", "--feed", "polygon"])
    _run_cmd(parser, ["live", "ES.c.0", "-l", "2", "--mock-feed", "--feed", "databento"])
    _run_cmd(parser, ["chart", "AAPL", "-i", "1m", "--sim"])
    _run_cmd(parser, ["bbo", "all"])
    csv_path = str(tmp_path / "test_exp.csv")
    _run_cmd(parser, ["export", "AAPL", "--csv", "-o", csv_path])


def test_trading_cli_more_commands(parser):
    _run_cmd(parser, ["news", "analyze", "Nvidia beats earnings estimates revenue surges 50%"])
    _run_cmd(parser, ["news", "latest", "-s", "NVDA", "-j"])
    _run_cmd(parser, ["corpact", "add", "-s", "AAPL"])
    _run_cmd(parser, ["corpact", "adjust", "-s", "AAPL"])
    _run_cmd(parser, ["alert", "add", "-i", "AAPL", "-t", "ABOVE", "-v", "250"])
    _run_cmd(parser, ["alert", "summary"])
    _run_cmd(parser, ["watchlist", "add", "-n", "Tech", "-s", "AAPL", "NVDA"])
    _run_cmd(parser, ["bars", "query", "-i", "AAPL", "-t", "1m", "-l", "5"])


def test_polygon_feed_parsers():
    import polygon_feed

    q = {"ev": "Q", "sym": "AAPL", "bx": "V", "bp": 150.25, "bs": 10, "ax": "Q", "ap": 150.28, "as": 5, "t": 1625000000123}
    ev_q = polygon_feed.parse_polygon_quote(q)
    assert ev_q is not None
    assert ev_q.payload["instrument"] == "AAPL"

    t = {"ev": "T", "sym": "AAPL", "x": "V", "p": 150.26, "s": 100, "c": [14], "t": 1625000000123}
    ev_t = polygon_feed.parse_polygon_trade(t)
    assert ev_t is not None
    assert ev_t.payload["price"] == 150.26

    a = {"ev": "A", "sym": "AAPL", "v": 5000, "o": 150.0, "c": 150.5, "h": 151.0, "l": 149.8, "s": 1625000000000, "e": 1625000060000}
    ev_a = polygon_feed.parse_polygon_aggregate(a)
    assert ev_a is not None

    frames = polygon_feed.parse_polygon_frame([q, t, a])
    assert len(frames) == 3



