import sys
import os
import math
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import CanonicalEvent, EventType, QualityStatus
from analytics import OHLCVAggregator, SpreadAnalyzer, VolatilityTracker, MarketAnalytics

def test_ohlcv_single_bucket():
    aggregator = OHLCVAggregator(interval_s=5.0)
    prices = [150.0, 155.0, 145.0, 152.0, 153.0]
    for i, p in enumerate(prices):
        event = CanonicalEvent(
            event_id=f't{i}', instrument_id='AAPL', event_type=EventType.TRADE,
            exchange_timestamp=1000.0 + i, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source='FEEDA', sequence_number=i,
            price=p, quantity=100.0
        )
        aggregator.observe(event)

    candles = aggregator.candles()
    assert len(candles) == 1
    candle = candles[0]
    assert candle["open"] == 150.0
    assert candle["high"] == 155.0
    assert candle["low"] == 145.0
    assert candle["close"] == 153.0
    assert candle["event_count"] == 5
    assert candle["volume"] == 500.0

def test_ohlcv_multiple_buckets():
    aggregator = OHLCVAggregator(interval_s=5.0)
    
    # Bucket 1: 1000 to 1004.999
    event1 = CanonicalEvent(
        event_id='t1', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        price=150.0, quantity=100.0
    )
    event2 = CanonicalEvent(
        event_id='t2', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1002.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=2,
        price=152.0, quantity=100.0
    )
    # Bucket 2: 1005 to 1009.999
    event3 = CanonicalEvent(
        event_id='t3', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1005.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=3,
        price=155.0, quantity=100.0
    )
    event4 = CanonicalEvent(
        event_id='t4', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1007.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=4,
        price=158.0, quantity=100.0
    )
    
    for e in [event1, event2, event3, event4]:
        aggregator.observe(e)
        
    candles = aggregator.candles()
    assert len(candles) == 2

def test_ohlcv_ignores_quotes():
    aggregator = OHLCVAggregator(interval_s=5.0)
    event = CanonicalEvent(
        event_id='q1', instrument_id='AAPL', event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        bid_price=149.5, bid_size=100.0, ask_price=150.5, ask_size=200.0
    )
    aggregator.observe(event)
    assert len(aggregator.candles()) == 0

def test_ohlcv_candles_for_filters():
    aggregator = OHLCVAggregator(interval_s=5.0)
    aapl_event = CanonicalEvent(
        event_id='t1', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        price=150.0, quantity=100.0
    )
    msft_event = CanonicalEvent(
        event_id='t2', instrument_id='MSFT', event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=2,
        price=250.0, quantity=100.0
    )
    
    aggregator.observe(aapl_event)
    aggregator.observe(msft_event)
    
    aapl_candles = aggregator.candles_for('AAPL')
    assert len(aapl_candles) == 1
    assert aapl_candles[0]["instrument_id"] == 'AAPL'

def test_spread_analyzer_basic():
    analyzer = SpreadAnalyzer()
    quotes = [(149.5, 150.5), (149.0, 151.0), (149.8, 150.2)]
    
    for i, (bid, ask) in enumerate(quotes):
        event = CanonicalEvent(
            event_id=f'q{i}', instrument_id='AAPL', event_type=EventType.QUOTE,
            exchange_timestamp=1000.0 + i, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source='FEEDA', sequence_number=i,
            bid_price=bid, bid_size=100.0, ask_price=ask, ask_size=200.0
        )
        analyzer.observe(event)
        
    summary = analyzer.summary()
    assert len(summary) == 1
    stats = summary[0]
    assert stats["instrument_id"] == 'AAPL'
    assert stats["quote_count"] == 3
    assert math.isclose(stats["mean_spread"], (1.0 + 2.0 + 0.4) / 3, rel_tol=1e-5)
    assert math.isclose(stats["min_spread"], 0.4, rel_tol=1e-5)
    assert math.isclose(stats["max_spread"], 2.0, rel_tol=1e-5)
    assert stats["crossed_count"] == 0

def test_spread_analyzer_crossed_quote():
    analyzer = SpreadAnalyzer()
    event = CanonicalEvent(
        event_id='q1', instrument_id='AAPL', event_type=EventType.QUOTE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        bid_price=150.5, bid_size=100.0, ask_price=149.5, ask_size=200.0
    )
    analyzer.observe(event)
    summary = analyzer.summary()
    assert summary[0]["crossed_count"] == 1

def test_spread_ignores_trades():
    analyzer = SpreadAnalyzer()
    event = CanonicalEvent(
        event_id='t1', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        price=150.0, quantity=100.0
    )
    analyzer.observe(event)
    assert len(analyzer.summary()) == 0

def test_volatility_tracker_basic():
    tracker = VolatilityTracker(window=100)
    for i in range(5):
        event = CanonicalEvent(
            event_id=f't{i}', instrument_id='AAPL', event_type=EventType.TRADE,
            exchange_timestamp=1000.0 + i, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source='FEEDA', sequence_number=i,
            price=100.0, quantity=100.0
        )
        tracker.observe(event)
        
    summary = tracker.summary()
    assert len(summary) == 1
    stats = summary[0]
    assert math.isclose(stats["std_dev"], 0.0, abs_tol=1e-9)
    assert stats["price_range_pct"] == 0.0

def test_volatility_tracker_with_variance():
    tracker = VolatilityTracker(window=100)
    prices = [100.0, 102.0, 98.0, 104.0, 96.0]
    for i, p in enumerate(prices):
        event = CanonicalEvent(
            event_id=f't{i}', instrument_id='AAPL', event_type=EventType.TRADE,
            exchange_timestamp=1000.0 + i, receive_timestamp=1000.001,
            processing_timestamp=1000.002, source='FEEDA', sequence_number=i,
            price=p, quantity=100.0
        )
        tracker.observe(event)
        
    summary = tracker.summary()
    assert len(summary) == 1
    stats = summary[0]
    assert stats["std_dev"] > 0.0
    assert math.isclose(stats["mean_price"], 100.0, rel_tol=1e-5)
    assert stats["min_price"] == 96.0
    assert stats["max_price"] == 104.0

def test_market_analytics_facade():
    facade = MarketAnalytics(ohlcv_interval_s=5.0, volatility_window=100)
    
    trade = CanonicalEvent(
        event_id='t1', instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=1000.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=1,
        price=150.0, quantity=100.0
    )
    quote = CanonicalEvent(
        event_id='q1', instrument_id='AAPL', event_type=EventType.QUOTE,
        exchange_timestamp=1001.0, receive_timestamp=1000.001,
        processing_timestamp=1000.002, source='FEEDA', sequence_number=2,
        bid_price=149.5, bid_size=100.0, ask_price=150.5, ask_size=200.0
    )
    
    facade.observe(trade)
    facade.observe(quote)
    
    summary = facade.full_summary()
    assert 'ohlcv' in summary
    assert 'spreads' in summary
    assert 'volatility' in summary
