import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from bardb import Bar, BarAggregator, BarDatabase, INTERVALS
from models import CanonicalEvent, EventType, QualityStatus

def _make_trade(instrument='AAPL', ts=1000.0, price=150.0, qty=100.0, seq=0):
    return CanonicalEvent(
        event_id=f'evt-{seq}',
        instrument_id=instrument,
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts,
        processing_timestamp=ts,
        source='TEST',
        sequence_number=seq,
        price=price,
        quantity=qty,
        quality_status=QualityStatus.VALID
    )

class TestBarAggregator(unittest.TestCase):
    def test_bar_aggregator_single_bucket(self):
        agg = BarAggregator(interval_s=60.0)
        # Feed 10 trades within same 60s bucket (0-60)
        for i in range(10):
            event = _make_trade(ts=10.0 + i, price=100.0 + i, qty=10.0)
            res = agg.observe(event)
            self.assertIsNone(res)
        
        bars = agg.flush()
        self.assertEqual(len(bars), 1)
        bar = bars[0]
        self.assertEqual(bar.instrument_id, 'AAPL')
        self.assertEqual(bar.interval_s, 60.0)
        self.assertEqual(bar.bucket_start, 0.0)
        self.assertEqual(bar.open, 100.0)
        self.assertEqual(bar.high, 109.0)
        self.assertEqual(bar.low, 100.0)
        self.assertEqual(bar.close, 109.0)
        self.assertEqual(bar.volume, 100.0)
        self.assertEqual(bar.trade_count, 10)

    def test_bar_aggregator_bucket_rollover(self):
        agg = BarAggregator(interval_s=60.0)
        
        # Bucket 1: [0, 60)
        e1 = _make_trade(ts=10.0, price=100.0)
        self.assertIsNone(agg.observe(e1))
        
        # Bucket 2: [60, 120)
        e2 = _make_trade(ts=70.0, price=110.0)
        res = agg.observe(e2)
        self.assertIsNotNone(res) # Bucket 1 finished
        self.assertEqual(res.bucket_start, 0.0)
        self.assertEqual(res.close, 100.0)
        
        # Bucket 3: [120, 180)
        e3 = _make_trade(ts=150.0, price=120.0)
        res2 = agg.observe(e3)
        self.assertIsNotNone(res2) # Bucket 2 finished
        self.assertEqual(res2.bucket_start, 60.0)
        self.assertEqual(res2.close, 110.0)
        
        bars = agg.flush()
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].bucket_start, 120.0)
        
    def test_bar_aggregator_vwap(self):
        agg = BarAggregator(interval_s=60.0)
        # Trade 1: price 100, qty 10 (value 1000)
        agg.observe(_make_trade(ts=10.0, price=100.0, qty=10.0))
        # Trade 2: price 200, qty 20 (value 4000)
        agg.observe(_make_trade(ts=20.0, price=200.0, qty=20.0))
        
        bars = agg.flush()
        bar = bars[0]
        # Total value = 5000, Total qty = 30 -> VWAP = 166.666...
        self.assertAlmostEqual(bar.vwap, 5000.0 / 30.0)
        self.assertEqual(bar.volume, 30.0)

class TestBarDatabase(unittest.TestCase):
    def test_bar_database_write_and_query(self):
        with BarDatabase(':memory:') as db:
            # 200 events, spaced 1 second apart starting at 0
            # 200 seconds -> 4 1-minute buckets (0, 60, 120, 180)
            for i in range(200):
                db.observe(_make_trade(ts=float(i), price=100.0+i))
            db.flush()
            
            bars = db.query_bars('AAPL', interval='1m')
            self.assertEqual(len(bars), 4)
            self.assertEqual([b.bucket_start for b in bars], [0.0, 60.0, 120.0, 180.0])

    def test_bar_database_multi_interval(self):
        with BarDatabase(':memory:', intervals=['1m', '5m']) as db:
            for i in range(400):
                db.observe(_make_trade(ts=float(i), price=100.0))
            db.flush()
            
            # 400 seconds -> 7 1m buckets (0, 60, ..., 360)
            bars_1m = db.query_bars('AAPL', interval='1m')
            self.assertEqual(len(bars_1m), 7)
            
            # 400 seconds -> 2 5m buckets (0, 300)
            bars_5m = db.query_bars('AAPL', interval='5m')
            self.assertEqual(len(bars_5m), 2)
            self.assertEqual([b.bucket_start for b in bars_5m], [0.0, 300.0])

    def test_bar_database_query_as_of(self):
        with BarDatabase(':memory:') as db:
            for i in [10, 70, 130, 190]:
                db.observe(_make_trade(ts=float(i), price=float(i)))
            db.flush()
            
            # Buckets are at 0, 60, 120, 180
            # as_of = 100 -> should return bucket 60
            bar = db.query_as_of('AAPL', '1m', 100.0)
            self.assertIsNotNone(bar)
            self.assertEqual(bar.bucket_start, 60.0)
            
            # as_of = 30 -> should return bucket 0
            bar2 = db.query_as_of('AAPL', '1m', 30.0)
            self.assertIsNotNone(bar2)
            self.assertEqual(bar2.bucket_start, 0.0)
            
            # as_of = -10 -> None
            bar3 = db.query_as_of('AAPL', '1m', -10.0)
            self.assertIsNone(bar3)

    def test_bar_database_query_window(self):
        with BarDatabase(':memory:') as db:
            for i in range(0, 600, 30):  # 10 minutes, trades every 30s
                db.observe(_make_trade(ts=float(i), price=float(i)))
            db.flush()
            
            # Center at min 5 (300s), window=60s -> [240, 360]
            # Buckets in this range: 240, 300, 360
            bars = db.query_window('AAPL', '1m', center_time=300.0, window_s=60.0)
            self.assertEqual(len(bars), 3)
            self.assertEqual([b.bucket_start for b in bars], [240.0, 300.0, 360.0])

    def test_bar_database_latest_bar(self):
        with BarDatabase(':memory:') as db:
            self.assertIsNone(db.latest_bar('AAPL', '1m'))
            for i in [10, 70, 130]:
                db.observe(_make_trade(ts=float(i), price=float(i)))
            db.flush()
            
            bar = db.latest_bar('AAPL', '1m')
            self.assertIsNotNone(bar)
            self.assertEqual(bar.bucket_start, 120.0)

    def test_bar_database_bar_count(self):
        with BarDatabase(':memory:', intervals=['1m', '5m']) as db:
            for i in range(400):
                db.observe(_make_trade(ts=float(i), price=100.0))
            db.flush()
            
            self.assertEqual(db.bar_count('AAPL', '1m'), 7)
            self.assertEqual(db.bar_count('AAPL', '5m'), 2)
            self.assertEqual(db.bar_count(), 9)

    def test_bar_database_instruments(self):
        with BarDatabase(':memory:') as db:
            self.assertEqual(db.instruments(), [])
            db.observe(_make_trade(instrument='AAPL', ts=10.0))
            db.observe(_make_trade(instrument='MSFT', ts=10.0))
            db.flush()
            
            insts = db.instruments()
            self.assertEqual(set(insts), {'AAPL', 'MSFT'})

    def test_bar_database_summary(self):
        with BarDatabase(':memory:') as db:
            db.observe(_make_trade(instrument='AAPL', ts=10.0))
            db.flush()
            summary = db.summary()
            self.assertIn('intervals', summary)
            self.assertIn('instrument_count', summary)
            self.assertIn('total_bars', summary)

    def test_bar_database_context_manager(self):
        with BarDatabase(':memory:', intervals=['1m']) as db:
            db.observe(_make_trade(ts=10.0))
            db.flush()
            self.assertEqual(db.bar_count(), 1)

if __name__ == '__main__':
    unittest.main()
