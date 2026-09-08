"""
MDRAP Columnar Time-Series Storage & Ultra-Fast Analytical Engine (DuckDB & Parquet).

Implements Phase 3 (Spec §14, §26):
- High-throughput embedded DuckDB columnar storage.
- Vectorized SIMD OHLCV resampling, VWAP, spreads, and latency quantiles.
- Zero-copy SQLite synchronization via DuckDB's native SQLite scanner.
- Compressed Apache Parquet exporting with zstd/snappy compression.
- Scale-up comparative micro-benchmarking (SQLite row scan vs DuckDB columnar scan).
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import duckdb
except ImportError:
    duckdb = None

from models import CanonicalEvent, EventType, QualityStatus


COLUMNAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_ticks (
    event_id VARCHAR PRIMARY KEY,
    instrument_id VARCHAR,
    event_type VARCHAR,
    exchange_timestamp DOUBLE,
    receive_timestamp DOUBLE,
    processing_timestamp DOUBLE,
    source VARCHAR,
    sequence_number BIGINT,
    price DOUBLE,
    quantity DOUBLE,
    bid_price DOUBLE,
    bid_size DOUBLE,
    ask_price DOUBLE,
    ask_size DOUBLE,
    quality_status VARCHAR,
    reasons VARCHAR,
    raw_id VARCHAR
);
"""


class ColumnarStore:
    """
    DuckDB-powered columnar time-series storage and analytical query engine.
    Processes billions of market events with SIMD vectorization and compressed Parquet files.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        read_only: bool = False,
        threads: int = 4,
        memory_limit: str = "2GB",
    ):
        if duckdb is None:
            raise RuntimeError("duckdb is not installed. Install with 'pip install duckdb'.")

        self.db_path = db_path
        self.read_only = read_only
        self._lock = threading.RLock()

        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        try:
            self.con = duckdb.connect(database=db_path, read_only=read_only)
        except Exception as exc:
            # If write lock is held by another process (e.g. streaming daemon), gracefully fallback to read-only
            if not read_only and "lock" in str(exc).lower():
                self.con = duckdb.connect(database=db_path, read_only=True)
                self.read_only = True
            else:
                raise exc
        
        # Configure resource limits
        self.con.execute(f"PRAGMA threads={max(1, threads)}")
        if memory_limit:
            self.con.execute(f"PRAGMA memory_limit='{memory_limit}'")

        if not self.read_only:
            self.con.execute(COLUMNAR_SCHEMA)

    def __enter__(self) -> ColumnarStore:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close DuckDB connection safely."""
        with self._lock:
            if hasattr(self, "con") and self.con:
                try:
                    self.con.close()
                except Exception:
                    pass
                self.con = None

    # -----------------------------------------------------------------------
    # Ingestion & Synchronization
    # -----------------------------------------------------------------------

    def ingest_events(self, events: Iterable[CanonicalEvent]) -> int:
        """
        Batch-insert CanonicalEvent objects into columnar storage.
        """
        rows = []
        for ev in events:
            reasons_str = json.dumps(ev.reasons) if ev.reasons else ""
            rows.append((
                ev.event_id,
                ev.instrument_id,
                ev.event_type.value if hasattr(ev.event_type, "value") else str(ev.event_type),
                ev.exchange_timestamp,
                ev.receive_timestamp,
                ev.processing_timestamp,
                ev.source,
                ev.sequence_number,
                ev.price,
                ev.quantity,
                ev.bid_price,
                ev.bid_size,
                ev.ask_price,
                ev.ask_size,
                ev.quality_status.value if hasattr(ev.quality_status, "value") else str(ev.quality_status),
                reasons_str,
                ev.raw_id or "",
            ))

        if not rows:
            return 0

        with self._lock:
            self.con.executemany("""
                INSERT OR IGNORE INTO canonical_ticks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, rows)
            return len(rows)

    def max_timestamp(self) -> float:
        """Return highest exchange timestamp stored in columnar store."""
        with self._lock:
            res = self.con.execute("SELECT MAX(exchange_timestamp) FROM canonical_ticks").fetchone()
            return float(res[0]) if res and res[0] is not None else 0.0

    def sync_from_sqlite(self, sqlite_path: str = "data/mdrap.db", incremental: bool = True) -> int:
        """
        Directly attach SQLite database and bulk copy canonical events into DuckDB
        using zero-copy vectorized scanning.
        If incremental=True and store has existing ticks, only synchronizes new delta events (O(Delta)).
        """
        if not os.path.exists(sqlite_path):
            raise FileNotFoundError(f"SQLite database not found at {sqlite_path}")

        with self._lock:
            abs_path = os.path.abspath(sqlite_path).replace("\\", "/")
            max_ts = self.max_timestamp() if incremental else 0.0

            # Attach SQLite database
            self.con.execute(f"ATTACH '{abs_path}' AS sqldb (TYPE SQLITE);")
            try:
                before = self.count()
                if incremental and max_ts > 0.0:
                    self.con.execute("""
                        INSERT OR IGNORE INTO canonical_ticks
                        SELECT 
                            event_id, instrument_id, event_type,
                            exchange_timestamp, receive_timestamp, processing_timestamp,
                            source, sequence_number, price, quantity,
                            bid_price, bid_size, ask_price, ask_size,
                            quality_status, reasons, raw_id
                        FROM sqldb.canonical_events
                        WHERE exchange_timestamp >= ?;
                    """, [max_ts])
                else:
                    self.con.execute("""
                        INSERT OR IGNORE INTO canonical_ticks
                        SELECT 
                            event_id, instrument_id, event_type,
                            exchange_timestamp, receive_timestamp, processing_timestamp,
                            source, sequence_number, price, quantity,
                            bid_price, bid_size, ask_price, ask_size,
                            quality_status, reasons, raw_id
                        FROM sqldb.canonical_events;
                    """)
                after = self.count()
                synced = after - before
            finally:
                self.con.execute("DETACH sqldb;")

            return synced

    def freshness(self, sqlite_path: str = "data/mdrap.db") -> Dict[str, Any]:
        """
        Compare DuckDB tick counts and timestamps against SQLite to evaluate data freshness.
        """
        duck_count = self.count()
        duck_max_ts = self.max_timestamp()

        sql_count = 0
        sql_max_ts = 0.0
        if os.path.exists(sqlite_path):
            import sqlite3
            try:
                con = sqlite3.connect(sqlite_path)
                cur = con.cursor()
                r = cur.execute("SELECT count(*), max(exchange_timestamp) FROM canonical_events").fetchone()
                if r:
                    sql_count = int(r[0] or 0)
                    sql_max_ts = float(r[1] or 0.0)
                con.close()
            except Exception:
                pass

        lag_ticks = max(0, sql_count - duck_count)
        return {
            "duckdb_ticks": duck_count,
            "sqlite_ticks": sql_count,
            "lag_ticks": lag_ticks,
            "duckdb_max_ts": duck_max_ts,
            "sqlite_max_ts": sql_max_ts,
            "is_fresh": (lag_ticks == 0),
        }

    def count(self) -> int:
        """Return total tick count in columnar store."""
        with self._lock:
            res = self.con.execute("SELECT count(*) FROM canonical_ticks").fetchone()
            return res[0] if res else 0

    def symbols(self) -> List[str]:
        """Return sorted list of all unique instruments present in ticks."""
        with self._lock:
            res = self.con.execute("SELECT DISTINCT instrument_id FROM canonical_ticks ORDER BY 1").fetchall()
            return [r[0] for r in res if r[0]]

    # -----------------------------------------------------------------------
    # Analytical Queries (Vectorized SIMD)
    # -----------------------------------------------------------------------

    def query_ohlcv(
        self,
        symbol: str,
        interval_s: float = 5.0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Generate OHLCV candles on the fly using vectorized time bucketing and
        DuckDB's SIMD arg_min/arg_max aggregations.
        """
        sym = symbol.upper()
        sym_clean = sym.replace("-", "/")

        query = """
            SELECT
                instrument_id,
                FLOOR(exchange_timestamp / ?) * ? AS bucket_start,
                ? AS interval_s,
                arg_min(price, exchange_timestamp) AS open,
                max(price) AS high,
                min(price) AS low,
                arg_max(price, exchange_timestamp) AS close,
                sum(quantity) AS volume,
                count(*) AS event_count
            FROM canonical_ticks
            WHERE (instrument_id = ? OR instrument_id = ?)
              AND event_type = 'TRADE'
              AND price IS NOT NULL
            GROUP BY 1, 2
            ORDER BY bucket_start DESC
            LIMIT ?
        """
        with self._lock:
            res = self.con.execute(query, [interval_s, interval_s, interval_s, sym, sym_clean, limit]).fetchall()

        candles = []
        for r in reversed(res):
            candles.append({
                "instrument_id": r[0],
                "bucket_start": float(r[1]),
                "interval_s": float(r[2]),
                "open": round(float(r[3]), 4),
                "high": round(float(r[4]), 4),
                "low": round(float(r[5]), 4),
                "close": round(float(r[6]), 4),
                "volume": round(float(r[7] or 0.0), 2),
                "event_count": int(r[8]),
            })
        return candles

    def query_vwap(
        self,
        symbol: str,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Compute exact institutional VWAP = sum(price * qty) / sum(qty) in vectorized SIMD.
        """
        sym = symbol.upper()
        sym_clean = sym.replace("-", "/")

        where_clauses = [
            "(instrument_id = ? OR instrument_id = ?)",
            "event_type = 'TRADE'",
            "price IS NOT NULL",
            "quantity > 0"
        ]
        params = [sym, sym_clean]

        if start_ts is not None:
            where_clauses.append("exchange_timestamp >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where_clauses.append("exchange_timestamp <= ?")
            params.append(end_ts)

        where_sql = " AND ".join(where_clauses)
        query = f"""
            SELECT
                count(*) as trade_count,
                sum(price * quantity) / sum(quantity) as vwap,
                sum(price * quantity) as total_notional,
                sum(quantity) as total_volume,
                min(price) as min_price,
                max(price) as max_price
            FROM canonical_ticks
            WHERE {where_sql}
        """
        with self._lock:
            r = self.con.execute(query, params).fetchone()

        if not r or r[0] == 0:
            return {
                "instrument_id": sym,
                "trade_count": 0,
                "vwap": 0.0,
                "total_notional": 0.0,
                "total_volume": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
            }

        return {
            "instrument_id": sym,
            "trade_count": int(r[0]),
            "vwap": round(float(r[1] or 0.0), 4),
            "total_notional": round(float(r[2] or 0.0), 2),
            "total_volume": round(float(r[3] or 0.0), 4),
            "min_price": round(float(r[4] or 0.0), 4),
            "max_price": round(float(r[5] or 0.0), 4),
        }

    def query_spread_analytics(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Compute bid-ask spread telemetry, crossed-market anomalies, and venue spread metrics.
        """
        params = []
        where_extra = ""
        if symbol and symbol.upper() not in ("ALL", "*"):
            sym = symbol.upper()
            sym_clean = sym.replace("-", "/")
            where_extra = "AND (instrument_id = ? OR instrument_id = ?)"
            params.extend([sym, sym_clean])

        query = f"""
            SELECT
                instrument_id,
                count(*) as quote_count,
                avg(ask_price - bid_price) as mean_spread,
                min(ask_price - bid_price) as min_spread,
                max(ask_price - bid_price) as max_spread,
                sum(CASE WHEN bid_price > ask_price THEN 1 ELSE 0 END) as crossed_count,
                (sum(CASE WHEN bid_price > ask_price THEN 1.0 ELSE 0.0 END) / count(*)) * 100.0 as crossed_pct
            FROM canonical_ticks
            WHERE event_type = 'QUOTE'
              AND bid_price IS NOT NULL
              AND ask_price IS NOT NULL
              {where_extra}
            GROUP BY instrument_id
            ORDER BY quote_count DESC
        """
        with self._lock:
            res = self.con.execute(query, params).fetchall()

        out = []
        for r in res:
            out.append({
                "instrument_id": r[0],
                "quote_count": int(r[1]),
                "mean_spread": round(float(r[2] or 0.0), 4),
                "min_spread": round(float(r[3] or 0.0), 4),
                "max_spread": round(float(r[4] or 0.0), 4),
                "crossed_count": int(r[5] or 0),
                "crossed_pct": round(float(r[6] or 0.0), 2),
            })
        return out

    def query_latency_quantiles(self) -> Dict[str, Any]:
        """
        Compute processing engine latency quantiles in microseconds (p50, p90, p95, p99, p99.9)
        across the entire dataset in a single vectorized pass.
        """
        query = """
            SELECT
                quantile_cont((processing_timestamp - receive_timestamp) * 1e6, 0.50) as p50_us,
                quantile_cont((processing_timestamp - receive_timestamp) * 1e6, 0.90) as p90_us,
                quantile_cont((processing_timestamp - receive_timestamp) * 1e6, 0.95) as p95_us,
                quantile_cont((processing_timestamp - receive_timestamp) * 1e6, 0.99) as p99_us,
                quantile_cont((processing_timestamp - receive_timestamp) * 1e6, 0.999) as p999_us,
                avg((processing_timestamp - receive_timestamp) * 1e6) as mean_us,
                count(*) as total_events
            FROM canonical_ticks
            WHERE processing_timestamp >= receive_timestamp
        """
        with self._lock:
            r = self.con.execute(query).fetchone()

        if not r or r[6] == 0:
            return {
                "total_events": 0,
                "mean_us": 0.0,
                "p50_us": 0.0,
                "p90_us": 0.0,
                "p95_us": 0.0,
                "p99_us": 0.0,
                "p999_us": 0.0,
            }

        return {
            "total_events": int(r[6]),
            "mean_us": round(float(r[5] or 0.0), 2),
            "p50_us": round(float(r[0] or 0.0), 2),
            "p90_us": round(float(r[1] or 0.0), 2),
            "p95_us": round(float(r[2] or 0.0), 2),
            "p99_us": round(float(r[3] or 0.0), 2),
            "p999_us": round(float(r[4] or 0.0), 2),
        }

    def query_volume_profile(self, symbol: str, bins: int = 15) -> List[Dict[str, Any]]:
        """
        Compute volume distribution across discrete price rungs for technical profile analysis.
        """
        sym = symbol.upper()
        sym_clean = sym.replace("-", "/")

        query_bounds = """
            SELECT min(price), max(price), sum(quantity)
            FROM canonical_ticks
            WHERE (instrument_id = ? OR instrument_id = ?)
              AND event_type = 'TRADE' AND price IS NOT NULL AND quantity > 0
        """
        with self._lock:
            b_res = self.con.execute(query_bounds, [sym, sym_clean]).fetchone()

        if not b_res or b_res[0] is None or b_res[1] is None or abs(float(b_res[1]) - float(b_res[0])) < 1e-6:
            return []

        min_p, max_p, total_vol = float(b_res[0]), float(b_res[1]), float(b_res[2] or 1.0)
        bin_width = (max_p - min_p) / max(1, bins)
        if bin_width <= 0:
            return []

        query = """
            SELECT
                FLOOR((price - ?) / ?) as bin_idx,
                sum(quantity) as bin_volume,
                count(*) as bin_trades
            FROM canonical_ticks
            WHERE (instrument_id = ? OR instrument_id = ?)
              AND event_type = 'TRADE' AND price IS NOT NULL AND quantity > 0
            GROUP BY 1
            ORDER BY 1
        """
        with self._lock:
            rows = self.con.execute(query, [min_p, bin_width, sym, sym_clean]).fetchall()

        profile = []
        for r in rows:
            idx = int(r[0])
            b_low = min_p + (idx * bin_width)
            b_high = b_low + bin_width
            vol = float(r[1] or 0.0)
            pct = (vol / total_vol) * 100.0 if total_vol > 0 else 0.0
            profile.append({
                "bin_low": round(b_low, 2),
                "bin_high": round(b_high, 2),
                "volume": round(vol, 2),
                "trades": int(r[2]),
                "pct": round(pct, 1),
            })
        return profile

    # -----------------------------------------------------------------------
    # Parquet Export
    # -----------------------------------------------------------------------

    def export_parquet(
        self,
        output_path: str,
        instrument_id: Optional[str] = None,
        compression: str = "zstd",
    ) -> str:
        """
        Export ticks directly to an Apache Parquet file using columnar compression.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        abs_out = os.path.abspath(output_path).replace("\\", "/")

        where_clause = ""
        if instrument_id and instrument_id.upper() not in ("ALL", "*"):
            sym = instrument_id.upper()
            sym_clean = sym.replace("-", "/")
            where_clause = f"WHERE (instrument_id = '{sym}' OR instrument_id = '{sym_clean}')"

        query = f"""
            COPY (
                SELECT * FROM canonical_ticks {where_clause}
                ORDER BY exchange_timestamp
            ) TO '{abs_out}' (FORMAT PARQUET, COMPRESSION '{compression}');
        """
        with self._lock:
            self.con.execute(query)

        return abs_out

    # -----------------------------------------------------------------------
    # Raw SQL Execution
    # -----------------------------------------------------------------------

    def sql(self, query: str, params: Optional[list | tuple | dict] = None) -> List[Dict[str, Any]]:
        """Execute arbitrary SQL query returning a list of dictionaries."""
        with self._lock:
            rel = self.con.execute(query, params or [])
            cols = [desc[0] for desc in rel.description]
            rows = rel.fetchall()
            return [dict(zip(cols, r)) for r in rows]

    # -----------------------------------------------------------------------
    # Performance Micro-Benchmark: SQLite vs DuckDB
    # -----------------------------------------------------------------------

    def benchmark_sqlite_vs_duckdb(
        self,
        sqlite_path: str = "data/mdrap.db",
        num_events: int = 50_000,
    ) -> Dict[str, Any]:
        """
        Controlled micro-benchmark comparing SQLite row scan vs DuckDB vectorized columnar scan.
        """
        import sqlite3

        if not os.path.exists(sqlite_path):
            raise FileNotFoundError(f"SQLite database {sqlite_path} does not exist for benchmark.")

        # 1. Connect SQLite directly
        sql_con = sqlite3.connect(sqlite_path)
        sql_cur = sql_con.cursor()

        # Query 1: OHLCV aggregation in SQLite (Group By with math)
        t0 = time.perf_counter()
        sql_cur.execute("""
            SELECT 
                instrument_id,
                CAST(exchange_timestamp / 5.0 AS INT) * 5.0 AS b_start,
                min(price) as low,
                max(price) as high,
                sum(quantity) as vol,
                count(*) as cnt
            FROM canonical_events
            WHERE event_type = 'TRADE' AND price IS NOT NULL
            GROUP BY instrument_id, b_start
        """)
        sql_ohlcv_res = sql_cur.fetchall()
        t_sql_ohlcv = (time.perf_counter() - t0) * 1000.0

        # Query 2: VWAP in SQLite (sum(P*Q)/sum(Q))
        t0 = time.perf_counter()
        sql_cur.execute("""
            SELECT instrument_id, sum(price * quantity) / sum(quantity), sum(quantity), count(*)
            FROM canonical_events
            WHERE event_type = 'TRADE' AND price IS NOT NULL AND quantity > 0
            GROUP BY instrument_id
        """)
        sql_vwap_res = sql_cur.fetchall()
        t_sql_vwap = (time.perf_counter() - t0) * 1000.0

        sql_con.close()

        # Sync into DuckDB
        self.sync_from_sqlite(sqlite_path)

        # Query 1 in DuckDB: OHLCV
        t0 = time.perf_counter()
        duck_ohlcv_res = self.con.execute("""
            SELECT 
                instrument_id,
                FLOOR(exchange_timestamp / 5.0) * 5.0 AS b_start,
                arg_min(price, exchange_timestamp) as open,
                max(price) as high,
                min(price) as low,
                arg_max(price, exchange_timestamp) as close,
                sum(quantity) as vol,
                count(*) as cnt
            FROM canonical_ticks
            WHERE event_type = 'TRADE' AND price IS NOT NULL
            GROUP BY 1, 2
        """).fetchall()
        t_duck_ohlcv = (time.perf_counter() - t0) * 1000.0

        # Query 2 in DuckDB: VWAP
        t0 = time.perf_counter()
        duck_vwap_res = self.con.execute("""
            SELECT instrument_id, sum(price * quantity) / sum(quantity), sum(quantity), count(*)
            FROM canonical_ticks
            WHERE event_type = 'TRADE' AND price IS NOT NULL AND quantity > 0
            GROUP BY 1
        """).fetchall()
        t_duck_vwap = (time.perf_counter() - t0) * 1000.0

        total_ticks = self.count()
        ohlcv_speedup = t_sql_ohlcv / max(0.001, t_duck_ohlcv)
        vwap_speedup = t_sql_vwap / max(0.001, t_duck_vwap)

        return {
            "total_ticks": total_ticks,
            "sqlite": {
                "ohlcv_ms": round(t_sql_ohlcv, 2),
                "vwap_ms": round(t_sql_vwap, 2),
                "rows_scanned": len(sql_ohlcv_res),
            },
            "duckdb": {
                "ohlcv_ms": round(t_duck_ohlcv, 2),
                "vwap_ms": round(t_duck_vwap, 2),
                "rows_scanned": len(duck_ohlcv_res),
            },
            "speedup": {
                "ohlcv": round(ohlcv_speedup, 1),
                "vwap": round(vwap_speedup, 1),
                "overall": round((t_sql_ohlcv + t_sql_vwap) / max(0.001, t_duck_ohlcv + t_duck_vwap), 1),
            }
        }
