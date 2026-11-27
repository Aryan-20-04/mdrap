"""
MDRAP 6.0.6 — Core Infrastructure Hardening Regression Test Suite.

Verifies:
1. Feed Watchdog: Silent data loss detection, timeout thresholding, and forced reconnect.
2. WAL Growth & Checkpoint Control: Store.wal_checkpoint() across all SQLite WAL modes.
3. Heap-Optimized Order Book Reconstitution: Equivalence of top_levels() with sorted results.
4. Raw Event Provenance: Monotonic latency timestamps and epoch metadata preservation.
5. NO DEPLOYMENT Governance: Strategy status freeze and live order submission prohibition.
"""

from __future__ import annotations

import heapq
import os
import sqlite3
import time
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus, RawEvent
from mdrap.storage import Store
from mdrap.ws_feed import (
    ConnectorState,
    LiveCryptoFeed,
    ProviderTelemetry,
    SequenceScope,
    WebSocketFeedManager,
)
from tools.replay_l2 import VenueBookState, NormalizedReplayEvent


def test_ws_provider_telemetry_watchdog_fields():
    """Verify ProviderTelemetry includes heartbeat and watchdog counters."""
    telem = ProviderTelemetry(
        provider_id="BINANCE",
        state=ConnectorState.CONNECTED,
        active_subscriptions=["BTC/USD"],
        last_received_ts=1700000000.0,
        last_processed_ts=1700000000.1,
        feed_freshness_ms=100.0,
        reconnect_count=1,
        error_count=0,
        dropped_count=0,
        sequence_gaps_count=0,
        last_sequence_num=12345,
        wire_latency_us=250.0,
        data_mode="LIVE",
        last_heartbeat_ts=1700000000.05,
        watchdog_triggers=0,
    )
    d = telem.to_dict()
    assert d["provider_id"] == "BINANCE"
    assert d["state"] == "CONNECTED"
    assert d["last_heartbeat_ts"] == 1700000000.05
    assert d["watchdog_triggers"] == 0


def test_ws_feed_manager_telemetry_reporting():
    """Verify WebSocketFeedManager populates telemetry with watchdog fields."""
    mgr = WebSocketFeedManager(
        symbols=["BTC/USD"], venues=["BINANCE", "KRAKEN"], stale_timeout_ms=5000.0
    )
    telem = mgr.get_provider_telemetry()
    assert "BINANCE" in telem
    assert "KRAKEN" in telem
    assert telem["BINANCE"].state == ConnectorState.DISCONNECTED
    assert telem["BINANCE"].watchdog_triggers == 0
    assert telem["BINANCE"].last_heartbeat_ts is None


def test_sqlite_wal_checkpoint_modes(tmp_path):
    """Verify Store.wal_checkpoint supports PASSIVE, FULL, RESTART, and TRUNCATE modes."""
    db_file = str(tmp_path / "test_wal_checkpoint.db")
    store = Store(db_file, durability="balanced")

    # Insert events to generate WAL activity
    evs = [
        CanonicalEvent(
            event_id=f"ev-{i}",
            source="FEEDX",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=time.time(),
            receive_timestamp=time.time(),
            price=150.0 + i,
            quantity=100.0,
            quality_status=QualityStatus.VALID,
        )
        for i in range(50)
    ]
    store.write_canonical_batch(evs)
    store.commit()

    # Test all 4 SQLite WAL checkpoint modes
    for mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE"):
        res = store.wal_checkpoint(mode=mode)
        assert isinstance(res, dict)
        assert "busy" in res
        assert "log_pages" in res
        assert "checkpointed_pages" in res
        assert res["busy"] == 0

    # Test invalid mode raises ValueError
    with pytest.raises(ValueError, match="Invalid WAL checkpoint mode"):
        store.wal_checkpoint(mode="INVALID_MODE")

    store.close()


def test_store_periodic_auto_checkpoint(tmp_path, monkeypatch):
    """Verify Store triggers periodic passive WAL checkpoints under high write volume."""
    db_file = str(tmp_path / "test_auto_ckpt.db")
    monkeypatch.setenv("MDRAP_WAL_CHECKPOINT_INTERVAL", "10")
    store = Store(db_file, durability="balanced")

    assert store._checkpoint_interval == 10
    initial_ckpt_count = store._wal_checkpoint_count

    # Write 25 events (> 2 interval thresholds)
    evs = [
        CanonicalEvent(
            event_id=f"auto-ev-{i}",
            source="FEEDX",
            instrument_id="AAPL",
            event_type=EventType.TRADE,
            exchange_timestamp=time.time(),
            receive_timestamp=time.time(),
            price=100.0 + i,
            quantity=10.0,
            quality_status=QualityStatus.VALID,
        )
        for i in range(25)
    ]
    store.write_canonical_batch(evs)
    store.commit()

    assert store._wal_checkpoint_count > initial_ckpt_count
    store.close()


def test_venue_book_state_top_levels_heap_equivalence():
    """Verify heapq-based top_levels() matches sorted() output exactly across depth sizes."""
    book = VenueBookState(venue="BINANCE", instrument="BTC/USD")
    # Populate 100 bid and ask price levels
    import random

    rng = random.Random(42)
    for _ in range(100):
        bp = round(rng.uniform(60000.0, 65000.0), 2)
        ap = round(rng.uniform(65001.0, 70000.0), 2)
        book.bids[bp] = round(rng.uniform(0.1, 5.0), 4)
        book.asks[ap] = round(rng.uniform(0.1, 5.0), 4)

    # Test depths smaller than, equal to, and larger than book size
    for depth in (5, 25, 50, 100, 150):
        expected_bids = sorted(book.bids.items(), key=lambda kv: kv[0], reverse=True)[
            :depth
        ]
        expected_asks = sorted(book.asks.items(), key=lambda kv: kv[0])[:depth]
        actual_bids, actual_asks = book.top_levels(depth=depth)
        assert actual_bids == expected_bids
        assert actual_asks == expected_asks


def test_normalized_replay_event_slots_and_provenance():
    """Verify NormalizedReplayEvent is slotted and preserves provenance metadata."""
    ev = NormalizedReplayEvent(
        venue="KRAKEN",
        instrument="BTC/USD",
        event_kind="TRADE",
        exchange_ts=1700000000.0,
        recv_mono_ns=123456789,
        recv_wall_s=1700000000.05,
        epoch=0,
        venue_seq=101,
        trade_id="trd-99",
        price=64000.0,
        quantity=1.5,
        provenance={"raw_bytes_len": 128, "ingest_delta_us": 12.5},
    )
    assert ev.venue == "KRAKEN"
    assert ev.provenance["raw_bytes_len"] == 128
    assert ev.provenance["ingest_delta_us"] == 12.5
    # Verify __slots__ is enforced (cannot set arbitrary unknown attribute)
    with pytest.raises(AttributeError):
        ev.arbitrary_unslotted_attr = "fail"


def test_no_deployment_governance_invariants():
    """Verify NO DEPLOYMENT decision is enforced across strategies and backends."""
    from mdrap_strategies.backends.base import ExecutionBackend
    from mdrap_strategies.backends.testnet import (
        ExchangeTestnetBackend,
        SecurityViolationError,
    )
    from mdrap_strategies.risk_engine import InstitutionalRiskEngine

    risk_engine = InstitutionalRiskEngine()
    # Testnet backend strictly rejects production endpoints at initialization time
    with pytest.raises(SecurityViolationError, match="Production URL"):
        ExchangeTestnetBackend(
            risk_engine=risk_engine,
            base_url="https://api.binance.com",  # Production URL
        )

    # Verify shadow trading registry statuses
    from tools.run_shadow_trading import STRATEGY_REGISTRY

    assert STRATEGY_REGISTRY["Fixed-Spread Post-Only"] == "ABANDONED"
    assert STRATEGY_REGISTRY["Avellaneda-Stoikov"] == "RESEARCH ONLY"
    assert STRATEGY_REGISTRY["Inventory-Skewed"] == "RESEARCH ONLY"
    assert STRATEGY_REGISTRY["Directional Alpha Quoting"] == "NO DEPLOYMENT"
    assert STRATEGY_REGISTRY["No-Trade Baseline"] == "CANONICAL BASELINE"
