"""
Persistent multi-timeframe OHLCV bar database for MDRAP.

This module provides a persistent bar storage engine that incrementally rolls up raw ticks
into multi-timeframe OHLCV candles and supports temporal as-of queries. Bars are incrementally
computed from raw trade events and stored in SQLite with temporal query support. This forms
the foundation for all historical quantitative analysis.
"""

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, List

from models import CanonicalEvent, EventType

INTERVALS = {
    '1s': 1.0,
    '5s': 5.0,
    '1m': 60.0,
    '5m': 300.0,
    '15m': 900.0,
    '1h': 3600.0,
    '1d': 86400.0,
}

@dataclass(slots=True)
class Bar:
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
    """Incrementally rolls up trade events into OHLCV bars at a given interval."""
    def __init__(self, interval_s: float = 60.0):
        self._interval_s = interval_s
        self._buckets: dict[str, tuple[float, dict]] = {}  # instrument_id -> (current_bucket_start, bar_data)
        
    def observe(self, event: CanonicalEvent) -> Bar | None:
        """Feed a trade event. Returns a completed Bar if a new bucket started (closing the previous one)."""
        if event.event_type != EventType.TRADE or event.price is None or event.quantity is None:
            return None
            
        bucket_start = float(int(event.exchange_timestamp // self._interval_s) * self._interval_s)
        
        completed_bar = None
        if event.instrument_id in self._buckets:
            current_start, data = self._buckets[event.instrument_id]
            if bucket_start > current_start:
                # Close the previous bucket
                vwap = data['vwap_num'] / data['volume'] if data['volume'] > 0 else data['close']
                completed_bar = Bar(
                    instrument_id=event.instrument_id,
                    interval_s=self._interval_s,
                    bucket_start=current_start,
                    open=data['open'],
                    high=data['high'],
                    low=data['low'],
                    close=data['close'],
                    volume=data['volume'],
                    trade_count=data['trade_count'],
                    vwap=vwap
                )
                # Initialize new bucket below
            elif bucket_start < current_start:
                # Late arrival for a previous bucket (ignoring for now as per simple incremental roll-up)
                return None
                
        if event.instrument_id not in self._buckets or bucket_start > self._buckets[event.instrument_id][0]:
            # New bucket
            self._buckets[event.instrument_id] = (bucket_start, {
                'open': event.price,
                'high': event.price,
                'low': event.price,
                'close': event.price,
                'volume': event.quantity,
                'trade_count': 1,
                'vwap_num': event.price * event.quantity
            })
        else:
            # Update existing bucket
            data = self._buckets[event.instrument_id][1]
            data['high'] = max(data['high'], event.price)
            data['low'] = min(data['low'], event.price)
            data['close'] = event.price
            data['volume'] += event.quantity
            data['trade_count'] += 1
            data['vwap_num'] += event.price * event.quantity
            
        return completed_bar
        
    def flush(self) -> list[Bar]:
        """Return all currently open (incomplete) bars and clear state."""
        bars = []
        for instrument_id, (bucket_start, data) in self._buckets.items():
            vwap = data['vwap_num'] / data['volume'] if data['volume'] > 0 else data['close']
            bars.append(Bar(
                instrument_id=instrument_id,
                interval_s=self._interval_s,
                bucket_start=bucket_start,
                open=data['open'],
                high=data['high'],
                low=data['low'],
                close=data['close'],
                volume=data['volume'],
                trade_count=data['trade_count'],
                vwap=vwap
            ))
        self._buckets.clear()
        return bars


class BarDatabase:
    """Persistent multi-timeframe OHLCV bar storage with temporal queries."""
    
    def __init__(self, db_path: str = 'data/bars.db', intervals: list[str] | None = None):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        try:
            self._conn.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError:
            pass
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA mmap_size=268435456')
        self._conn.execute('PRAGMA cache_size=-64000')
        self._conn.execute('PRAGMA temp_store=MEMORY')
        self._init_db()
        
        self.intervals_config = intervals or ['1s', '5s', '1m', '5m', '15m', '1h', '1d']
        self.aggregators = {
            interval: BarAggregator(INTERVALS[interval])
            for interval in self.intervals_config
            if interval in INTERVALS
        }
        
        self._pending_bars: list[Bar] = []
        
    def _init_db(self):
        self._conn.execute('''
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
        ''')
        self._conn.commit()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        
    def observe(self, event: CanonicalEvent):
        """Feed a trade event. Rolls up into all configured intervals. Completed bars are batched for write."""
        for interval, agg in self.aggregators.items():
            bar = agg.observe(event)
            if bar:
                self._pending_bars.append(bar)
                
        if len(self._pending_bars) >= 1000:
            self._write_bars(self._pending_bars)
            self._pending_bars.clear()
            
    def flush(self):
        """Write all pending bars to SQLite via executemany."""
        for agg in self.aggregators.values():
            self._pending_bars.extend(agg.flush())
            
        if self._pending_bars:
            self._write_bars(self._pending_bars)
            self._pending_bars.clear()
            
    def _write_bars(self, bars: list[Bar]):
        with self._conn:
            self._conn.executemany('''
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
            ''', [
                (b.instrument_id, b.interval_s, b.bucket_start, b.open, b.high, b.low, b.close, b.volume, b.trade_count, b.vwap)
                for b in bars
            ])
            
    def close(self):
        """Flush and close connection."""
        self.flush()
        self._conn.close()
        
    def _row_to_bar(self, row: tuple) -> Bar:
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
            vwap=row[9]
        )
        
    def query_bars(self, instrument_id: str, interval: str = '1m', start_time: float | None = None, end_time: float | None = None, limit: int = 1000) -> list[Bar]:
        """Query bars from SQLite. Returns bars sorted by bucket_start."""
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")
            
        query = "SELECT * FROM bars WHERE instrument_id = ? AND interval_s = ?"
        params = [instrument_id, interval_s]
        
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
        
    def query_as_of(self, instrument_id: str, interval: str, as_of_time: float) -> Bar | None:
        """Return the last completed bar at or before as_of_time."""
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")
            
        cursor = self._conn.execute('''
            SELECT * FROM bars 
            WHERE instrument_id = ? AND interval_s = ? AND bucket_start <= ?
            ORDER BY bucket_start DESC LIMIT 1
        ''', (instrument_id, interval_s, as_of_time))
        
        row = cursor.fetchone()
        return self._row_to_bar(row) if row else None
        
    def query_window(self, instrument_id: str, interval: str, center_time: float, window_s: float) -> list[Bar]:
        """Return all bars within center_time ± window_s."""
        return self.query_bars(
            instrument_id=instrument_id,
            interval=interval,
            start_time=center_time - window_s,
            end_time=center_time + window_s
        )
        
    def latest_bar(self, instrument_id: str, interval: str = '1m') -> Bar | None:
        """Return the most recent bar."""
        interval_s = INTERVALS.get(interval)
        if not interval_s:
            raise ValueError(f"Unknown interval: {interval}")
            
        cursor = self._conn.execute('''
            SELECT * FROM bars 
            WHERE instrument_id = ? AND interval_s = ?
            ORDER BY bucket_start DESC LIMIT 1
        ''', (instrument_id, interval_s))
        
        row = cursor.fetchone()
        return self._row_to_bar(row) if row else None
        
    def bar_count(self, instrument_id: str | None = None, interval: str | None = None) -> int:
        """Count stored bars, optionally filtered."""
        query = "SELECT COUNT(*) FROM bars WHERE 1=1"
        params = []
        
        if instrument_id:
            query += " AND instrument_id = ?"
            params.append(instrument_id)
            
        if interval:
            interval_s = INTERVALS.get(interval)
            if interval_s:
                query += " AND interval_s = ?"
                params.append(interval_s)
                
        cursor = self._conn.execute(query, params)
        return cursor.fetchone()[0]
        
    def instruments(self) -> list[str]:
        """List all instruments with stored bars."""
        cursor = self._conn.execute("SELECT DISTINCT instrument_id FROM bars")
        return [row[0] for row in cursor.fetchall()]
        
    def summary(self) -> dict:
        """Return summary stats: total bars, instruments, intervals, time range."""
        cursor = self._conn.execute('''
            SELECT 
                COUNT(*),
                COUNT(DISTINCT instrument_id),
                COUNT(DISTINCT interval_s),
                MIN(bucket_start),
                MAX(bucket_start)
            FROM bars
        ''')
        row = cursor.fetchone()
        
        cursor2 = self._conn.execute('SELECT DISTINCT instrument_id FROM bars LIMIT 10')
        instruments = [r[0] for r in cursor2.fetchall()]
        
        cursor3 = self._conn.execute('SELECT DISTINCT interval_s FROM bars')
        intervals_inv = {v: k for k, v in INTERVALS.items()}
        intervals_present = [intervals_inv.get(r[0], str(r[0])) for r in cursor3.fetchall()]
        
        return {
            'total_bars': row[0] or 0,
            'total_instruments': row[1] or 0,
            'instrument_count': row[1] or 0,
            'total_intervals': row[2] or 0,
            'min_time': row[3],
            'max_time': row[4],
            'sample_instruments': instruments,
            'intervals': intervals_present
        }
