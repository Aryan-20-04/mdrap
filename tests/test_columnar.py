import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models import CanonicalEvent, EventType, QualityStatus
from storage import Store
from columnar import ColumnarStore


@pytest.fixture
def memory_store():
    store = ColumnarStore(db_path=":memory:")
    yield store
    store.close()


def make_trade(event_id: str, symbol: str, price: float, qty: float, ts: float, delta_proc: float = 0.0005) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id=symbol,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.0001,
        processing_timestamp=ts + 0.0001 + delta_proc,
        source="FEEDA",
        sequence_number=int(event_id.replace("t", "").replace("q", "").replace("e", "") or "1"),
        price=price,
        quantity=qty,
        quality_status=QualityStatus.VALID,
    )


def make_quote(event_id: str, symbol: str, bid: float, ask: float, ts: float) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id=symbol,
        event_type=EventType.QUOTE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.0001,
        processing_timestamp=ts + 0.0002,
        source="FEEDB",
        sequence_number=int(event_id.replace("t", "").replace("q", "").replace("e", "") or "1"),
        bid_price=bid,
        bid_size=100.0,
        ask_price=ask,
        ask_size=100.0,
        quality_status=QualityStatus.VALID,
    )


def test_columnar_store_lifecycle(memory_store):
    assert memory_store.count() == 0
    assert memory_store.symbols() == []


def test_columnar_ingest_and_count(memory_store):
    events = [
        make_trade("t1", "AAPL", 150.0, 100.0, 1000.0),
        make_trade("t2", "AAPL", 151.0, 200.0, 1001.0),
        make_quote("q1", "MSFT", 300.0, 301.0, 1000.0),
    ]
    inserted = memory_store.ingest_events(events)
    assert inserted == 3
    assert memory_store.count() == 3
    symbols = memory_store.symbols()
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_columnar_sync_from_sqlite():
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_file = os.path.join(tmp_dir, "test_source.db")
        duck_file = os.path.join(tmp_dir, "test_target.duckdb")

        # 1. Populate SQLite store
        with Store(sqlite_file) as s_store:
            events = [
                make_trade(f"t{i}", "AAPL", 100.0 + i, 50.0, 1000.0 + i)
                for i in range(10)
            ]
            s_store.write_canonical_batch(events)
            s_store.commit()

        # 2. Sync to ColumnarStore
        with ColumnarStore(duck_file) as c_store:
            synced = c_store.sync_from_sqlite(sqlite_file)
            assert synced == 10
            assert c_store.count() == 10
            assert c_store.symbols() == ["AAPL"]


def test_columnar_query_ohlcv(memory_store):
    # Create 5 trades in bucket 1000.0 (interval 5.0)
    # Prices: 150 (open), 155 (high), 145 (low), 152 (close)
    events = [
        make_trade("t1", "AAPL", 150.0, 100.0, 1000.0),
        make_trade("t2", "AAPL", 155.0, 50.0, 1001.0),
        make_trade("t3", "AAPL", 145.0, 50.0, 1002.0),
        make_trade("t4", "AAPL", 152.0, 100.0, 1003.0),
        # Bucket 1005.0
        make_trade("t5", "AAPL", 153.0, 200.0, 1006.0),
    ]
    memory_store.ingest_events(events)

    candles = memory_store.query_ohlcv("AAPL", interval_s=5.0, limit=10)
    assert len(candles) == 2

    # First candle (1000.0)
    c1 = candles[0]
    assert c1["bucket_start"] == 1000.0
    assert c1["open"] == 150.0
    assert c1["high"] == 155.0
    assert c1["low"] == 145.0
    assert c1["close"] == 152.0
    assert c1["volume"] == 300.0
    assert c1["event_count"] == 4

    # Second candle (1005.0)
    c2 = candles[1]
    assert c2["bucket_start"] == 1005.0
    assert c2["open"] == 153.0
    assert c2["close"] == 153.0
    assert c2["volume"] == 200.0
    assert c2["event_count"] == 1


def test_columnar_query_vwap(memory_store):
    # Trades:
    # 100 shares @ 100.0 = 10,000
    # 200 shares @ 103.0 = 20,600
    # Total volume = 300, total notional = 30,600 -> VWAP = 102.0
    events = [
        make_trade("t1", "AAPL", 100.0, 100.0, 1000.0),
        make_trade("t2", "AAPL", 103.0, 200.0, 1001.0),
    ]
    memory_store.ingest_events(events)

    vwap = memory_store.query_vwap("AAPL")
    assert vwap["instrument_id"] == "AAPL"
    assert vwap["trade_count"] == 2
    assert vwap["total_volume"] == 300.0
    assert vwap["total_notional"] == 30600.0
    assert vwap["vwap"] == 102.0
    assert vwap["min_price"] == 100.0
    assert vwap["max_price"] == 103.0


def test_columnar_query_spread_analytics(memory_store):
    quotes = [
        make_quote("q1", "AAPL", 149.5, 150.5, 1000.0),  # spread = 1.0
        make_quote("q2", "AAPL", 149.0, 151.0, 1001.0),  # spread = 2.0
        make_quote("q3", "AAPL", 151.0, 150.0, 1002.0),  # crossed quote (bid > ask)
    ]
    memory_store.ingest_events(quotes)

    res = memory_store.query_spread_analytics("AAPL")
    assert len(res) == 1
    r = res[0]
    assert r["instrument_id"] == "AAPL"
    assert r["quote_count"] == 3
    assert r["crossed_count"] == 1
    assert pytest.approx(r["crossed_pct"], 0.1) == 33.33
    assert r["min_spread"] == -1.0
    assert r["max_spread"] == 2.0


def test_columnar_query_latency_quantiles(memory_store):
    events = [
        make_trade("t1", "AAPL", 100.0, 10.0, 1000.0, delta_proc=0.000050),
        make_trade("t2", "AAPL", 100.0, 10.0, 1001.0, delta_proc=0.000100),
        make_trade("t3", "AAPL", 100.0, 10.0, 1002.0, delta_proc=0.000200),
        make_trade("t4", "AAPL", 100.0, 10.0, 1003.0, delta_proc=0.000500),
    ]
    memory_store.ingest_events(events)

    lat = memory_store.query_latency_quantiles()
    assert lat["total_events"] == 4
    assert lat["p50_us"] > 0
    assert lat["p99_us"] >= lat["p50_us"]


def test_columnar_query_volume_profile(memory_store):
    events = [
        make_trade("t1", "AAPL", 100.0, 500.0, 1000.0),
        make_trade("t2", "AAPL", 110.0, 300.0, 1001.0),
        make_trade("t3", "AAPL", 120.0, 200.0, 1002.0),
    ]
    memory_store.ingest_events(events)

    prof = memory_store.query_volume_profile("AAPL", bins=3)
    assert len(prof) > 0
    total_vol = sum(p["volume"] for p in prof)
    assert total_vol == 1000.0


def test_columnar_export_parquet(memory_store):
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_parquet = os.path.join(tmp_dir, "export_test.parquet")
        events = [
            make_trade("t1", "AAPL", 150.0, 100.0, 1000.0),
            make_trade("t2", "AAPL", 151.0, 200.0, 1001.0),
        ]
        memory_store.ingest_events(events)

        exported_path = memory_store.export_parquet(out_parquet, instrument_id="AAPL")
        assert os.path.exists(exported_path)
        assert os.path.getsize(exported_path) > 0

        # Verify reading back via duckdb
        check_count = memory_store.con.execute(f"SELECT count(*) FROM read_parquet('{exported_path}')").fetchone()[0]
        assert check_count == 2


def test_columnar_sql(memory_store):
    events = [
        make_trade("t1", "AAPL", 150.0, 100.0, 1000.0),
        make_trade("t2", "MSFT", 300.0, 50.0, 1001.0),
    ]
    memory_store.ingest_events(events)

    rows = memory_store.sql("SELECT instrument_id, price FROM canonical_ticks ORDER BY price")
    assert len(rows) == 2
    assert rows[0]["instrument_id"] == "AAPL"
    assert rows[0]["price"] == 150.0
    assert rows[1]["instrument_id"] == "MSFT"
    assert rows[1]["price"] == 300.0


def test_columnar_micro_benchmark():
    with tempfile.TemporaryDirectory() as tmp_dir:
        sqlite_file = os.path.join(tmp_dir, "bench_source.db")
        duck_file = os.path.join(tmp_dir, "bench_target.duckdb")

        with Store(sqlite_file) as s_store:
            events = [
                make_trade(f"t{i}", "AAPL", 100.0 + (i % 10), 10.0, 1000.0 + i)
                for i in range(100)
            ]
            s_store.write_canonical_batch(events)
            s_store.commit()

        with ColumnarStore(duck_file) as c_store:
            res = c_store.benchmark_sqlite_vs_duckdb(sqlite_file)
            assert res["total_ticks"] == 100
            assert "sqlite" in res
            assert "duckdb" in res
            assert "speedup" in res
            assert res["duckdb"]["ohlcv_ms"] >= 0
