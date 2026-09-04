"""
MDRAP Long-Term Maintainable Architecture & Reliability Hardening Test Suite.

Verifies:
1. Slow Consumer Isolation (Head-of-Line Blocking elimination in IPC streaming).
2. SQLite Multi-Threaded Concurrency (Thread-safe reentrant locking & retry).
3. Rolling Stats Clean Baseline Immunity (Decoupled anomaly detection from Welford window).
4. EWMA Dynamic Reliability Tracking (Responsive degradation without ratio dilution).
5. Rate-Limit Quarantine Invariant (Spec §26 Principle 3: never drop silently).
6. Hybrid Time-and-Count Batch Flusher (Low-volume feed persistence guarantee).
7. FastPath Capacity Fallback (Graceful fallback when exceeding C static table limits).
8. BBO TTL Staleness Guard (Expired quote eviction).
"""
import os
import sys
import time
import socket
import tempfile
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus, RawEvent, Reason
from storage import Store
from service import MarketDataDaemon, StreamClient
from quality import QualityEngine, QualityConfig
from fastpath import FastQualityEngine
from reconciliation import ReliabilityTracker, SourceStats
from pipeline import Pipeline
from security import SecurityManager, TokenBucketRateLimiter
from bbo import BBOEngine


def _make_trade(eid: str, inst: str, price: float, ts: float, src: str = "FEEDX", seq: int = 1) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=eid,
        instrument_id=inst,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=src,
        sequence_number=seq,
        price=price,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )


# =========================================================================
# 1. Slow Consumer Isolation (Head-of-Line Blocking Elimination)
# =========================================================================
def test_slow_consumer_queue_isolation():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    daemon = MarketDataDaemon(
        host="127.0.0.1",
        port=19877,
        db_path=db_path,
        use_live=False,
        sim_speed_eps=0.0,
    )
    daemon.start(blocking=False)
    time.sleep(0.3)

    try:
        # Fast consumer
        client_fast = StreamClient(host="127.0.0.1", port=daemon.port)
        client_fast.connect()
        client_fast.sock.sendall(b"SUBSCRIBE ALL\n")
        time.sleep(0.05)

        # Slow consumer (connects and subscribes, but never reads)
        s_slow = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s_slow.connect(("127.0.0.1", daemon.port))
        s_slow.sendall(b"SUBSCRIBE ALL\n")
        time.sleep(0.05)

        # Inject 1,200 events rapidly (exceeding queue maxsize=1000)
        for i in range(1200):
            raw = RawEvent(
                source="TESTFEED",
                payload={"instrument": "AAPL", "price": 150.0 + i * 0.01, "quantity": 100, "sequence": i + 1, "exchange_ts": 1000.0 + i},
                receive_timestamp=1000.0 + i,
                raw_id=f"tick_{i}",
            )
            daemon._process_and_broadcast(raw)

        time.sleep(0.2)

        # Fast client reads ticks successfully without blocking
        ticks = list(client_fast.stream(symbol="ALL", limit=10))
        assert len(ticks) == 10

        # Slow client session should have dropped ticks due to full queue
        with daemon._sub_lock:
            dropped = sum(s.dropped_ticks for s in daemon._sessions.values())
            assert dropped > 0, f"Expected dropped ticks > 0, got {dropped}"

        client_fast.close()
        s_slow.close()

    finally:
        daemon.stop()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


# =========================================================================
# 2. SQLite Multi-Threaded Concurrency (Thread-Safe Store)
# =========================================================================
def test_sqlite_multithreaded_concurrency():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(db_path)
    errors = []
    stop_event = threading.Event()

    def writer_worker():
        batch_id = 0
        while not stop_event.is_set() and batch_id < 20:
            events = [
                _make_trade(f"w_{batch_id}_{i}", "AAPL", 150.0 + i, 1000.0 + i)
                for i in range(50)
            ]
            try:
                store.write_canonical_batch(events)
                store.commit()
                batch_id += 1
                time.sleep(0.01)
            except Exception as e:
                errors.append(f"Writer error: {e}")

    def reader_worker(reader_id: int):
        while not stop_event.is_set():
            try:
                res = store.latest("AAPL", limit=10)
                assert isinstance(res, list)
                time.sleep(0.005)
            except Exception as e:
                errors.append(f"Reader-{reader_id} error: {e}")

    threads = [threading.Thread(target=writer_worker)]
    for r in range(4):
        threads.append(threading.Thread(target=reader_worker, args=(r,)))

    for t in threads:
        t.start()

    time.sleep(0.5)
    stop_event.set()
    for t in threads:
        t.join(timeout=3.0)

    store.close()
    if os.path.exists(db_path):
        try:
            os.unlink(db_path)
        except Exception:
            pass

    assert len(errors) == 0, f"Encountered concurrency errors: {errors}"


# =========================================================================
# 3. Clean Baseline Anomaly Immunity (No Variance Poisoning)
# =========================================================================
def test_clean_baseline_welford_immunity():
    engine = QualityEngine()

    # Step 1: Establish stable baseline for AAPL at $150
    for i in range(40):
        ev = _make_trade(f"base_{i}", "AAPL", 150.0 + (i % 2) * 0.05, 1000.0 + i, seq=i + 1)
        evaluated = engine.evaluate(ev)
        assert evaluated.quality_status == QualityStatus.VALID

    # Step 2: Inject a massive price outlier ($100,000 on $150 AAPL)
    outlier1 = _make_trade("spike_1", "AAPL", 100_000.0, 1045.0, seq=100)
    evaluated1 = engine.evaluate(outlier1)
    assert evaluated1.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.PRICE_ANOMALY.value in evaluated1.reasons

    # Step 3: Under previous code, $100,000 poisoned the rolling window variance,
    # causing subsequent real anomalies (e.g. $200 on $150 stock) to be falsely treated as VALID.
    # With clean baseline protection, the $100,000 outlier was excluded from the window.
    outlier2 = _make_trade("spike_2", "AAPL", 200.0, 1046.0, seq=101)
    evaluated2 = engine.evaluate(outlier2)
    assert evaluated2.quality_status == QualityStatus.SUSPICIOUS
    assert Reason.PRICE_ANOMALY.value in evaluated2.reasons


# =========================================================================
# 4. EWMA Dynamic Reliability Tracking
# =========================================================================
def test_ewma_reliability_responsiveness():
    tracker = ReliabilityTracker()

    # Feed 1,000 clean trades to establish high volume feed
    for i in range(1000):
        ev = _make_trade(f"clean_{i}", "FEEDX", 100.0, 1000.0 + i, seq=i + 1)
        tracker.observe(ev)

    stats = tracker.stats["FEEDX"]
    assert stats.total == 1000
    assert stats.score > 0.95

    # Sudden feed degradation: 40 consecutive INVALID events arrive
    for j in range(40):
        ev = _make_trade(f"err_{j}", "FEEDX", 100.0, 2000.0 + j, seq=1001 + j)
        ev.quality_status = QualityStatus.INVALID
        ev.reasons.append(Reason.SCHEMA_VIOLATION.value)
        tracker.observe(ev)

    # In the old cumulative model: 40 / 1040 = 3.8% error rate -> score ~ 0.96 (fails to trip degradation threshold of 0.90)
    # In the new EWMA model: EWMA error rate rapidly rises -> score falls below 0.85 immediately
    updated_stats = tracker.stats["FEEDX"]
    assert updated_stats.score < 0.85, f"Expected score < 0.85, got {updated_stats.score}"
    assert updated_stats.ewma_error_rate > 0.40


# =========================================================================
# 5. Rate-Limit Quarantine Invariant (Spec §26 Principle 3)
# =========================================================================
def test_rate_limited_events_are_quarantined():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(db_path)
    sec = SecurityManager(store=store)
    sec.rate_limiter = TokenBucketRateLimiter(rate=1.0, capacity=2.0)
    pipeline = Pipeline(store=store, security=sec)

    # 1st & 2nd events allowed
    raw1 = RawEvent(source="FEED_BURST", payload={"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1000.0, "price": 150.0, "quantity": 100, "sequence": 1}, receive_timestamp=1000.0, raw_id="r1")
    raw2 = RawEvent(source="FEED_BURST", payload={"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1000.01, "price": 150.1, "quantity": 100, "sequence": 2}, receive_timestamp=1000.01, raw_id="r2")
    ev1 = pipeline.process_one(raw1)
    ev2 = pipeline.process_one(raw2)
    assert ev1.quality_status == QualityStatus.VALID
    assert ev2.quality_status == QualityStatus.VALID

    # 3rd event exceeds rate limit capacity
    raw3 = RawEvent(source="FEED_BURST", payload={"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1000.02, "price": 150.2, "quantity": 100, "sequence": 3}, receive_timestamp=1000.02, raw_id="r3")
    ev3 = pipeline.process_one(raw3)

    # Invariant: Must NOT return None or silently drop. Must be INVALID and quarantined.
    assert ev3 is not None
    assert ev3.quality_status == QualityStatus.INVALID
    pipeline.flush()

    quar = store.query_quarantine()
    assert any("Rate limit exceeded" in q["reasons"] for q in quar)
    store.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


# =========================================================================
# 6. Hybrid Time-and-Count Batch Flusher
# =========================================================================
def test_time_based_flush_for_low_volume_feeds():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = Store(db_path)
    pipeline = Pipeline(store=store, flush_interval_s=0.1)

    # Ingest 3 events (far below BATCH_SIZE=2000)
    for i in range(3):
        raw = RawEvent(
            source="SLOW_FEED",
            payload={"instrument": "MSFT", "event_type": "TRADE", "price": 400.0 + i, "quantity": 50, "sequence": i + 1, "exchange_ts": 1000.0 + i},
            receive_timestamp=1000.0 + i,
            raw_id=f"slow_{i}",
        )
        pipeline.process_one(raw)

    # Wait for the flush interval to elapse
    time.sleep(0.15)
    pipeline._maybe_flush()

    # Verify rows were flushed to SQLite without waiting for buffer to hit 2,000
    rows = store.latest("MSFT", limit=10)
    assert len(rows) == 3

    pipeline.finish()
    store.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


# =========================================================================
# 7. FastPath Capacity Fallback (> 64 Instruments)
# =========================================================================
def test_fastpath_capacity_fallback_above_64_instruments():
    engine = FastQualityEngine()

    # Generate 70 unique instruments (exceeding C static table limit of MAX_INSTRUMENTS=64)
    results = []
    for i in range(70):
        sym = f"SYM_{i:03d}"
        ev = _make_trade(f"ev_{i}", sym, 100.0, 1000.0 + i)
        res = engine.evaluate(ev)
        results.append(res)

    # All 70 should be processed successfully without schema violation errors
    for r in results:
        assert r.quality_status == QualityStatus.VALID
        assert Reason.SCHEMA_VIOLATION.value not in r.reasons


# =========================================================================
# 8. BBO Staleness Guard & TTL Eviction
# =========================================================================
def test_bbo_staleness_guard():
    engine = BBOEngine(quote_ttl_s=1.0)

    # Quote AAPL at t=1000.0
    q = CanonicalEvent(
        event_id="q1",
        instrument_id="AAPL",
        event_type=EventType.QUOTE,
        exchange_timestamp=1000.0,
        receive_timestamp=1000.001,
        processing_timestamp=1000.002,
        source="FEEDA",
        sequence_number=1,
        bid_price=150.0,
        bid_size=100.0,
        ask_price=150.5,
        ask_size=100.0,
        quality_status=QualityStatus.VALID,
    )
    engine.observe(q)

    # Immediately: quote is fresh
    bbo_fresh = engine.current_bbo("AAPL", allow_stale=False, now=1000.5)
    assert bbo_fresh is not None
    assert not bbo_fresh.is_stale

    # After TTL: quote is stale
    bbo_stale = engine.current_bbo("AAPL", allow_stale=False, now=1002.0)
    assert bbo_stale is None

    # Still queryable if allow_stale=True, but marked is_stale
    bbo_allowed = engine.current_bbo("AAPL", allow_stale=True, now=1002.0)
    assert bbo_allowed is not None
    assert bbo_allowed.is_stale is True
