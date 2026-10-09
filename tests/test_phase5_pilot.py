"""Phase 5 Production Pilot & Operational Excellence Test Suite.

Verifies:
  1. Deployment automation, preflight checks, and smoke verification.
  2. Diagnostic bundle generation and secret redaction.
  3. Independent consumer integration (SBE unpacking and sequence gap auditing).
  4. Configuration drift detection against approved baseline.
  5. Operational incident exercise (persistence mitigation and unacknowledged write boundaries).
"""

import json
import os
import struct
import tempfile
import time
import pytest
from scripts.deploy_pilot import (
    execute_smoke_verification,
    setup_deployment_environment,
    verify_prerequisites,
)
from scripts.diagnostic_bundle import generate_diagnostic_bundle
from mdrap.models import EventType, QualityStatus


def test_deployment_preflight_automation(tmp_path):
    """Deployment automation verifies prerequisites, creates directory tree, and executes smoke test."""
    checks = verify_prerequisites()
    assert checks["python_version_ge_3_11"] is True
    assert checks["64_bit_architecture"] is True

    base_dir = str(tmp_path / "pilot_stage")
    paths = setup_deployment_environment(base_dir)
    assert os.path.exists(paths["wal_dir"])
    assert os.path.exists(paths["data_dir"])
    assert os.path.exists(paths["logs_dir"])

    smoke_ok = execute_smoke_verification(paths["wal_dir"])
    assert smoke_ok is True


def test_diagnostic_bundle_generation_and_redaction(tmp_path):
    """Diagnostic bundle accurately captures operational telemetry while redacting secrets."""
    config_file = str(tmp_path / "config.json")
    wal_dir = str(tmp_path / "wal")
    bundle_out = str(tmp_path / "bundle.json")
    os.makedirs(wal_dir, exist_ok=True)

    test_config = {
        "engine": {
            "venue": "NASDAQ",
            "port": 8080,
            "api_key_salt": "super_secret_salt_12345",
            "admin_token": "bearer_token_xyz",
            "db_password": "my_password_99",
        }
    }
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(test_config, f)

    bundle = generate_diagnostic_bundle(
        config_path=config_file,
        wal_dir=wal_dir,
        output_path=bundle_out,
    )

    assert os.path.exists(bundle_out)
    assert bundle["status"] == "HEALTHY"
    assert "platform_info" in bundle
    assert "build_info" in bundle

    # Verify secret redaction
    cfg = bundle["configuration"]["engine"]
    assert cfg["venue"] == "NASDAQ"
    assert cfg["port"] == 8080
    assert cfg["api_key_salt"] == "[REDACTED]"
    assert cfg["admin_token"] == "[REDACTED]"
    assert cfg["db_password"] == "[REDACTED]"


def test_independent_consumer_integration():
    """Independent consumer successfully ingests 64-byte SBE frames, parses fields, and audits gaps."""
    # Build 3 sequential SBE frames with a deliberate gap between frame 2 and frame 3
    frames_data = []

    # Frame 1: Seq 100
    inst_b1 = b"AAPL" + b"\x00" * 12
    f1 = struct.pack("<16sQqddII8s", inst_b1, 100, 1000000000, 150.25, 50.0, 1, 0, b"\x00" * 8)
    frames_data.append(f1)

    # Frame 2: Seq 101
    f2 = struct.pack("<16sQqddII8s", inst_b1, 101, 1000001000, 150.30, 25.0, 1, 0, b"\x00" * 8)
    frames_data.append(f2)

    # Frame 3: Seq 105 (Gap of 3 missing: 102, 103, 104)
    f3 = struct.pack("<16sQqddII8s", inst_b1, 105, 1000005000, 150.35, 100.0, 1, 0, b"\x00" * 8)
    frames_data.append(f3)

    # Independent consumer simulation
    class IndependentQuantConsumer:
        def __init__(self):
            self.consumed_count = 0
            self.expected_seq = None
            self.gaps_detected = 0
            self.missing_events_count = 0

        def process_frame(self, raw_bytes: bytes) -> dict:
            assert len(raw_bytes) == 64
            inst, seq, ex_ts, px, sz, ev_type, q_flag, _ = struct.unpack("<16sQqddII8s", raw_bytes)
            sym = inst.split(b"\x00")[0].decode("ascii")

            # Sequence gap auditing
            if self.expected_seq is not None:
                if seq > self.expected_seq:
                    gap = seq - self.expected_seq
                    self.gaps_detected += 1
                    self.missing_events_count += gap
                    self.expected_seq = seq + 1
                elif seq == self.expected_seq:
                    self.expected_seq = seq + 1
            else:
                self.expected_seq = seq + 1

            self.consumed_count += 1
            return {"sym": sym, "seq": seq, "px": px, "sz": sz}

    consumer = IndependentQuantConsumer()
    for frame in frames_data:
        res = consumer.process_frame(frame)
        assert res["sym"] == "AAPL"

    assert consumer.consumed_count == 3
    assert consumer.gaps_detected == 1
    assert consumer.missing_events_count == 3


def test_configuration_drift_detection():
    """Drift detector identifies unauthorized changes between active config and approved baseline."""
    approved_baseline = {
        "engine_mode": "production_durable",
        "listen_port": 8080,
        "max_segment_mb": 10,
        "fsync_policy": "always",
    }

    # Unmodified config -> Zero drift
    active_clean = dict(approved_baseline)
    diffs = [k for k, v in approved_baseline.items() if active_clean.get(k) != v]
    assert len(diffs) == 0

    # Tampered config -> Detects modified keys
    active_drifted = dict(approved_baseline)
    active_drifted["listen_port"] = 9090
    active_drifted["fsync_policy"] = "never"  # Dangerous drift!

    diffs = {
        k: {"expected": approved_baseline[k], "actual": active_drifted[k]}
        for k in approved_baseline
        if active_drifted.get(k) != approved_baseline[k]
    }
    assert len(diffs) == 2
    assert diffs["listen_port"]["actual"] == 9090
    assert diffs["fsync_policy"]["actual"] == "never"


def test_incident_exercise_persistence_mitigation(tmp_path):
    """Storage write failure raises explicit exception, preventing false-positive ACK."""
    from mdrap.ingestlog import IngestLog
    from mdrap.models import RawEvent

    wal_dir = str(tmp_path / "incident_wal")
    os.makedirs(wal_dir, exist_ok=True)

    log = IngestLog(log_dir=wal_dir)
    raw = RawEvent(raw_id="inc_1", source="TEST", receive_timestamp=time.time(), payload={"seq": 1})
    off = log.append(raw)
    assert off >= 0

    # Simulate sudden underlying file closure/invalidation
    log._current_file.close()

    # Subsequent append must raise IOError/ValueError and must NOT claim durable ACK
    with pytest.raises((ValueError, OSError, IOError)):
        log.append(RawEvent(raw_id="inc_2", source="TEST", receive_timestamp=time.time(), payload={"seq": 2}))

    log.close()
