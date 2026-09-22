import os
import pytest
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from archive import RawArchive
from models import RawEvent, QualityStatus
from pipeline import Pipeline
from security import SecurityManager
from storage import Store


def test_p1_archive_captures_all_events_before_security_gates(tmp_path):
    archive_dir = str(tmp_path / "archive")
    archive = RawArchive(archive_dir)
    store = Store(":memory:")
    
    # Configure security manager with low rate limit to trigger rejections
    sec = SecurityManager(store=store, rate_limit=1.0)
    sec.rate_limiter.capacity = 1.0
    import time
    sec.rate_limiter._buckets["FEEDX"] = (0.0, time.perf_counter())  # Force immediate rate limit reject
    
    pipeline = Pipeline(store=store, security=sec, archive=archive)
    
    raw = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "price": 100.0},
        receive_timestamp=1000.0,
        raw_id="raw-p1-1",
    )
    
    ev = pipeline.process_one(raw)
    assert ev.quality_status == QualityStatus.INVALID
    
    # The raw event MUST be in the archive even though it was rejected at the gate!
    archive.close()
    from archive import replay
    raws = list(replay(archive_dir))
    assert len(raws) == 1
    assert raws[0].raw_id == "raw-p1-1"


def test_p2_require_hmac_enforced_when_configured():
    sec = SecurityManager(require_hmac=True)
    # FEEDX requires HMAC
    assert sec.hmac_required("FEEDX") is True
    # Public exchange feeds do NOT require HMAC
    assert sec.hmac_required("BINANCE") is False
    assert sec.hmac_required("COINBASE") is False
    
    store = Store(":memory:")
    pipeline = Pipeline(store=store, security=sec)
    
    # Message without signature from FEEDX must be rejected
    raw_unsigned = RawEvent(
        source="FEEDX",
        payload={"instrument": "AAPL", "price": 100.0},
        receive_timestamp=1000.0,
        raw_id="raw-p2-1",
    )
    ev = pipeline.process_one(raw_unsigned)
    assert ev.quality_status == QualityStatus.INVALID
    assert "HMAC" in ev.reasons[0] or "SECURITY" in ev.reasons[0] or "rate" in ev.reasons[0]
