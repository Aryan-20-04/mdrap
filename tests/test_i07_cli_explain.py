"""Unit tests for Program I-7: CLI mdrap explain command."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
import pytest

from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent
from mdrap.cli.operations import cmd_explain
from mdrap.cli import build_parser


def test_cli_explain_json_valid_event(capsys: pytest.CaptureFixture[str]) -> None:
    """Test mdrap explain --json returns structured state and reasons for a valid event."""
    now = time.time()
    with tempfile.TemporaryDirectory() as wal_dir:
        log = IngestLog(wal_dir, fsync_policy="always")
        ev = RawEvent(
            source="BINANCE",
            receive_timestamp=now,
            payload={
                "instrument": "BTC-USDT",
                "event_type": "TRADE",
                "sequence": 1,
                "price": 60000.0,
                "quantity": 1.5,
                "exchange_ts": now,
            },
        )
        log.append(ev)
        log.close()

        args = argparse.Namespace(
            offset=0,
            wal_path=wal_dir,
            json=True,
        )
        ret = cmd_explain(args)
        assert ret == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["offset"] == 0
        assert data["source"] == "BINANCE"
        assert data["quality_status"] == "VALID"
        assert data["reasons"] == []
        assert data["quarantined"] is False
        assert data["state_context"]["stream_key"] == "BINANCE:BTC-USDT"
        assert data["state_context"]["last_sequence"] is None  # before event 0 was processed


def test_cli_explain_json_sequence_gap(capsys: pytest.CaptureFixture[str]) -> None:
    """Test mdrap explain --json detects and annotates SEQUENCE_GAP anomaly."""
    with tempfile.TemporaryDirectory() as wal_dir:
        log = IngestLog(wal_dir, fsync_policy="always")
        # Event 0: seq 10
        log.append(
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "sequence": 10,
                    "price": 150.0,
                    "quantity": 100.0,
                    "exchange_ts": 1700000000.0,
                },
            )
        )
        # Event 1: seq 15 (gap: missing 11, 12, 13, 14)
        log.append(
            RawEvent(
                source="NASDAQ",
                payload={
                    "instrument": "AAPL",
                    "event_type": "TRADE",
                    "sequence": 15,
                    "price": 150.1,
                    "quantity": 50.0,
                    "exchange_ts": 1700000001.0,
                },
            )
        )
        log.close()

        args = argparse.Namespace(
            offset=1,
            wal_path=wal_dir,
            json=True,
        )
        ret = cmd_explain(args)
        assert ret == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["offset"] == 1
        assert data["quality_status"] == "SUSPICIOUS"
        assert "SEQUENCE_GAP" in data["reasons"]
        assert len(data["reasons_annotated"]) >= 1
        assert data["reasons_annotated"][0]["code"] == "SEQUENCE_GAP"
        assert data["state_context"]["last_sequence"] == 10  # seq before event 1


def test_cli_explain_interactive_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Test mdrap explain renders rich terminal tables in interactive mode."""
    with tempfile.TemporaryDirectory() as wal_dir:
        log = IngestLog(wal_dir, fsync_policy="always")
        log.append(
            RawEvent(
                source="COINBASE",
                payload={
                    "instrument": "ETH-USD",
                    "event_type": "QUOTE",
                    "sequence": 1,
                    "bid": 3000.0,
                    "ask": 3000.5,
                    "exchange_ts": 1700000000.0,
                },
            )
        )
        log.close()

        args = argparse.Namespace(
            offset=0,
            wal_path=wal_dir,
            json=False,
        )
        ret = cmd_explain(args)
        assert ret == 0

        captured = capsys.readouterr()
        assert "MDRAP EVENT DECISION EXPLAINER" in captured.out
        assert "Raw Event Ingest Frame" in captured.out
        assert "Quality Classification Reasons" in captured.out


def test_cli_explain_parser_integration() -> None:
    """Test build_parser correctly parses `mdrap explain 42 --wal-path custom/wal --json`."""
    parser = build_parser()
    args = parser.parse_args(["explain", "42", "--wal-path", "custom/wal", "--json"])
    assert args.command == "explain"
    assert args.offset == 42
    assert args.wal_path == "custom/wal"
    assert args.json is True


def test_cli_explain_error_handling(capsys: pytest.CaptureFixture[str]) -> None:
    """Test clean error reporting when offset or WAL path does not exist."""
    # 1. Non-existent WAL
    args_bad_wal = argparse.Namespace(
        offset=0,
        wal_path="nonexistent_wal_dir_12345",
        json=True,
    )
    ret1 = cmd_explain(args_bad_wal)
    assert ret1 == 1
    cap1 = capsys.readouterr()
    assert "does not exist" in cap1.out

    # 2. Offset beyond log head
    with tempfile.TemporaryDirectory() as wal_dir:
        log = IngestLog(wal_dir, fsync_policy="always")
        log.append(RawEvent(source="TEST", payload={"instrument": "X", "event_type": "TRADE", "price": 10.0}))
        log.close()

        args_bad_offset = argparse.Namespace(
            offset=999,
            wal_path=wal_dir,
            json=True,
        )
        ret2 = cmd_explain(args_bad_offset)
        assert ret2 == 1
        cap2 = capsys.readouterr()
        assert "Offset 999 not found" in cap2.out
