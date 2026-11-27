"""End-to-End integration test for MDRAP Observer with live MarketDataDaemon."""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import pytest

from mdrap.service import MarketDataDaemon
from mdrap.models import RawEvent

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer import cli as observer_cli
from observer.client import ObserverClient
from observer.dashboard import ObserverDashboard, _venue_status_style
from observer.exporter import ObserverExporter
from observer.state import ObserverState, VENUES


def test_observer_e2e_with_mdrap_daemon(monkeypatch):
    monkeypatch.setenv("MDRAP_API_KEY_SALT", "test_salt_1234567890")
    monkeypatch.setenv("MDRAP_DEMO", "1")

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=10000.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)
    port = daemon.port

    state = ObserverState()
    tmpdir = tempfile.mkdtemp()
    exporter = ObserverExporter(output_dir=tmpdir, flush_interval_s=0.1)
    exporter.start()

    def on_event(evt):
        state.process_event(evt)
        exporter.record_event(evt)

    client = ObserverClient(mode="tcp", host="127.0.0.1", port=port, on_event_callback=on_event)
    client.start()

    try:
        time.sleep(0.4)

        # Publish raw events across 5 venues for BTC and ETH
        venues_events = [
            ("BINANCE", "BTCUSDT", 65000.0, 64998.0, 65002.0),
            ("COINBASE", "BTC-USD", 65001.0, 64999.0, 65003.0),
            ("KRAKEN", "XBT/USD", 65000.5, 64998.5, 65001.5),
            ("DATABENTO", "BTC", 65000.0, 64997.0, 65004.0),
            ("ITCH_SBE", "ETH", 3450.0, 3449.0, 3451.0),
        ]

        now = time.time()
        for i, (venue, sym, price, bid, ask) in enumerate(venues_events):
            raw = RawEvent(
                source=venue,
                receive_timestamp=now,
                payload={
                    "instrument": sym,
                    "event_type": "TRADE" if "ETH" in sym else "TICK",
                    "price": price,
                    "bid": bid,
                    "ask": ask,
                    "bid_size": 2.0,
                    "ask_size": 2.0,
                    "quantity": 5.0,
                    "sequence": i + 1,
                    "exchange_ts": now,
                },
            )
            daemon._process_and_broadcast(raw)

        # Allow network delivery
        time.sleep(0.6)

        # Verify state for BTC-USD (Coinbase best bid 64999.0, Kraken best ask 65001.5)
        best_bid, _, best_ask, _ = state.get_consolidated_nbbo("BTC-USD")
        assert best_bid == 64999.0  # Coinbase
        assert best_ask == 65001.5  # Kraken

        # Verify state for BTC-USDT (Binance best bid 64998.0, best ask 65002.0)
        usdt_bid, _, usdt_ask, _ = state.get_consolidated_nbbo("BTC-USDT")
        assert usdt_bid == 64998.0  # Binance
        assert usdt_ask == 65002.0  # Binance

        # Check venue health
        assert state.health["BINANCE"].status == "CONNECTED"
        assert state.health["COINBASE"].status == "CONNECTED"
        assert state.health["KRAKEN"].status == "CONNECTED"
        assert state.health["DATABENTO"].status == "CONNECTED"
        assert state.health["ITCH_SBE"].status == "CONNECTED"

        # Check trades for ETH-USD
        assert len(state.trades["ETH-USD"]) >= 1
        assert any(t.price == 3450.0 for t in state.trades["ETH-USD"])

        # Render dashboard test
        dashboard = ObserverDashboard(state=state, mode="TCP")
        dashboard.render()

        # Check exports written
        assert os.path.exists(exporter.events_file)
        with open(exporter.events_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 5
    finally:
        client.stop()
        exporter.stop()
        daemon.stop()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_health_snapshot_and_stale_transition(monkeypatch):
    """Verify cli.main() calls check_all_stale() and emits enriched stream, venue, and exporter telemetry."""
    monkeypatch.setenv("MDRAP_API_KEY_SALT", "test_salt_1234567890")
    monkeypatch.setenv("MDRAP_DEMO", "1")

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmpdir = tempfile.mkdtemp()

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=0,
        db_path=db_path,
        use_live=False,
        enable_shm=False,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.25)
    port = daemon.port

    stop_pub = threading.Event()

    def publish_loop():
        # Wait for CLI's ObserverClient to connect
        time.sleep(0.3)
        now = time.time()
        # Send initial event for both BINANCE and COINBASE
        for venue, sym in [("BINANCE", "BTCUSDT"), ("COINBASE", "BTC-USD")]:
            daemon._process_and_broadcast(
                RawEvent(
                    source=venue,
                    receive_timestamp=now,
                    payload={
                        "instrument": sym,
                        "event_type": "TICK",
                        "price": 65000.0,
                        "bid": 64999.0,
                        "ask": 65001.0,
                        "bid_size": 1.0,
                        "ask_size": 1.0,
                        "feed_seq": 1,
                        "sequence": 1,
                        "exchange_ts": now,
                    },
                )
            )
        # Keep BINANCE alive while COINBASE goes silent so COINBASE transitions to STALE
        seq = 2
        while not stop_pub.is_set():
            time.sleep(0.1)
            t = time.time()
            daemon._process_and_broadcast(
                RawEvent(
                    source="BINANCE",
                    receive_timestamp=t,
                    payload={
                        "instrument": "BTCUSDT",
                        "event_type": "TICK",
                        "price": 65000.5,
                        "bid": 64999.5,
                        "ask": 65001.5,
                        "bid_size": 1.0,
                        "ask_size": 1.0,
                        "feed_seq": seq,
                        "sequence": seq,
                        "exchange_ts": t,
                    },
                )
            )
            seq += 1

    pub_thread = threading.Thread(target=publish_loop, daemon=True)
    pub_thread.start()

    try:
        rc = observer_cli.main(
            [
                "--mode",
                "tcp",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--export",
                tmpdir,
                "--refresh",
                "0.12",
                "--duration",
                "1.35",
                "--stale-threshold",
                "0.35",
                "--no-ui",
            ]
        )
        assert rc == 0
    finally:
        stop_pub.set()
        pub_thread.join(timeout=2.0)
        daemon.stop()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass

    try:
        health_files = glob.glob(os.path.join(tmpdir, "health_*.jsonl"))
        assert len(health_files) == 1
        with open(health_files[0], "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]

        assert len(records) >= 3

        expected_stream_keys = {"events", "gaps", "duplicates", "out_of_order", "resets", "filtered_events"}
        expected_venue_keys = {
            "status",
            "feed_type",
            "events",
            "gaps",
            "feed_gaps",
            "duplicates",
            "out_of_order",
            "feed_resets",
            "stale_epoch_packets",
            "feed_seq_unavailable",
            "reconnects",
            "reconnects_supported",
            "drops_supported",
        }

        coinbase_statuses: list[str] = []
        binance_statuses: list[str] = []

        for rec in records:
            snap = rec["data"]
            assert expected_stream_keys.issubset(snap["stream"].keys())
            assert "exporter_events_written" in snap
            assert "exporter_flushed_records" in snap
            assert "exporter_fsynced_records" in snap
            assert "exporter_dropped_records" in snap
            assert "exporter_failed_records" in snap
            assert "exporter_write_errors" in snap
            assert set(snap["venues"].keys()) == set(VENUES)
            for v in VENUES:
                assert expected_venue_keys.issubset(snap["venues"][v].keys())
                assert snap["venues"][v]["reconnects"] is None
                assert snap["venues"][v]["drops"] is None
                assert snap["venues"][v]["reconnects_supported"] is False
                assert snap["venues"][v]["drops_supported"] is False
            coinbase_statuses.append(snap["venues"]["COINBASE"]["status"])
            binance_statuses.append(snap["venues"]["BINANCE"]["status"])

        last_snap = records[-1]["data"]
        assert last_snap["stream"]["events"] >= 3
        assert "CONNECTED" in coinbase_statuses
        assert coinbase_statuses[-1] == "STALE"
        assert "CONNECTED" in binance_statuses
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cli_subprocess_emits_health_jsonl():
    """Verify running python -m observer.cli in a subprocess writes valid health_*.jsonl."""
    observer_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    tmpdir = tempfile.mkdtemp()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "observer.cli",
                "--mode",
                "tcp",
                "--host",
                "127.0.0.1",
                "--port",
                "1",
                "--export",
                tmpdir,
                "--refresh",
                "0.1",
                "--duration",
                "0.35",
                "--no-ui",
            ],
            cwd=observer_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr

        health_files = glob.glob(os.path.join(tmpdir, "health_*.jsonl"))
        assert len(health_files) == 1
        with open(health_files[0], "r", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        assert len(rows) >= 1
        snap = rows[0]["data"]
        assert "stream" in snap and "venues" in snap
        assert "exporter_events_written" in snap
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_dashboard_renders_stale_status_clearly(monkeypatch, capsys):
    """Verify dashboard distinguishes STALE (yellow) from CONNECTED (green) and DISCONNECTED (red)."""
    from rich.console import Console

    assert _venue_status_style("CONNECTED") == "bold green"
    assert _venue_status_style("STALE") == "bold yellow"
    assert _venue_status_style("DISCONNECTED") == "bold red"

    state = ObserverState(stale_threshold_s=1.0)
    state.health["BINANCE"].status = "CONNECTED"
    state.health["BINANCE"].last_seen_ts = time.time()
    state.health["COINBASE"].status = "CONNECTED"
    state.health["COINBASE"].last_seen_ts = time.time() - 5.0
    state.check_all_stale()
    assert state.health["COINBASE"].status == "STALE"

    monkeypatch.setattr(os, "system", lambda cmd: 0)

    dashboard = ObserverDashboard(state=state, mode="TCP")
    dashboard.console = Console(record=True, width=140)
    dashboard._render_rich()
    rendered = dashboard.console.export_text()
    assert "CONNECTED" in rendered
    assert "STALE" in rendered

    dashboard._render_plain()
    plain_out = capsys.readouterr().out
    assert "STALE" in plain_out
    assert "CONNECTED" in plain_out
