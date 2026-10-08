"""
Phase 12: Watchdog Subsystem Tests (OPS-01).
Validates:
- Active check_silence() detects total feed silence across all sources when zero events arrive.
- Active background heartbeat thread continuously monitors silence and invokes alert callbacks.
- Thread-safe concurrency between feed ingestion threads and watchdog heartbeat.
- Graceful start and stop of heartbeat worker.
"""

import threading
import time
from unittest.mock import MagicMock
import pytest

from mdrap.models import CanonicalEvent, EventType, QualityStatus
from mdrap.reconciliation import ReliabilityTracker
from mdrap.watchdog import SourceWatchdog, SourceState


def _make_event(eid: str, source: str, ts: float) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=eid,
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002,
        source=source,
        sequence_number=1,
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id=f"r_{eid}",
    )


def test_ops_01_total_feed_silence_detected_by_check_silence():
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=2.0)

    # Ingest events from two sources at t=1000
    watchdog.observe(_make_event("e1", "FEED_A", 1000.0))
    watchdog.observe(_make_event("e2", "FEED_B", 1000.0))

    assert watchdog.source_states()["FEED_A"] == "HEALTHY"
    assert watchdog.source_states()["FEED_B"] == "HEALTHY"

    # Total feed silence: neither FEED_A nor FEED_B sends any events for 3.0s
    # In the old passive implementation, observe() was never called, so silence was never detected.
    alerts = watchdog.check_silence(current_time=1003.0)

    # Both feeds must be marked SILENT
    assert len(alerts) == 2
    alert_sources = {a.source for a in alerts}
    assert alert_sources == {"FEED_A", "FEED_B"}
    assert all(a.alert_type == "SILENCE" for a in alerts)

    states = watchdog.source_states()
    assert states["FEED_A"] == "SILENT"
    assert states["FEED_B"] == "SILENT"
    assert watchdog.active_sources() == []


def test_ops_01_background_heartbeat_timer_and_callback():
    tracker = ReliabilityTracker()
    # Fast silence threshold for quick test (0.1s)
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=0.1)

    t0 = time.time()
    watchdog.observe(_make_event("e1", "LIVE_FEED", t0))
    assert watchdog.source_states()["LIVE_FEED"] == "HEALTHY"

    captured_alerts = []
    alert_event = threading.Event()

    def callback(alerts):
        captured_alerts.extend(alerts)
        alert_event.set()

    # Start heartbeat running every 50ms
    watchdog.start_heartbeat(interval_s=0.05, on_alert=callback)

    try:
        # Wait up to 1.0s for the heartbeat thread to detect silence without any new events arriving
        triggered = alert_event.wait(timeout=1.0)
        assert triggered, "Heartbeat failed to trigger silence alert within timeout"

        assert len(captured_alerts) >= 1
        assert captured_alerts[0].source == "LIVE_FEED"
        assert captured_alerts[0].alert_type == "SILENCE"
        assert watchdog.source_states()["LIVE_FEED"] == "SILENT"
    finally:
        watchdog.stop_heartbeat()

    # Verify heartbeat thread stopped cleanly
    assert watchdog._heartbeat_thread is None
    assert watchdog._heartbeat_stop is None


def test_ops_01_concurrent_observe_and_check_silence():
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=1.0)

    # Register initial sources
    for s in ["FEED_1", "FEED_2", "FEED_3"]:
        watchdog.observe(_make_event("init", s, time.time()))

    stop_flag = threading.Event()
    errors = []

    def ingester(source_name):
        seq = 0
        while not stop_flag.is_set():
            try:
                seq += 1
                ev = _make_event(f"{source_name}_{seq}", source_name, time.time())
                watchdog.observe(ev)
                time.sleep(0.002)
            except Exception as e:
                errors.append(e)

    def poller():
        while not stop_flag.is_set():
            try:
                watchdog.check_silence()
                _ = watchdog.source_states()
                _ = watchdog.active_sources()
                time.sleep(0.003)
            except Exception as e:
                errors.append(e)

    threads = [
        threading.Thread(target=ingester, args=("FEED_1",)),
        threading.Thread(target=ingester, args=("FEED_2",)),
        threading.Thread(target=poller),
    ]

    for t in threads:
        t.start()

    time.sleep(0.1)
    stop_flag.set()

    for t in threads:
        t.join()

    assert not errors, f"Concurrent watchdog execution failed with errors: {errors}"
