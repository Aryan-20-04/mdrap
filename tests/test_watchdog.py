import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models import CanonicalEvent, EventType, QualityStatus, Reason
from reconciliation import ReliabilityTracker
from watchdog import SourceWatchdog, SourceState, WatchdogAlert


def _make_event(eid, source, ts, **kwargs):
    """Helper to create a CanonicalEvent with sensible defaults."""
    defaults = dict(
        event_id=eid, instrument_id='AAPL', event_type=EventType.TRADE,
        exchange_timestamp=ts, receive_timestamp=ts + 0.001,
        processing_timestamp=ts + 0.002, source=source, sequence_number=1,
        price=150.0, quantity=100.0,
    )
    defaults.update(kwargs)
    return CanonicalEvent(**defaults)


def test_healthy_sources():
    """Feed events from 3 sources within time window. All should be HEALTHY."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=2.0)

    for i, src in enumerate(['FEEDA', 'FEEDB', 'FEEDC']):
        watchdog.observe(_make_event(f'e{i}', src, 1000.0))

    states = watchdog.source_states()
    assert states['FEEDA'] == 'HEALTHY'
    assert states['FEEDB'] == 'HEALTHY'
    assert states['FEEDC'] == 'HEALTHY'


def test_silence_detection():
    """Feed events from FEEDA and FEEDB at t=1000. Feed FEEDA at t=1003. FEEDB should become SILENT."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=2.0)

    watchdog.observe(_make_event('e1', 'FEEDA', 1000.0))
    watchdog.observe(_make_event('e2', 'FEEDB', 1000.0))

    # Fast forward to 1003 for FEEDA only. FEEDB has been silent for >2s.
    alerts = watchdog.observe(_make_event('e3', 'FEEDA', 1003.0))

    states = watchdog.source_states()
    assert states['FEEDA'] == 'HEALTHY'
    assert states['FEEDB'] == 'SILENT'
    assert any(a.source == 'FEEDB' and a.alert_type == 'SILENCE' for a in alerts)


def test_degradation_detection():
    """Simulate drop in score by setting tracker._cached_scores, then verify DEGRADED state."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker, degradation_threshold=0.90)

    # First, register the source by sending an event
    watchdog.observe(_make_event('e1', 'FEEDC', 1000.0))
    assert watchdog.source_states()['FEEDC'] == 'HEALTHY'

    # Now set a low score to simulate degradation
    tracker._cached_scores['FEEDC'] = 0.5

    alerts = watchdog.observe(_make_event('e2', 'FEEDC', 1001.0))

    assert watchdog.source_states()['FEEDC'] == 'DEGRADED'
    assert any(a.alert_type == 'DEGRADATION' for a in alerts)


def test_recovery_after_silence():
    """Make a source go SILENT, then send events from it again. Verify it recovers to HEALTHY."""
    tracker = ReliabilityTracker()
    tracker._cached_scores['FEEDA'] = 1.0
    watchdog = SourceWatchdog(reliability=tracker, silence_threshold_s=2.0, recovery_threshold=0.93)

    watchdog.observe(_make_event('e1', 'FEEDA', 1000.0))

    # Advance time via another source — FEEDA goes silent
    watchdog.observe(_make_event('e2', 'FEEDB', 1003.0))
    assert watchdog.source_states()['FEEDA'] == 'SILENT'

    # Send from FEEDA again — should recover
    alerts = watchdog.observe(_make_event('e3', 'FEEDA', 1004.0))
    assert watchdog.source_states()['FEEDA'] == 'HEALTHY'
    assert any(a.alert_type == 'RECOVERY' for a in alerts)


def test_manual_block_unblock():
    """Call block_source and unblock_source, verify state and alerts."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker)

    alert = watchdog.block_source('FEEDX')
    assert watchdog.source_states()['FEEDX'] == 'BLOCKED'
    assert alert.alert_type == 'BLOCKED'
    assert alert.source == 'FEEDX'

    alert2 = watchdog.unblock_source('FEEDX')
    assert watchdog.source_states()['FEEDX'] == 'HEALTHY'
    assert alert2.alert_type == 'UNBLOCKED'
    assert alert2.source == 'FEEDX'


def test_blocked_source_skipped():
    """Block a source, feed events. Verify it stays BLOCKED and doesn't transition."""
    tracker = ReliabilityTracker()
    tracker._cached_scores['FEEDX'] = 0.5
    watchdog = SourceWatchdog(reliability=tracker)

    watchdog.block_source('FEEDX')

    watchdog.observe(_make_event('e1', 'FEEDX', 1000.0))
    assert watchdog.source_states()['FEEDX'] == 'BLOCKED'


def test_alerts_log():
    """Trigger state changes, call alerts(). Verify limit is respected."""
    tracker = ReliabilityTracker()
    watchdog = SourceWatchdog(reliability=tracker)

    watchdog.block_source('FEEDX')
    watchdog.unblock_source('FEEDX')
    watchdog.block_source('FEEDX')

    history = watchdog.alerts(limit=2)
    assert len(history) == 2


def test_active_sources():
    """Verify active_sources() returns only HEALTHY sources."""
    tracker = ReliabilityTracker()
    tracker._cached_scores['FEEDA'] = 1.0
    tracker._cached_scores['FEEDB'] = 0.5  # will degrade
    tracker._cached_scores['FEEDC'] = 1.0

    watchdog = SourceWatchdog(reliability=tracker, degradation_threshold=0.90)

    watchdog.observe(_make_event('e1', 'FEEDA', 1000.0))
    watchdog.observe(_make_event('e2', 'FEEDB', 1000.0))
    watchdog.block_source('FEEDC')

    active = watchdog.active_sources()
    assert 'FEEDA' in active
    assert 'FEEDB' not in active  # degraded
    assert 'FEEDC' not in active  # blocked
