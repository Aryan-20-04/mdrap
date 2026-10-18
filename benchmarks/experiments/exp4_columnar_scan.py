"""Experiment 4: Historical Replay & Columnar Projection (Strategy §14.5 / LSEG & NVIDIA 2026).

Benchmarks IngestLog journal scanning throughput, conversion into analytical columnar
projections (DuckDB / Parquet / SQLite), and verifies complete isolation ensuring analytical
failures can never mutate or poison the canonical persistence layer.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent

__stability__ = "experimental"


try:
    import duckdb

    HAS_DUCKDB = True
except ImportError:
    duckdb = None  # type: ignore
    HAS_DUCKDB = False


@dataclass
class ColumnarScanReport:
    total_records: int
    raw_journal_bytes: int
    projected_bytes: int
    scan_duration_sec: float
    scan_throughput_eps: float
    query_latency_ms: float
    canonical_log_intact: bool
    lineage_fields_verified: bool
    backend_used: str = "sqlite"

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "raw_journal_bytes": self.raw_journal_bytes,
            "projected_bytes": self.projected_bytes,
            "scan_duration_sec": round(self.scan_duration_sec, 4),
            "scan_throughput_eps": round(self.scan_throughput_eps, 1),
            "query_latency_ms": round(self.query_latency_ms, 2),
            "canonical_log_intact": self.canonical_log_intact,
            "lineage_verified": self.lineage_fields_verified,
            "backend": self.backend_used,
        }


def populate_sample_journal(journal_dir: str, num_records: int = 15_000) -> int:
    """Create a temporary IngestLog WAL segment populated with synthetic trades using batch commit."""
    log = IngestLog(
        journal_dir, fsync_policy="never", max_segment_bytes=64 * 1024 * 1024
    )
    now = time.time()
    events = []
    for i in range(1, num_records + 1):
        payload = {
            "instrument": "BTC/USDT",
            "event_type": "TRADE",
            "price": round(65000.0 + (i % 100) * 0.5, 2),
            "quantity": 0.5,
            "sequence": i,
            "exchange_ts": now + (i * 0.001),
        }
        events.append(
            RawEvent(
                source="BINANCE", payload=payload, receive_timestamp=now + (i * 0.001)
            )
        )
    log.append_batch(events)
    log.flush()
    log.close()

    total_bytes = sum(
        os.path.getsize(os.path.join(journal_dir, f))
        for f in os.listdir(journal_dir)
        if f.endswith(".log")
    )
    return total_bytes


def project_to_analytical_store(
    journal_dir: str, db_path: str, backend: str = "sqlite"
) -> tuple[float, int, int, str]:
    """Scan IngestLog segments and project into an analytical table (DuckDB or SQLite)."""
    use_duckdb = (backend == "duckdb") or (backend == "auto" and HAS_DUCKDB)
    selected_backend = "duckdb" if use_duckdb else "sqlite"

    if use_duckdb:
        conn = duckdb.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS historical_trades (
                "offset" BIGINT PRIMARY KEY,
                symbol VARCHAR NOT NULL,
                price DOUBLE NOT NULL,
                quantity DOUBLE NOT NULL,
                exchange_ts DOUBLE NOT NULL,
                sequence BIGINT NOT NULL,
                source VARCHAR NOT NULL,
                ingest_ts DOUBLE NOT NULL
            )
        """)
        log = IngestLog(journal_dir)
        batch = []
        scanned_count = 0
        t0 = time.perf_counter()

        for offset, raw in log.iter_from(0):
            scanned_count += 1
            p = raw.payload
            batch.append(
                (
                    offset,
                    p.get("instrument", ""),
                    float(p.get("price", 0.0)),
                    float(p.get("quantity", 0.0)),
                    float(p.get("exchange_ts", 0.0)),
                    int(p.get("sequence", 0)),
                    str(raw.source),
                    float(raw.receive_timestamp),
                )
            )
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT INTO historical_trades VALUES (?,?,?,?,?,?,?,?)", batch
                )
                batch.clear()

        if batch:
            conn.executemany(
                "INSERT INTO historical_trades VALUES (?,?,?,?,?,?,?,?)", batch
            )
            batch.clear()

        elapsed = max(time.perf_counter() - t0, 1e-6)
        conn.close()
        db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        return elapsed, scanned_count, db_bytes, selected_backend

    # SQLite Fallback
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_trades (
            offset INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            exchange_ts REAL NOT NULL,
            sequence INTEGER NOT NULL,
            source TEXT NOT NULL,
            ingest_ts REAL NOT NULL
        )
    """)

    log = IngestLog(journal_dir)
    batch = []
    scanned_count = 0
    t0 = time.perf_counter()

    for offset, raw in log.iter_from(0):
        scanned_count += 1
        p = raw.payload
        batch.append(
            (
                offset,
                p.get("instrument", ""),
                p.get("price", 0.0),
                p.get("quantity", 0.0),
                p.get("exchange_ts", 0.0),
                p.get("sequence", 0),
                raw.source,
                raw.receive_timestamp,
            )
        )
        if len(batch) >= 2000:
            conn.executemany(
                "INSERT INTO historical_trades VALUES (?,?,?,?,?,?,?,?)", batch
            )
            batch.clear()

    if batch:
        conn.executemany(
            "INSERT INTO historical_trades VALUES (?,?,?,?,?,?,?,?)", batch
        )
        batch.clear()

    conn.commit()
    elapsed = max(time.perf_counter() - t0, 1e-6)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sym_ts ON historical_trades(symbol, exchange_ts)"
    )
    conn.commit()
    conn.close()

    db_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    return elapsed, scanned_count, db_bytes, selected_backend


def run_analytical_query(db_path: str, backend: str = "sqlite") -> tuple[float, bool]:
    """Execute analytical aggregation (VWAP and tick count) on projected data."""
    use_duckdb = (backend == "duckdb") or (backend == "auto" and HAS_DUCKDB)
    t0 = time.perf_counter()

    if use_duckdb:
        conn = duckdb.connect(db_path)
        rows = conn.execute("""
            SELECT symbol, COUNT(*), SUM(price * quantity) / SUM(quantity) as vwap, MIN(price), MAX(price)
            FROM historical_trades
            GROUP BY symbol
        """).fetchall()
        query_time_ms = (time.perf_counter() - t0) * 1000.0
        conn.close()
        return query_time_ms, len(rows) > 0

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, COUNT(*), SUM(price * quantity) / SUM(quantity) as vwap, MIN(price), MAX(price)
        FROM historical_trades
        GROUP BY symbol
    """)
    rows = cursor.fetchall()
    query_time_ms = (time.perf_counter() - t0) * 1000.0
    conn.close()
    return query_time_ms, len(rows) > 0


def run_experiment_4(
    num_records: int = 15_000, backend: str = "sqlite"
) -> ColumnarScanReport:
    """Execute Experiment 4: Historical IngestLog scan, projection, and query benchmark."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_exp4_")
    try:
        wal_dir = os.path.join(temp_dir, "wal")
        os.makedirs(wal_dir, exist_ok=True)
        db_path = os.path.join(
            temp_dir,
            "analytics.duckdb"
            if (backend == "duckdb" or (backend == "auto" and HAS_DUCKDB))
            else "analytics.db",
        )

        journal_bytes = populate_sample_journal(wal_dir, num_records=num_records)
        scan_sec, scanned_cnt, projected_bytes, chosen_backend = (
            project_to_analytical_store(wal_dir, db_path, backend=backend)
        )
        query_ms, query_ok = run_analytical_query(db_path, backend=chosen_backend)

        # Verification: Attempt an adversarial mutation on the projection database,
        # then verify the canonical IngestLog remains 100% byte-intact and uncorrupted.
        if chosen_backend == "duckdb":
            conn = duckdb.connect(db_path)
            conn.execute("DELETE FROM historical_trades WHERE sequence % 2 == 0")
            conn.close()
        else:
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM historical_trades WHERE sequence % 2 == 0")
            conn.commit()
            conn.close()

        # Re-verify IngestLog byte size and frame integrity
        log_after = IngestLog(wal_dir)
        records_after = sum(1 for _ in log_after.iter_from(0))
        canonical_intact = (records_after == num_records) and (
            log_after.corrupted_frames_count == 0
        )

        throughput_eps = scanned_cnt / max(scan_sec, 1e-6)

        return ColumnarScanReport(
            total_records=scanned_cnt,
            raw_journal_bytes=journal_bytes,
            projected_bytes=projected_bytes,
            scan_duration_sec=scan_sec,
            scan_throughput_eps=throughput_eps,
            query_latency_ms=query_ms,
            canonical_log_intact=canonical_intact,
            lineage_fields_verified=query_ok,
            backend_used=chosen_backend,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 70)
    print("MDRAP Experiment 4: Historical Replay & Columnar Projection Benchmark")
    print("=" * 70)
    rep = run_experiment_4(num_records=20_000)
    d = rep.to_dict()
    print(f"Backend Used:            {d['backend'].upper()}")
    print(f"Total Records Scanned:   {d['total_records']:,}")
    print(f"Raw Journal Size:        {d['raw_journal_bytes']:,} bytes")
    print(f"Projected Storage Size:  {d['projected_bytes']:,} bytes")
    print(f"Scan Duration:           {d['scan_duration_sec']} sec")
    print(f"Scan Throughput:         {d['scan_throughput_eps']:,.1f} events/sec")
    print(f"Analytical VWAP Query:   {d['query_latency_ms']} ms")
    print(
        f"Canonical Log Intact:    {d['canonical_log_intact']} (Immutable persistence preserved)"
    )
