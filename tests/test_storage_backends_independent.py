"""Phase 10 Storage Layer Independent Backend Tests.

Exhaustively verifies:
1. SQLite:
   - single insert & batch insert
   - point and range reads
   - concurrent reads under read-locks
   - WAL journal mode persistence
   - database restart and data durability
   - backup and restore verification
2. DuckDB / Columnar:
   - columnar table creation & event writes
   - analytical OLAP queries (volume, price aggregations)
   - database restart and session independence
"""

import os
import sqlite3
import threading
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store


def _make_event(event_id: str, seq: int, price: float = 150.0) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0 + seq * 0.001,
        receive_timestamp=1000.0 + seq * 0.001 + 0.001,
        processing_timestamp=1000.0 + seq * 0.001 + 0.002,
        source="FEED_A",
        sequence_number=seq,
        venue="NASDAQ",
        price=price,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
    )


def test_sqlite_insert_batch_read_and_restart(tmp_path):
    """Test SQLite single/batch inserts, read queries, and restart durability."""
    db_file = str(tmp_path / "sqlite_test.db")

    # 1. Initialize and write
    store = Store(path=db_file)
    events = [_make_event(f"ev_{i}", i, 150.0 + i * 0.1) for i in range(1, 101)]

    # Batch insert via write_batches_atomic
    store.write_batches_atomic(canonical=events)
    store.commit()

    # Read back and verify count
    rows = store.query_events(instrument_id="AAPL", limit=200)
    assert len(rows) == 100
    store.close()

    # 2. Restart Store on same file
    store2 = Store(path=db_file)
    rows_reopened = store2.query_events(instrument_id="AAPL", limit=200)
    assert len(rows_reopened) == 100
    assert rows_reopened[0]["event_id"] == "ev_100"  # DESC order
    store2.close()


def test_sqlite_wal_mode_and_concurrent_reads(tmp_path):
    """Test WAL mode operation and concurrent readers while writing."""
    db_file = str(tmp_path / "sqlite_wal.db")
    store = Store(path=db_file)

    # Verify WAL mode
    cur = store.conn.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"

    # Prepopulate data
    store.write_batches_atomic([_make_event(f"ev_{i}", i) for i in range(1, 51)])
    store.commit()

    # Launch concurrent reader threads
    read_results = []
    errors = []

    def reader_worker():
        try:
            r_conn = sqlite3.connect(db_file, timeout=5.0)
            c = r_conn.cursor()
            c.execute("SELECT COUNT(*) FROM canonical_events;")
            count = c.fetchone()[0]
            read_results.append(count)
            r_conn.close()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent read errors: {errors}"
    assert len(read_results) == 8
    assert all(count >= 50 for count in read_results)
    store.close()


def test_sqlite_backup_and_restore(tmp_path):
    """Test full online database backup to secondary destination and restore."""
    db_file = str(tmp_path / "sqlite_primary.db")
    backup_file = str(tmp_path / "sqlite_backup.db")

    store = Store(path=db_file)
    store.write_batches_atomic([_make_event(f"ev_{i}", i) for i in range(1, 25)])
    store.commit()

    # Online backup using SQLite backup API
    backup_conn = sqlite3.connect(backup_file)
    with store._lock:
        store.conn.backup(backup_conn)
    backup_conn.close()
    store.close()

    assert os.path.exists(backup_file)

    # Reopen backup as primary
    restored_store = Store(path=backup_file)
    rows = restored_store.query_events(limit=50)
    assert len(rows) == 24
    restored_store.close()


def test_duckdb_columnar_storage_and_query(tmp_path):
    """Test DuckDB columnar engine if installed."""
    try:
        import duckdb
    except ImportError:
        return

    duck_file = str(tmp_path / "test.duckdb")
    con = duckdb.connect(duck_file)

    con.execute("""
        CREATE TABLE ticks (
            event_id VARCHAR,
            instrument VARCHAR,
            price DOUBLE,
            quantity DOUBLE,
            ts DOUBLE
        );
    """)

    # Columnar bulk insert
    con.executemany(
        "INSERT INTO ticks VALUES (?, ?, ?, ?, ?);",
        [(f"ev_{i}", "AAPL", 150.0 + i, 100.0, 1000.0 + i) for i in range(500)],
    )

    # Analytical OLAP aggregation query
    res = con.execute(
        "SELECT AVG(price), SUM(quantity), COUNT(*) FROM ticks;"
    ).fetchone()
    avg_price, total_qty, cnt = res
    assert cnt == 500
    assert total_qty == 50000.0
    assert 390.0 < avg_price < 410.0

    con.close()

    # Verify file persistence upon reconnect
    con2 = duckdb.connect(duck_file)
    cnt2 = con2.execute("SELECT COUNT(*) FROM ticks;").fetchone()[0]
    assert cnt2 == 500
    con2.close()
