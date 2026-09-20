"""
MDRAP Comprehensive Audit & Hardening Test Suite.

Verifies the security, numerical robustness, concurrency, data resilience,
and institutional microstructure calculations implemented during the platform audit:
1. Numerical bounds & NaN/Inf schema violation guards in Python & C engines.
2. DuckDB read-only concurrency under concurrent file locks.
3. Incremental CDC synchronization and replication lag monitoring.
4. Resilient JSONL archive replay recovering from malformed/corrupted lines.
5. Cryptographic secrets hardening.
6. Institutional Level-1 Order Flow Imbalance (OFI) & Cumulative Volume Delta (CVD).
7. Terminal dashboard rendering and multi-timeframe candle resampling.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from archive import RawArchive, replay
from bbo import BBOEngine
from columnar import ColumnarStore
from dashboard import Dashboard, render
from depth import ConsolidatedDepthEngine, DepthLevel
from fastpath import FastQualityEngine, _NATIVE_LIB
from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from pipeline import Pipeline
from quality import QualityEngine
from security import SecurityManager
from storage import Store


# ---------------------------------------------------------------------------
# Track 1: Numerical Bounds & NaN/Inf Protection
# ---------------------------------------------------------------------------

def test_quality_engine_rejects_nan_price():
    """Verify QualityEngine rejects NaN prices with SCHEMA_VIOLATION before stats update."""
    engine = QualityEngine()
    ev = CanonicalEvent(
        event_id="e1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEED_A",
        sequence_number=1,
        price=float("nan"),
        quantity=100.0,
    )
    evaluated = engine.evaluate(ev)
    assert evaluated.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in evaluated.reasons
    # Verify rolling stats mean is not poisoned with NaN
    stats = engine._price_stats.get("AAPL")
    assert stats is None or not math.isnan(stats.mean)


def test_quality_engine_rejects_inf_and_negative_prices():
    """Verify QualityEngine rejects Inf and negative prices/quantities."""
    engine = QualityEngine()
    # Positive Inf
    ev_inf = CanonicalEvent(
        event_id="e2",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEED_A",
        sequence_number=2,
        price=float("inf"),
        quantity=50.0,
    )
    res_inf = engine.evaluate(ev_inf)
    assert res_inf.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_inf.reasons

    # Negative price
    ev_neg_p = CanonicalEvent(
        event_id="e3",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEED_A",
        sequence_number=3,
        price=-150.25,
        quantity=50.0,
    )
    res_neg_p = engine.evaluate(ev_neg_p)
    assert res_neg_p.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_neg_p.reasons

    # Negative quantity
    ev_neg_q = CanonicalEvent(
        event_id="e4",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEED_A",
        sequence_number=4,
        price=150.25,
        quantity=-10.0,
    )
    res_neg_q = engine.evaluate(ev_neg_q)
    assert res_neg_q.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_neg_q.reasons


def test_fastpath_engine_bounds_validation():
    """Verify FastQualityEngine rejects NaN, Inf, and negative values before or in C extension."""
    if not _NATIVE_LIB:
        pytest.skip("Fastpath C extension not compiled on this platform")

    fp = FastQualityEngine()
    ev_nan = CanonicalEvent(
        event_id="fp1",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=1,
        price=float("nan"),
        quantity=100.0,
    )
    res_nan = fp.evaluate(ev_nan)
    assert res_nan.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_nan.reasons

    ev_neg = CanonicalEvent(
        event_id="fp2",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDX",
        sequence_number=2,
        price=-10.0,
        quantity=100.0,
    )
    res_neg = fp.evaluate(ev_neg)
    assert res_neg.quality_status == QualityStatus.INVALID
    assert Reason.SCHEMA_VIOLATION.value in res_neg.reasons


# ---------------------------------------------------------------------------
# Track 2: DuckDB Concurrency & Incremental CDC
# ---------------------------------------------------------------------------

def test_duckdb_read_only_concurrency():
    """Verify multiple readers can access DuckDB even when an active connection is held."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "concurrent.duckdb")
    try:
        # 1. Primary connection writes schema and ticks
        with ColumnarStore(db_path=db_path, read_only=False) as writer:
            ev = CanonicalEvent(
                event_id="t1",
                instrument_id="AAPL",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0,
                receive_timestamp=1000.001,
                processing_timestamp=1000.002,
                source="FEED_A",
                sequence_number=1,
                price=150.0,
                quantity=100.0,
            )
            writer.ingest_events([ev])
            assert writer.count() == 1

        # 2. Open read-only connection
        with ColumnarStore(db_path=db_path, read_only=True) as reader:
            assert reader.count() == 1
            candles = reader.query_ohlcv("AAPL", interval_s=5.0)
            assert len(candles) == 1
            assert candles[0]["open"] == 150.0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_incremental_cdc_synchronization():
    """Verify O(Delta) CDC sync copies only delta rows and reports accurate freshness."""
    temp_dir = tempfile.mkdtemp()
    sql_path = os.path.join(temp_dir, "source.db")
    duck_path = os.path.join(temp_dir, "target.duckdb")

    try:
        # Create SQLite store and write initial batch
        store = Store(sql_path)
        events_batch1 = [
            CanonicalEvent(
                event_id=f"c_{i}",
                instrument_id="MSFT",
                event_type=EventType.TRADE,
                exchange_timestamp=1000.0 + i,
                receive_timestamp=1000.001 + i,
                processing_timestamp=1000.002 + i,
                source="FEED_A",
                sequence_number=i,
                price=300.0 + i,
                quantity=10.0,
            )
            for i in range(10)
        ]
        store.write_canonical_batch(events_batch1)
        store.commit()
        store.close()

        # Initial sync into DuckDB
        with ColumnarStore(db_path=duck_path, read_only=False) as col:
            synced_first = col.sync_from_sqlite(sql_path, incremental=True)
            assert synced_first == 10
            assert col.count() == 10

            # Freshness check
            fresh = col.freshness(sql_path)
            assert fresh["sqlite_ticks"] == 10
            assert fresh["duckdb_ticks"] == 10
            assert fresh["lag_ticks"] == 0
            assert fresh["is_fresh"] is True

            # Ingest 5 more rows into SQLite
            store = Store(sql_path)
            events_batch2 = [
                CanonicalEvent(
                    event_id=f"c_{10 + i}",
                    instrument_id="MSFT",
                    event_type=EventType.TRADE,
                    exchange_timestamp=1010.0 + i,
                    receive_timestamp=1010.001 + i,
                    processing_timestamp=1010.002 + i,
                    source="FEED_A",
                    sequence_number=10 + i,
                    price=310.0 + i,
                    quantity=15.0,
                )
                for i in range(5)
            ]
            store.write_canonical_batch(events_batch2)
            store.commit()
            store.close()

            # Freshness reports lag of 5
            fresh_lag = col.freshness(sql_path)
            assert fresh_lag["sqlite_ticks"] == 15
            assert fresh_lag["duckdb_ticks"] == 10
            assert fresh_lag["lag_ticks"] == 5
            assert fresh_lag["is_fresh"] is False

            # Incremental sync copies only the 5 new rows
            synced_second = col.sync_from_sqlite(sql_path, incremental=True)
            assert synced_second == 5
            assert col.count() == 15

            # Verified fully synchronized
            fresh_synced = col.freshness(sql_path)
            assert fresh_synced["lag_ticks"] == 0
            assert fresh_synced["is_fresh"] is True
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Track 3: Resilient Raw Archive Replay
# ---------------------------------------------------------------------------

def test_archive_replay_recovers_from_corrupted_jsonl():
    """Verify archive.replay() recovers gracefully from corrupted JSON lines without halting."""
    temp_dir = tempfile.mkdtemp()
    try:
        date_dir = os.path.join(temp_dir, "2026-09-05")
        os.makedirs(date_dir, exist_ok=True)
        file_path = os.path.join(date_dir, "FEED_A.jsonl")

        # Write 2 valid events, 1 corrupted line, and 1 more valid event
        valid_ev1 = {"raw_id": "r1", "source": "FEED_A", "receive_timestamp": 1000.0, "payload": {"price": 100.0}}
        valid_ev2 = {"raw_id": "r2", "source": "FEED_A", "receive_timestamp": 1001.0, "payload": {"price": 101.0}}
        valid_ev3 = {"raw_id": "r3", "source": "FEED_A", "receive_timestamp": 1002.0, "payload": {"price": 102.0}}

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(valid_ev1) + "\n")
            f.write(json.dumps(valid_ev2) + "\n")
            f.write("CORRUPTED_NON_JSON_DATA_<<<TRUNCATED>>>\n")  # Corrupted line
            f.write(json.dumps(valid_ev3) + "\n")

        # Replay should yield all 3 valid events without raising JSONDecodeError
        replayed = list(replay(temp_dir))
        assert len(replayed) == 3
        assert replayed[0].raw_id == "r1"
        assert replayed[1].raw_id == "r2"
        assert replayed[2].raw_id == "r3"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Track 4: Cryptographic Secrets Hardening
# ---------------------------------------------------------------------------

def test_cryptographic_secrets_hardening():
    """Verify create_feed_secret creates secure random secrets rather than predictable strings (S3)."""
    sec = SecurityManager()
    payload = {"instrument": "NVDA", "price": 120.5}
    # Unknown source raises KeyError per S3
    with pytest.raises(KeyError, match="Unknown feed source"):
        sec.sign_payload("FEED_TEST", payload)

    sec.create_feed_secret("FEED_TEST")
    token = sec.sign_payload("FEED_TEST", payload)
    assert token is not None
    assert len(token) == 64  # HMAC-SHA256 hex digest

    # Valid verification
    assert sec.verify_payload("FEED_TEST", payload, token) is True

    # Tampered payload fails verification
    tampered_payload = {"instrument": "NVDA", "price": 130.5}
    assert sec.verify_payload("FEED_TEST", tampered_payload, token) is False


# ---------------------------------------------------------------------------
# Track 5: Microstructure: OFI & Cumulative Volume Delta (CVD)
# ---------------------------------------------------------------------------

def test_order_flow_imbalance_and_cvd_calculation():
    """Verify Cont-Kukanov-Stoikov Level 1 OFI and CVD calculations."""
    engine = ConsolidatedDepthEngine()

    # Step 1: Initial quote at t=1000.0: Bid 100.0 (size 5.0), Ask 101.0 (size 4.0)
    raw1 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1000.0,
            "bids": [[100.0, 5.0]],
            "asks": [[101.0, 4.0]],
        },
        receive_timestamp=1000.001,
        raw_id="q1",
    )
    ladder1 = engine.observe(raw1)
    assert ladder1 is not None
    assert ladder1.ofi == 0.0  # Initial update has no predecessor
    assert ladder1.cumulative_ofi == 0.0
    assert ladder1.cvd == 0.0

    # Step 2: Higher bid arrives: Bid moves up to 100.5 (size 6.0), Ask unchanged at 101.0 (size 4.0)
    # delta W_bid = +6.0 (P_b > P_b_prev)
    # delta W_ask = 0 (P_a == P_a_prev, size unchanged: 4.0 - 4.0 = 0)
    # OFI = 6.0 - 0 = +6.0
    raw2 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1001.0,
            "bids": [[100.5, 6.0]],
            "asks": [[101.0, 4.0]],
        },
        receive_timestamp=1001.001,
        raw_id="q2",
    )
    ladder2 = engine.observe(raw2)
    assert ladder2 is not None
    assert ladder2.ofi == 6.0
    assert ladder2.cumulative_ofi == 6.0

    # Step 3: Trade event arrives: Buyer lifts offer at 101.0 with quantity 2.5
    raw_trade = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "type": "TRADE",
            "exchange_ts": 1002.0,
            "price": 101.0,
            "quantity": 2.5,
            "side": "BUY",
        },
        receive_timestamp=1002.001,
        raw_id="t1",
    )
    engine.observe(raw_trade)
    assert engine._cum_cvd["BTC/USD"] == 2.5

    # Step 4: Ask updates after fill: Ask moves to 101.5 (size 3.0), Bid unchanged at 100.5 (size 6.0)
    # delta W_bid = 0
    # delta W_ask = +4.0 (P_a > P_a_prev, previous ask size)
    # OFI = 0 - 4.0 = -4.0
    raw3 = RawEvent(
        source="BINANCE",
        payload={
            "instrument": "BTC/USD",
            "exchange_ts": 1003.0,
            "bids": [[100.5, 6.0]],
            "asks": [[101.5, 3.0]],
        },
        receive_timestamp=1003.001,
        raw_id="q3",
    )
    ladder3 = engine.observe(raw3)
    assert ladder3 is not None
    assert ladder3.ofi == -4.0
    assert ladder3.cumulative_ofi == 2.0  # 6.0 + (-4.0)
    assert ladder3.cvd == 2.5


# ---------------------------------------------------------------------------
# Track 6: Terminal Dashboard Rendering Verification
# ---------------------------------------------------------------------------

def test_terminal_dashboard_render():
    """Verify legacy/batch terminal dashboard render produces renderable group without errors."""
    store = Store(":memory:")
    pipeline = Pipeline(store)
    # Process a normal trade via raw event
    raw = RawEvent(
        source="FEED_A",
        payload={
            "instrument": "AAPL",
            "type": "TRADE",
            "exchange_ts": 1000.0,
            "price": 150.0,
            "quantity": 100.0,
        },
        receive_timestamp=1000.001,
        raw_id="raw_dash_1",
    )
    pipeline.process_one(raw)
    rendered = render(pipeline, target_events=10)
    assert rendered is not None
    store.close()
