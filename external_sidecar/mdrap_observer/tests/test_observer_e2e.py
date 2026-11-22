"""End-to-End integration test for MDRAP Observer with live MarketDataDaemon."""

import os
import sys
import tempfile
import time
import pytest

from mdrap.service import MarketDataDaemon
from mdrap.models import RawEvent

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from observer.client import ObserverClient
from observer.dashboard import ObserverDashboard
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
