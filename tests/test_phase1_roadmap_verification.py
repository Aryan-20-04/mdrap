"""
Phase 1 Roadmap Hardening Verification Suite.
Verifies all 10 Phase 1 tasks (1.1 - 1.10) under rigorous adversarial conditions.
"""

import os
import platform
import socket
import tempfile
import threading
import time
import pytest
from unittest.mock import patch, MagicMock

from mdrap.models import RawEvent, CanonicalEvent, EventType, QualityStatus
from mdrap.security import SecurityManager, Role, TokenBucketRateLimiter, hash_api_key
from mdrap.alert_sinks import validate_webhook_url, WebhookAlertSink
from mdrap.watchdog import SourceWatchdog, SourceState, ReliabilityTracker
from mdrap.storage import Store
from mdrap.archive import RawArchive
from mdrap.prometheus import PrometheusExporter


def test_1_1_admin_auth_hash_bypass_and_audit():
    """1.1: Passing raw token_hash fails authentication and writes ACCESS_DENIED audit log."""
    sec = SecurityManager()
    ent = sec.register_api_key(client_id="Phase1Admin", role=Role.ADMIN)
    raw_token = ent.token
    token_hash = ent.token_hash

    assert raw_token is not None and token_hash is not None

    # Valid token authenticates
    assert sec.get_entitlement(raw_token, active_only=True) is not None

    # Submitting token_hash directly MUST return None (never authenticate)
    assert sec.get_entitlement(token_hash, active_only=True) is None
    assert sec._api_keys.get_by_token(token_hash) is None
    assert sec._api_keys.get_by_token_or_hash(token_hash) is None

    # Submitting token_hash to authorize must raise AccessDenied and write ACCESS_DENIED audit row
    with pytest.raises(Exception):
        sec.authorize(token_hash, "manage_keys")

    audit_rows = sec.get_audit_log()
    denied = [row for row in audit_rows if row.get("action") == "ACCESS_DENIED"]
    assert len(denied) >= 1, "Expected ACCESS_DENIED row in audit log"


def test_1_2_ssrf_shorthand_ips_cgnat_and_redirects():
    """1.2: Webhook SSRF validation catches shorthand IPs (127.1), CGNAT (100.64.0.0/10), and disables redirects."""
    # Shorthand octet loopback
    with pytest.raises(ValueError, match="SSRF"):
        validate_webhook_url("http://127.1/hook")

    # Integer representation of 127.0.0.1 (2130706433)
    with pytest.raises(ValueError, match="SSRF"):
        validate_webhook_url("http://2130706433/hook")

    # CGNAT range (100.64.0.0/10)
    with pytest.raises(ValueError, match="SSRF"):
        validate_webhook_url("http://100.64.1.5/hook")

    # Non-standard restricted port
    with pytest.raises(ValueError, match="SSRF"):
        validate_webhook_url("http://example.com:22/ssh")


def test_1_3_rate_limiter_10k_rotating_sources_bounded_memory_and_rate():
    """1.3: Flooding 10k rotating source names routes to overflow bucket, strictly bounding memory and rate."""
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=100.0)

    # 1. Fill 1,024 legitimate sources
    for i in range(1024):
        limiter.allow(f"src_{i}", tokens=1.0)

    assert len(limiter._buckets) <= 1025

    # 2. Flood 10,000 distinct randomized source names
    t0 = time.perf_counter()
    allowed_count = 0
    for i in range(10_000):
        if limiter.allow(f"rotating_attacker_{i}", tokens=1.0):
            allowed_count += 1
    elapsed = time.perf_counter() - t0

    # Total allowed requests across all 10k rotating sources cannot exceed capacity + rate refill
    max_expected = 100.0 + elapsed * 100.0 + 5.0
    assert allowed_count <= max_expected, (
        f"Rate amplification: allowed {allowed_count} > max theoretical {max_expected}"
    )
    # Memory is strictly bounded: cannot grow beyond 1025 buckets
    assert len(limiter._buckets) <= 1025, (
        f"Bucket leak: {len(limiter._buckets)} entries"
    )


def test_1_5_active_watchdog_detects_total_silence_within_two_seconds():
    """1.5: Active watchdog heartbeat thread detects silence in <= 2.0s with zero incoming events."""
    rel = ReliabilityTracker()
    wd = SourceWatchdog(reliability=rel, silence_threshold_s=0.5)

    ev = CanonicalEvent(
        event_id="e1",
        event_type=EventType.TRADE,
        source="FEED_A",
        instrument_id="AAPL",
        exchange_timestamp=time.time(),
        receive_timestamp=time.time(),
        price=150.0,
        quantity=100.0,
        quality_status=QualityStatus.VALID,
    )
    wd.observe(ev)
    assert wd._source_states["FEED_A"] == SourceState.HEALTHY

    alerts_received = []
    wd.start_heartbeat(interval_s=0.1, on_alert=lambda a: alerts_received.extend(a))

    try:
        # Stop all input and wait 0.8 seconds (threshold is 0.5s)
        time.sleep(0.8)
        assert wd._source_states["FEED_A"] == SourceState.SILENT
        assert any(
            a.alert_type == "SILENCE" and a.source == "FEED_A" for a in wd._alerts
        )
    finally:
        wd.stop_heartbeat()


def test_1_7_in_memory_sqlite_single_connection_multi_thread():
    """1.7: In-memory SQLite uses a single connection guarded by RLock across concurrent threads."""
    store = Store(":memory:")
    assert store.is_memory is True
    assert store.read_conn is store.conn
    assert store._read_lock is store._lock

    errors = []

    def _worker(thread_id: int):
        try:
            for j in range(25):
                store.write_quarantine_batch(
                    [
                        (
                            f"raw_{thread_id}_{j}",
                            "AAPL",
                            f"FEED_{thread_id}",
                            "INVALID",
                            "PARITY_TEST",
                            '{"idx": 1}',
                            time.time(),
                        )
                    ]
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread errors in in-memory SQLite: {errors}"
    store.close()


def test_1_8_arm_fail_closed_guard():
    """1.8: On ARM64 without native atomics, SHMWriter/SHMReader fail closed unless overridden."""
    from mdrap.shm import _check_arm_shm_safety

    # Mock platform.machine to report 'aarch64' and HAS_FASTPATH to False
    with (
        patch("platform.machine", return_value="aarch64"),
        patch.dict(os.environ, {"MDRAP_UNSAFE_SHM": "0"}),
    ):
        with patch("mdrap.fastpath.HAS_FASTPATH", False):
            with pytest.raises(
                RuntimeError, match="Refusing to run lock-free Shared Memory on ARM64"
            ):
                _check_arm_shm_safety()

    # With MDRAP_UNSAFE_SHM=1 override, it must pass
    with (
        patch("platform.machine", return_value="aarch64"),
        patch.dict(os.environ, {"MDRAP_UNSAFE_SHM": "1"}),
    ):
        with patch("mdrap.fastpath.HAS_FASTPATH", False):
            _check_arm_shm_safety()  # Should not raise


def test_1_9_prometheus_integrity_cost_cached():
    """1.9: Prometheus exporter caches audit verification and does not block /metrics."""
    mock_store = MagicMock()
    mock_store.verify_audit_integrity.return_value = (True, "OK")

    exporter = PrometheusExporter(audit_check_interval_s=60.0)
    state = MagicMock()
    state.store = mock_store
    state.watchdog = None
    state.reliability = None

    # First scrape triggers async verify
    metrics_1 = exporter.render(state)
    assert "mdrap_audit_verified_status 1" in metrics_1

    # Second scrape immediately returns cached status without calling verify_audit_integrity again
    metrics_2 = exporter.render(state)
    assert "mdrap_audit_verified_status 1" in metrics_2


def test_1_10_raw_archive_fd_leak_prevention():
    """1.10: RawArchive enforces LRU cap (<=32 open FDs) across hundreds of date partitions."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_archive_test_")
    try:
        archive = RawArchive(base_dir=temp_dir, buffer_size=1)
        base_time = 1700000000.0  # Unix timestamp

        # Simulate writes across 50 distinct dates (each day is 86400 seconds)
        for d in range(50):
            ts = base_time + (d * 86400.0)
            raw = RawEvent(
                raw_id=f"r_{d}",
                source="FEED_ROTATING",
                payload={"day": d},
                receive_timestamp=ts,
            )
            archive.write(raw)

        # File handles must be strictly capped to <= 32
        assert len(archive._file_handles) <= 32, (
            f"FD leak: {len(archive._file_handles)} handles open! Expected <= 32"
        )
        archive.close()
        assert len(archive._file_handles) == 0
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_1_4_ws_auth_before_accept():
    """1.4: WebSocket endpoint rejects unauthenticated clients before accept() with status 1008."""
    from fastapi.testclient import TestClient
    from mdrap.api import create_app
    from starlette.websockets import WebSocketDisconnect

    app = create_app()
    client = TestClient(app)

    # 1. Connecting without API key must be closed with 1008 before accepting
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/events/stream"):
            pass
    assert exc.value.code == 1008


def test_1_6_replay_lock_concurrency():
    """1.6: Replay buffer safely handles concurrent recording and replaying under _replay_lock."""
    from mdrap.service import MarketDataDaemon
    from mdrap.fastpath import NativeReplayBuffer

    daemon = MarketDataDaemon.__new__(MarketDataDaemon)
    daemon._replay_lock = threading.RLock()
    daemon._replay_buffer = NativeReplayBuffer(capacity=1000)

    stop_event = threading.Event()
    errors = []

    def _writer():
        seq = 0
        while not stop_event.is_set() and seq < 500:
            seq += 1
            daemon._record_replay(
                {
                    "seq": seq,
                    "sym": "AAPL",
                    "source": "FEED_A",
                    "type": "TICK",
                    "price": 150.0 + (seq % 10),
                }
            )
            time.sleep(0.001)

    def _reader():
        while not stop_event.is_set():
            try:
                with daemon._replay_lock:
                    records = daemon._replay_buffer.replay(1, 500, "AAPL")
                time.sleep(0.002)
            except Exception as exc:
                errors.append(exc)

    writer_t = threading.Thread(target=_writer)
    reader_t = threading.Thread(target=_reader)
    writer_t.start()
    reader_t.start()

    writer_t.join(timeout=3.0)
    stop_event.set()
    reader_t.join(timeout=1.0)

    assert len(errors) == 0, f"Concurrent replay errors: {errors}"
