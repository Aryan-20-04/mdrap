import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from security import SecurityManager, Role, PermissionError, TokenBucketRateLimiter, InputSanitizer
from storage import Store
from pipeline import Pipeline
from models import RawEvent, QualityStatus


@pytest.fixture
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s = Store(path)
    yield s
    s.close()
    if os.path.exists(path):
        os.unlink(path)


def test_hmac_signing_and_verification():
    sec = SecurityManager()
    payload = {"instrument": "AAPL", "price": 150.25, "quantity": 100, "sequence": 1}
    sig = sec.sign_payload("FEEDX", payload)
    assert isinstance(sig, str) and len(sig) == 64
    assert sec.verify_payload("FEEDX", payload, sig) is True


def test_hmac_tampered_payload_rejected():
    sec = SecurityManager()
    payload = {"instrument": "AAPL", "price": 150.25, "quantity": 100, "sequence": 1}
    sig = sec.sign_payload("FEEDX", payload)

    # Tamper with the price
    tampered = dict(payload)
    tampered["price"] = 999.99
    assert sec.verify_payload("FEEDX", tampered, sig) is False
    assert sec.verify_payload("FEEDX", payload, "invalid_sig_12345") is False


def test_rbac_hierarchy():
    sec = SecurityManager()
    # VIEWER permitted for VIEWER actions
    sec.authorize(Role.VIEWER, Role.VIEWER, "read_bbo")
    
    # VIEWER blocked from OPERATOR actions
    with pytest.raises(PermissionError):
        sec.authorize(Role.VIEWER, Role.OPERATOR, "start_ingestion")

    # OPERATOR permitted for VIEWER and OPERATOR actions
    sec.authorize(Role.OPERATOR, Role.VIEWER, "read_bbo")
    sec.authorize(Role.OPERATOR, Role.OPERATOR, "start_ingestion")
    with pytest.raises(PermissionError):
        sec.authorize(Role.OPERATOR, Role.ADMIN, "block_feed")

    # ADMIN permitted for everything
    sec.authorize(Role.ADMIN, Role.VIEWER, "read_bbo")
    sec.authorize(Role.ADMIN, Role.OPERATOR, "start_ingestion")
    sec.authorize(Role.ADMIN, Role.ADMIN, "block_feed")


def test_token_bucket_rate_limiter():
    limiter = TokenBucketRateLimiter(rate=100.0, capacity=5.0)
    # Burst 5 tokens immediately
    for _ in range(5):
        assert limiter.allow("FEEDX", 1.0) is True

    # 6th token should be throttled
    assert limiter.allow("FEEDX", 1.0) is False

    # Reset allows again
    limiter.reset("FEEDX")
    assert limiter.allow("FEEDX", 1.0) is True


def test_input_sanitizer():
    # Valid payload
    valid, err = InputSanitizer.sanitize({"instrument": "BTC/USD", "price": 70000.0, "quantity": 1.5, "sequence": 10})
    assert valid is True
    assert err is None

    # Malformed symbol / injection attempt
    valid, err = InputSanitizer.sanitize({"instrument": "AAPL'; DROP TABLE--", "price": 150.0})
    assert valid is False
    assert "Invalid symbol format" in err

    # Negative price
    valid, err = InputSanitizer.sanitize({"instrument": "AAPL", "price": -50.0})
    assert valid is False
    assert "Price out of acceptable bounds" in err

    # Non-dict payload
    valid, err = InputSanitizer.sanitize("not a dict")
    assert valid is False


def test_tamper_evident_audit_chain(store):
    sec = SecurityManager(store=store)

    # Log sequential administrative actions
    sec.log_audit("FEED_CONNECTED", actor="operator_alice", role=Role.OPERATOR, details="FEEDX connected")
    sec.log_audit("CIRCUIT_BREAKER_TRIPPED", actor="system", role=Role.ADMIN, details="FEEDY spread spike")
    sec.log_audit("FEED_UNBLOCKED", actor="admin_bob", role=Role.ADMIN, details="FEEDY restored")

    # Verify pristine chain
    valid, msg, count = sec.verify_audit_trail()
    assert valid is True
    assert count == 3
    assert "verified" in msg.lower()

    # Tamper with the middle record in the database directly
    store.conn.execute("UPDATE audit_log SET details = 'HACKED DETAILS' WHERE entry_id = 2")
    store.conn.commit()

    # Verify tampering is immediately detected
    valid, msg, bad_id = sec.verify_audit_trail()
    assert valid is False
    assert bad_id == 2
    assert "tampered" in msg.lower() or "mismatch" in msg.lower()


def test_pipeline_with_security(store):
    sec = SecurityManager(store=store)
    pipeline = Pipeline(store=store, security=sec)

    # 1. Clean event with valid signature
    clean_payload = {"instrument": "AAPL", "event_type": "TRADE", "exchange_ts": 1000.0, "price": 150.0, "quantity": 100, "sequence": 1}
    clean_payload["signature"] = sec.sign_payload("FEEDX", clean_payload)
    raw_clean = RawEvent(source="FEEDX", payload=clean_payload, receive_timestamp=1000.001, raw_id="raw-1")

    ev1 = pipeline.process_one(raw_clean)
    assert ev1 is not None
    assert ev1.quality_status == QualityStatus.VALID

    # 2. Tampered event with forged payload
    tampered_payload = dict(clean_payload)
    tampered_payload["price"] = 9999.0  # Forged price
    raw_tampered = RawEvent(source="FEEDX", payload=tampered_payload, receive_timestamp=1000.002, raw_id="raw-2")

    ev2 = pipeline.process_one(raw_tampered)
    assert ev2 is not None
    assert ev2.quality_status == QualityStatus.INVALID
    pipeline.flush()
    quar = store.query_quarantine()
    assert len(quar) >= 1
    assert any("HMAC verification failed" in q["reasons"] for q in quar)

    # 3. Verify audit log entry was generated for HMAC failure
    audit_rows = store.query_audit_log()
    assert any(r["action"] == "HMAC_SIGNATURE_INVALID" for r in audit_rows)


def test_audit_proof_export_and_standalone_verify(store):
    """Verifies exporting a JSON audit proof and verifying it independently without database."""
    sec = SecurityManager(store=store)
    sec.log_audit("SYS_START", actor="kernel", role=Role.ADMIN, details="Node boot")
    sec.log_audit("FEED_ADD", actor="admin", role=Role.ADMIN, details="Added KRAKEN feed")
    sec.log_audit("HEARTBEAT", actor="watchdog", role=Role.OPERATOR, details="Ping OK")

    fd, proof_file = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        proof = store.export_audit_proof(proof_file)
        assert proof["total_entries"] == 3
        assert os.path.exists(proof_file)

        # Standalone verification
        valid, msg, count = Store.verify_standalone_proof(proof_file)
        assert valid is True
        assert count == 3
        assert "verified" in msg.lower()
    finally:
        if os.path.exists(proof_file):
            os.unlink(proof_file)


def test_audit_proof_standalone_tamper_detection(store):
    """Verifies that tampering with an exported JSON proof file is detected."""
    import json
    sec = SecurityManager(store=store)
    sec.log_audit("INIT", actor="system", role=Role.ADMIN, details="System genesis")
    sec.log_audit("TRADE", actor="bot", role=Role.OPERATOR, details="Executed order")

    fd, proof_file = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        store.export_audit_proof(proof_file)

        # Modify entry details in the JSON proof directly
        with open(proof_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["entries"][1]["details"] = "FORGED ORDER DETAILS"
        with open(proof_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # Standalone verify should fail
        valid, msg, bad_id = Store.verify_standalone_proof(proof_file)
        assert valid is False
        assert bad_id == 2
        assert "tampered" in msg.lower() or "mismatch" in msg.lower()
    finally:
        if os.path.exists(proof_file):
            os.unlink(proof_file)


def test_env_var_secret_loading():
    """Verifies secrets can be loaded from MDRAP_SECRET_<SOURCE> environment variables."""
    os.environ["MDRAP_SECRET_MYFEED"] = "super_secret_env_key_123"
    try:
        sec = SecurityManager()
        payload = {"instrument": "BTC/USD", "price": 80000.0}
        sig = sec.sign_payload("MYFEED", payload)
        assert sec.verify_payload("MYFEED", payload, sig) is True
    finally:
        os.environ.pop("MDRAP_SECRET_MYFEED", None)

