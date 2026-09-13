import os
import sys
import tempfile
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from alerts import AlertEngine, AlertStatus, AlertType

def test_price_above_alert():
    engine = AlertEngine()
    alert = engine.add_price_alert("AAPL", "ABOVE", 200.0, repeat=False)
    assert alert.status == AlertStatus.ACTIVE
    
    triggered = engine.evaluate_tick("AAPL", 195.0, 100, 194.9, 195.1, time.time())
    assert not triggered
    assert alert.status == AlertStatus.ACTIVE
    
    triggered = engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    assert len(triggered) == 1
    assert triggered[0].alert_id == alert.alert_id
    assert triggered[0].status == AlertStatus.TRIGGERED

def test_price_below_alert():
    engine = AlertEngine()
    alert = engine.add_price_alert("AAPL", "BELOW", 150.0, repeat=False)
    
    triggered = engine.evaluate_tick("AAPL", 155.0, 100, 154.9, 155.1, time.time())
    assert not triggered
    
    triggered = engine.evaluate_tick("AAPL", 145.0, 100, 144.9, 145.1, time.time())
    assert len(triggered) == 1
    assert triggered[0].alert_id == alert.alert_id
    assert triggered[0].status == AlertStatus.TRIGGERED

def test_spread_alert():
    engine = AlertEngine()
    alert = engine.add_spread_alert("AAPL", 10.0) # 10 bps
    
    # 10 bps = 0.001
    triggered = engine.evaluate_tick("AAPL", 100.0, 100, 99.95, 100.05, time.time())
    assert not triggered
    
    triggered = engine.evaluate_tick("AAPL", 100.0, 100, 99.90, 100.10, time.time())
    assert len(triggered) == 1
    assert triggered[0].alert_id == alert.alert_id

def test_volume_spike_alert():
    engine = AlertEngine()
    alert = engine.add_volume_alert("AAPL", 10000)
    
    triggered = engine.evaluate_tick("AAPL", 150.0, 5000, 149.9, 150.1, time.time())
    assert not triggered
    
    triggered = engine.evaluate_tick("AAPL", 150.0, 15000, 149.9, 150.1, time.time())
    assert len(triggered) == 1
    assert triggered[0].alert_id == alert.alert_id

def test_alert_repeat():
    engine = AlertEngine()
    alert = engine.add_price_alert("AAPL", "ABOVE", 200.0, repeat=True)
    
    triggered = engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    assert len(triggered) == 1
    
    assert alert.status == AlertStatus.ACTIVE
    
    engine.evaluate_tick("AAPL", 195.0, 100, 194.9, 195.1, time.time())
    triggered = engine.evaluate_tick("AAPL", 210.0, 100, 209.9, 210.1, time.time())
    assert len(triggered) == 1

def test_cancel_alert():
    engine = AlertEngine()
    alert = engine.add_price_alert("AAPL", "ABOVE", 200.0)
    assert engine.active_count() == 1
    
    success = engine.cancel_alert(alert.alert_id)
    assert success
    assert alert.status == AlertStatus.CANCELLED
    assert engine.active_count() == 0
    
    triggered = engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    assert not triggered

def test_list_and_filter_alerts():
    engine = AlertEngine()
    engine.add_price_alert("AAPL", "ABOVE", 200.0)
    engine.add_price_alert("MSFT", "ABOVE", 300.0)
    engine.add_volume_alert("AAPL", 10000)
    engine.add_spread_alert("TSLA", 20)
    engine.add_price_alert("TSLA", "BELOW", 150.0)
    
    assert len(engine.list_alerts()) == 5
    assert len(engine.list_alerts(symbol="AAPL")) == 2
    assert len(engine.list_alerts(symbol="TSLA")) == 2
    assert len(engine.list_alerts(status=AlertStatus.ACTIVE)) == 5
    
    engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    assert len(engine.list_alerts(status=AlertStatus.TRIGGERED)) == 1

def test_alert_history():
    engine = AlertEngine()
    a1 = engine.add_price_alert("AAPL", "ABOVE", 200.0)
    a2 = engine.add_price_alert("MSFT", "ABOVE", 300.0)
    a3 = engine.add_volume_alert("TSLA", 10000)
    
    engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    time.sleep(0.01)
    engine.evaluate_tick("MSFT", 305.0, 100, 304.9, 305.1, time.time())
    time.sleep(0.01)
    engine.evaluate_tick("TSLA", 200.0, 15000, 199.9, 200.1, time.time())
    
    history = engine.history(limit=10)
    assert len(history) == 3
    assert history[0].alert_id == a3.alert_id
    assert history[1].alert_id == a2.alert_id
    assert history[2].alert_id == a1.alert_id

def test_clear_triggered():
    engine = AlertEngine()
    engine.add_price_alert("AAPL", "ABOVE", 200.0)
    engine.add_price_alert("MSFT", "ABOVE", 300.0)
    
    engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    assert engine.triggered_count() == 1
    
    cleared = engine.clear_triggered()
    assert cleared == 1
    assert engine.triggered_count() == 0

def test_alert_summary():
    engine = AlertEngine()
    engine.add_price_alert("AAPL", "ABOVE", 200.0)
    engine.add_price_alert("MSFT", "ABOVE", 300.0)
    a3 = engine.add_price_alert("TSLA", "BELOW", 150.0)
    
    engine.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
    engine.cancel_alert(a3.alert_id)
    
    summary = engine.summary()
    assert summary.get("active", 0) == 1
    assert summary.get("triggered", 0) == 1
    assert summary.get("cancelled", 0) == 1
    assert summary.get("total", 0) == 3

def test_sqlite_persistence():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    try:
        engine1 = AlertEngine(db_path=path)
        engine1.add_price_alert("AAPL", "ABOVE", 200.0)
        engine1.add_volume_alert("MSFT", 10000)
        
        engine1.evaluate_tick("AAPL", 205.0, 100, 204.9, 205.1, time.time())
        
        engine2 = AlertEngine(db_path=path)
        
        assert engine2.active_count() == 1
        assert engine2.triggered_count() == 1
        
        alerts = engine2.list_alerts()
        assert len(alerts) == 2
    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
