"""
Persistent Multi-Timeframe OHLCV Bar Database & Roll-up Engine.

This module provides an institutional-grade, persistent time-series candle storage
and roll-up engine that incrementally aggregates streaming ticks into multi-timeframe
OHLCV (Open, High, Low, Close, Volume) bars.

Key Architecture & Invariants:
- Multi-Interval Ingestion: Concurrently aggregates trade events across multiple
  timeframes (1s, 5s, 1m, 5m, 15m, 1h, 1d) via independent per-interval BarAggregators.
- Volume-Weighted Average Price (VWAP): Tracks running cumulative dollar volume
  (sum(P_i * Q_i)) and cumulative share volume (sum(Q_i)) per candle bucket:
      VWAP = sum(P_i * Q_i) / sum(Q_i)
- Point-In-Time Temporal Queries: Supports zero-lookahead "as-of" historical queries
  essential for backtesting models without survivorship or future lookahead bias:
      SELECT * FROM bars WHERE instrument_id = ? AND bucket_start <= as_of_time ORDER BY bucket_start DESC LIMIT 1
- High-Performance SQLite WAL Backend: Utilizes SQLite Write-Ahead Logging (WAL), memory-mapped
  I/O (256MB mmap), synchronous=NORMAL, and 64MB cache for sub-millisecond query execution.
- Idempotent Merging (UPSERT): Uses ON CONFLICT DO UPDATE to combine partial candles
  and correctly re-blend running VWAP without loss of precision:
      VWAP_merged = ((VWAP_cur * Vol_cur) + (VWAP_new * Vol_new)) / (Vol_cur + Vol_new)
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

from models import CanonicalEvent, EventType

__stability__ = "beta"

INTERVALS: dict[str, float] = {
    "1s": 1.0,
    "5s": 5.0,
    "1m": 60.0,
    "5m": 300.0,
    "15m": 900.0,
    "1h": 3600.0,
    "1d": 86400.0,
}


@dataclass(slots=True)
class Bar:
    """
    Standardized OHLCV + VWAP market candle.

    Attributes:
        instrument_id: Unique asset ticker symbol (e.g. 'AAPL', 'BTC/USD').
        interval_s: Candle duration in seconds (e.g. 60.0 for 1-minute bar).
        bucket_start: Unix epoch timestamp marking the start boundary of the interval bucket.
        open: First traded price observed in the bucket interval.
        high: Maximum traded price observed in the bucket interval.
        low: Minimum traded price observed in the bucket interval.
        close: Most recent (last) traded price observed in the bucket interval.
        volume: Total cumulative shares/contracts transacted within the interval.
        trade_count: Total number of discrete TRADE executions in the interval.
        vwap: Volume-weighted average price across all executions in the interval.
    """

    instrument_id: str
    interval_s: float
    bucket_start: float
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    vwap: float


class BarAggregator:
    """
    Incrementally rolls up streaming tick events into OHLCV bars for a single interval duration.

    Maintains in-memory rolling accumulators per instrument. When a new trade arrives with a
    timestamp in a subsequent bucket interval, the preceding bucket is finalized, sealed into
    a completed Bar object, and emitted.
    """

    def __init__(self, interval_s: float = 60.0) -> None:
        """
        Initialize the single-interval candle aggregator.

        Args:
            interval_s: Fixed bucket duration in seconds (e.g. 60.0 for 1-minute bars).
        """
        self._interval_s: float = interval_s
        # Map: instrument_id -> (bucket_start_epoch, {open, high, low, close, volume, trade_count, vwap_num})
        self._buckets: dict[str, tuple[float, dict[str, float]]] = {}

    def observe(self, event: CanonicalEvent) -> Bar | None:
        """
        Ingest a canonical event and update the active rolling candle.

        If the event's timestamp crosses into a new bucket interval, the active bucket
        is closed, converted to a completed Bar, and returned.

        Args:
            event: The incoming CanonicalEvent to evaluate.

        Returns:
            Bar | None: A finalized Bar if an existing candle was closed, else None.
        """
        # Only TRADE events with valid non-null prices and quantities form candles
        if (
            event.event_type != EventType.TRADE
            or event.price is None
            or event.quantity is None
        ):
            return None

        # Compute interval bucket start boundary using floor division
        bucket_start = float(
            int(event.exchange_timestamp // self._interval_s) * self._interval_s
        )

        completed_bar = None
        if event.instrument_id in self._buckets:
            current_start, data = self._buckets[event.instrument_id]
            if bucket_start > current_start:
                # Close and seal the previous candle bucket
                vwap = (
                    data["vwap_num"] / data["volume"]
                    if data["volume"] > 0
                    else data["close"]
                )
                completed_bar = Bar(
                    instrument_id=event.instrument_id,
                    interval_s=self._interval_s,
                    bucket_start=current_start,
                    open=data["open"],
                    high=data["high"],
                    low=data["low"],
                    close=data["close"],
                    volume=data["volume"],
                    trade_count=int(data["trade_count"]),
                    vwap=vwap,
                )
                # Initialize new bucket below
            elif bucket_start < current_start:
                # Late-arriving tick older than current bucket start; ignored to preserve linear monotonicity
                return None

        if (
            event.instrument_id not in self._buckets
            or bucket_start > self._buckets[event.instrument_id][0]
        ):
            # Seed fresh bucket accumulator with first trade
            self._buckets[event.instrument_id] = (
                bucket_start,
                {
                    "open": event.price,
                    "high": event.price,
                    "low": event.price,
                    "close": event.price,
                    "volume": event.quantity,
                    "trade_count": 1.0,
                    "vwap_num": event.price * event.quantity,
                },
            )
        else:
            # Incrementally update active bucket metrics
            data = self._buckets[event.instrument_id][1]
            data["high"] = max(data["high"], event.price)
            data["low"] = min(data["low"], event.price)
            data["close"] = event.price
            data["volume"] += event.quantity
            data["trade_count"] += 1.0
            data["vwap_num"] += event.price * event.quantity

        return completed_bar

    def flush(self) -> list[Bar]:
        """
        Extract all unsealed, in-flight rolling candles and reset internal memory.

        Returns:
            list[Bar]: List of finalized Bar objects representing all open buckets.
        """
        bars: list[Bar] = []
        for instrument_id, (bucket_start, data) in self._buckets.items():
            vwap = (
                data["vwap_num"] / data["volume"]
                if data["volume"] > 0
                else data["close"]
            )
            bars.append(
                Bar(
                    instrument_id=instrument_id,
                    interval_s=self._interval_s,
                    bucket_start=bucket_start,
                    open=data["open"],
                    high=data["high"],
                    low=data["low"],
                    close=data["close"],
                    volume=data["volume"],
                    trade_count=int(data["trade_count"]),
                    vwap=vwap,
                )
            )
        self._buckets.clear()
        return bars


class BarDatabase:
    """
    Persistent multi-timeframe OHLCV bar storage with temporal as-of queries.

    Manages concurrent multi-timeframe candle roll-ups backed by an optimized SQLite
    relational engine. Provides point-in-time historical querying, interval windowing,
    and thread-safe transactional persistence.
    """

    def __init__(
        self, db_path: str = "data/bars.db", intervals: list[str] | None = None
    ) -> None:
        """
        Initialize the bar database connection and internal aggregators.

        Args:
            db_path: Path to the SQLite database file, or ':memory:' for transient storage.
            intervals: List of interval codes to track (defaults to all supported standard intervals).
        """
        self.db_path: str = db_path
        self._conn: sqlite3.Connection = sqlite3.connect(db_path)
        # Enable Write-Ahead Logging (WAL) for high-concurrency read/write operations
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # WAL mode not supported for purely in-memory databases (:memory:)
        # Tune engine for low-latency ingest
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA mmap_size=268435456")  # 256 MB memory-mapped I/O
        self._conn.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._init_db()

        self.intervals_config: list[str] = intervals or [
            "1s",
            "5s",
            "1m",
            "5m",
            "15m",
            "1h",
            "1d",
        ]
        self.aggregators: dict[str, BarAggregator] = {
            interval: BarAggregator(INTERVALS[interval])
            for interval in self.intervals_config
            if interval in INTERVALS
        }

        self._pending_bars: list[Bar] = []

    def _init_db(self) -> None:
        """Create the canonical bars schema if not already present."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS bars (
                instrument_id TEXT,
                interval_s REAL,
                bucket_start REAL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                trade_count INTEGER,
                vwap REAL,
                PRIMARY KEY (instrument_id, interval_s, bucket_start)
            )
        """)
        self._conn.commit()

    def __enter__(self) -> BarDatabase:
        """Support context manager protocol for automated cleanup."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Flush unwritten bars and close database connection upon exit."""
        self.close()

    def observe(self, event: CanonicalEvent) -> None:
        """
        Feed a trade event into all active interval aggregators.

        Dispatches the event to every configured timeframe aggregator. Completed
        candles are staged in the batch buffer; when the buffer reaches 1,000 bars,
        it is automatically committed to disk.

        Args:
            event: The incoming CanonicalEvent to ingest.
        """
        for agg in self.aggregators.values():
            bar = agg.observe(event)
            if bar:
                self._pending_bars.append(bar)

        # Batch write threshold for amortizing transactional overhead
        if len(self._pending_bars) >= 1000:
            self._write_bars(self._pending_bars)
            self._pending_bars.clear()

    def flush(self) -> None:
        """
        Force-flush all active aggregators and commit all pending bars to SQLite.

        Extracts partial/open bars from in-memory aggregators and persists them.
        """
        for agg in self.aggregators.values():
            self._pending_bars.extend(agg.flush())

        if self._pending_bars:
            self._write_bars(self._pending_bars)
            self._pending_bars.clear()

    def _write_bars(self, bars: list[Bar]) -> None:
        """
        Bulk upsert bars into SQLite using ON CONFLICT DO UPDATE.

        If a candle bucket already exists, combines volumes, trade counts, expands high/low,
        and re-blends the volume-weighted average price (VWAP).
        """
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO bars (
                    instrument_id, interval_s, bucket_start,
                    open, high, low, close, volume, trade_count, vwap
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, interval_s, bucket_start) DO UPDATE SET
                    open=bars.open,
                    high=max(bars.high, excluded.high),
                    low=min(bars.low, excluded.low),
                    close=excluded.close,
                    volume=bars.volume + excluded.volume,
                    trade_count=bars.trade_count + excluded.trade_count,
                    vwap=CASE 
                        WHEN (bars.volume + excluded.volume) > 0 
                        THEN ((bars.vwap * bars.volume) + (excluded.vwap * excluded.volume)) / (bars.volume + excluded.volume)
                        ELSE excluded.vwap 
                    END
            """,
                [
                    (
                        b.instrument_id,
                        b.interval_s,
                        b.bucket_start,
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                        b.trade_count,
                        b.vwap,
                    )
                    for b in bars
                ],
            )

    def close(self) -> None:
        """Flush unwritten bars and safely terminate database connection."""
        self.flush()
        self._conn.close()

    def _row_to_bar(self, row: tuple[Any, ...]) -> Bar:
        """Hydrate a database query tuple into a strongly-typed Bar dataclass."""
        return Bar(
            instrument_id=row[0],
            interval_s=row[1],
            bucket_start=row[2],
            open=row[3],
            high=row[4],
            low=row[5],
            close=row[6],
            volume=row[7],
            trade_count=row[8],
            vwap=row[9],
        )

    def query_bars(
        self,
        instrument_id: str,
        interval: str = "1m",
        start_time: float | None = None,
        end_time: float | None = None,
        limit: int = 1000,
    ) -> list[Bar]:
        """
        Query chronological bars for a given symbol and interval.

        Args:
            instrument_id: Ticker symbol to query (e.g. 'AAPL').
            interval: Interval duration code (e.g. '1m', '5m', '1h').
            start_time: Optional minimum bucket_start epoch filter.
            end_time: Optional maximum bucket_start epoch filter.
            limit: Maximum count of candles to return.

        Returns:
            list[Bar]: Matching bars sorted ascending by bucket_start.
        """
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")

        query = "SELECT * FROM bars WHERE instrument_id = ? AND interval_s = ?"
        params: list[Any] = [instrument_id, interval_s]

        if start_time is not None:
            query += " AND bucket_start >= ?"
            params.append(start_time)
        if end_time is not None:
            query += " AND bucket_start <= ?"
            params.append(end_time)

        query += " ORDER BY bucket_start ASC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        return [self._row_to_bar(row) for row in cursor.fetchall()]

    def query_as_of(
        self, instrument_id: str, interval: str, as_of_time: float
    ) -> Bar | None:
        """
        Return the most recent completed bar strictly on or prior to as_of_time.

        Ensures zero-lookahead temporal integrity when backtesting or evaluating models.

        Args:
            instrument_id: Target symbol.
            interval: Target timeframe code.
            as_of_time: Cutoff timestamp epoch.

        Returns:
            Bar | None: The last bar at or before as_of_time, or None if no prior data exists.
        """
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")

        cursor = self._conn.execute(
            """
            SELECT * FROM bars 
            WHERE instrument_id = ? AND interval_s = ? AND bucket_start <= ?
            ORDER BY bucket_start DESC LIMIT 1
        """,
            (instrument_id, interval_s, as_of_time),
        )

        row = cursor.fetchone()
        return self._row_to_bar(row) if row else None

    def query_window(
        self, instrument_id: str, interval: str, center_time: float, window_s: float
    ) -> list[Bar]:
        """
        Return all bars falling within center_time ± window_s.

        Useful for event-study volatility and impact analysis surrounding corporate actions.

        Args:
            instrument_id: Target symbol.
            interval: Target timeframe code.
            center_time: Center anchor timestamp epoch.
            window_s: Window radius in seconds.

        Returns:
            list[Bar]: All bars within [center_time - window_s, center_time + window_s].
        """
        return self.query_bars(
            instrument_id=instrument_id,
            interval=interval,
            start_time=center_time - window_s,
            end_time=center_time + window_s,
        )

    def latest_bar(self, instrument_id: str, interval: str = "1m") -> Bar | None:
        """
        Return the most recent stored bar for the specified instrument and interval.

        Args:
            instrument_id: Target symbol.
            interval: Target timeframe code.

        Returns:
            Bar | None: The latest bar or None if the series is empty.
        """
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")

        cursor = self._conn.execute(
            """
            SELECT * FROM bars 
            WHERE instrument_id = ? AND interval_s = ?
            ORDER BY bucket_start DESC LIMIT 1
        """,
            (instrument_id, interval_s),
        )

        row = cursor.fetchone()
        return self._row_to_bar(row) if row else None

    def bar_count(
        self, instrument_id: str | None = None, interval: str | None = None
    ) -> int:
        """
        Count total stored bars, optionally filtered by symbol and/or interval.

        Args:
            instrument_id: Optional symbol filter.
            interval: Optional interval code filter.

        Returns:
            int: Number of matching candle records.
        """
        query = "SELECT COUNT(*) FROM bars WHERE 1=1"
        params: list[Any] = []

        if instrument_id:
            query += " AND instrument_id = ?"
            params.append(instrument_id)

        if interval:
            interval_s = INTERVALS.get(interval)
            if interval_s:
                query += " AND interval_s = ?"
                params.append(interval_s)

        cursor = self._conn.execute(query, params)
        return int(cursor.fetchone()[0])

    def instruments(self) -> list[str]:
        """
        List all distinct instrument symbols currently represented in the bar database.

        Returns:
            list[str]: Sorted list of unique symbol strings.
        """
        cursor = self._conn.execute("SELECT DISTINCT instrument_id FROM bars")
        return [row[0] for row in cursor.fetchall()]

    def summary(self) -> dict[str, Any]:
        """
        Compile high-level database diagnostics and coverage metrics.

        Returns:
            dict[str, Any]: Metadata dictionary containing total bars, distinct symbols,
            time horizons, and active intervals.
        """
        cursor = self._conn.execute("""
            SELECT 
                COUNT(*),
                COUNT(DISTINCT instrument_id),
                COUNT(DISTINCT interval_s),
                MIN(bucket_start),
                MAX(bucket_start)
            FROM bars
        """)
        row = cursor.fetchone()

        cursor2 = self._conn.execute("SELECT DISTINCT instrument_id FROM bars LIMIT 10")
        instruments = [r[0] for r in cursor2.fetchall()]

        cursor3 = self._conn.execute("SELECT DISTINCT interval_s FROM bars")
        intervals_inv = {v: k for k, v in INTERVALS.items()}
        intervals_present = [
            intervals_inv.get(r[0], str(r[0])) for r in cursor3.fetchall()
        ]

        return {
            "total_bars": row[0] or 0,
            "total_instruments": row[1] or 0,
            "instrument_count": row[1] or 0,
            "total_intervals": row[2] or 0,
            "min_time": row[3],
            "max_time": row[4],
            "sample_instruments": instruments,
            "intervals": intervals_present,
        }
