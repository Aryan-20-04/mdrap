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


def test_sanitizer_rejects_boolean_values():
    """Verifies that booleans are strictly rejected as numeric field values."""
    for field_name in ["price", "bid", "ask", "quantity", "sequence"]:
        valid, err = InputSanitizer.sanitize({field_name: True})
        assert valid is False, f"Expected {field_name}=True to be rejected"
        assert err is not None

        valid, err = InputSanitizer.sanitize({field_name: False})
        assert valid is False, f"Expected {field_name}=False to be rejected"
        assert err is not None


def test_rate_limiter_multithreaded_concurrency():
    """Verifies thread-safe token bucket consumption under concurrent access."""
    import threading
    limiter = TokenBucketRateLimiter(rate=0.0, capacity=100.0)
    allowed_count = [0]
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if limiter.allow("MULTI", 1.0):
                with lock:
                    allowed_count[0] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Total attempts = 200, capacity is 100, exactly 100 allowed without refill race
    assert allowed_count[0] == 100


def test_env_secrets_resolution():
    """Verifies dynamic resolution of API keys and feed secrets from environment."""
    os.environ["MDRAP_SECRET_BINANCE"] = "custom_binance_secret_xyz"
    os.environ["MDRAP_API_KEY_PRO"] = "custom_pro_token_abc"
    try:
        sec = SecurityManager()
        assert sec._secrets["BINANCE"] == b"custom_binance_secret_xyz"
        assert "custom_pro_token_abc" in sec._api_keys
        assert sec._api_keys["custom_pro_token_abc"].can_access_l2 is True
    finally:
        os.environ.pop("MDRAP_SECRET_BINANCE", None)
        os.environ.pop("MDRAP_API_KEY_PRO", None)


def test_audit_hash_delimiter_collision_resistance(store):
    """Verifies pipe delimiter escaping prevents audit hash collision/injection."""
    sec = SecurityManager(store=store)
    sec.log_audit("TEST_DELIM", actor="admin|injected", role=Role.ADMIN, details="field|injected|payload")
    valid, msg, count = sec.verify_audit_trail()
    assert valid is True
    assert count == 1


def test_hmac_compact_json_compatibility():
    """Verifies HMAC signature uses compact JSON separators matching cross-language standards."""
    import hashlib
    import hmac
    sec = SecurityManager()
    sec.register_feed_secret("TESTFEED", "secret123")
    payload = {"b": 2, "a": 1}
    sig = sec.sign_payload("TESTFEED", payload)

    # Standard compact JSON without whitespace
    raw_json = '{"a":1,"b":2}'.encode("utf-8")
    expected = hmac.new(b"secret123", raw_json, hashlib.sha256).hexdigest()
    assert sig == expected
    assert sec.verify_payload("TESTFEED", payload, expected) is True


