import math
import time
import pytest
from security import (
    AccessDenied,
    InputSanitizer,
    Role,
    SecurityManager,
    ClientEntitlement,
    Tier,
)
from storage import Store


def test_s3_unknown_source_sign_payload():
    sm = SecurityManager(require_env_secrets=False)
    with pytest.raises(KeyError, match="Unknown feed source"):
        sm.sign_payload("UNKNOWN_FEED_123", {"price": 100.0})

    sec = sm.create_feed_secret("NEW_FEED")
    assert isinstance(sec, str)
    sig = sm.sign_payload("NEW_FEED", {"price": 100.0})
    assert isinstance(sig, str)
    assert sm.verify_payload("NEW_FEED", {"price": 100.0}, sig) is True


def test_s5_key_expiration_and_inactive():
    sm = SecurityManager()
    now = time.time()
    # Active, not expired
    sm.register_api_key("client1", token="tok_active", expires_at=now + 3600)
    assert sm.get_entitlement("tok_active") is not None

    # Inactive
    sm.register_api_key("client2", token="tok_inactive")
    sm.revoke_api_key("tok_inactive")
    assert sm.get_entitlement("tok_inactive") is None

    # Expired
    sm.register_api_key("client3", token="tok_expired", expires_at=now - 10)
    assert sm.get_entitlement("tok_expired") is None


def test_s7_access_denied_hierarchy_and_audit():
    store = Store(":memory:")
    sm = SecurityManager(store=store)

    assert issubclass(AccessDenied, PermissionError)

    # VIEWER cannot do OPERATOR action
    with pytest.raises(AccessDenied):
        sm.authorize(Role.VIEWER, Role.OPERATOR, action_name="start_ingest")

    # OPERATOR can do VIEWER action
    sm.authorize(Role.OPERATOR, Role.VIEWER, action_name="read_bbo")

    # Token authorization
    sm.register_api_key("client_op", token="tok_op")
    ent = sm.get_entitlement("tok_op")
    ent.role = Role.OPERATOR
    sm.authorize(ent, Role.VIEWER, action_name="read_status")

    with pytest.raises(AccessDenied):
        sm.authorize("invalid_token", Role.VIEWER, action_name="read_status")


def test_s8_sanitizer_bounds_and_symbols():
    # OCC Option symbol (with spaces)
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL  240119C00150000", "price": 5.25})
    assert ok is True, f"Failed OCC symbol: {err}"

    # Symbol with special chars ES=F, ETH/USDT:USDT
    ok, err = InputSanitizer.sanitize({"instrument": "ES=F", "price": 4500.0})
    assert ok is True
    ok, err = InputSanitizer.sanitize({"instrument": "ETH/USDT:USDT", "price": 2500.0})
    assert ok is True

    # Trailing newline rejected
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL\n", "price": 150.0})
    assert ok is False

    # NaN price rejected
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL", "price": float("nan")})
    assert ok is False

    # Inf price rejected
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL", "price": float("inf")})
    assert ok is False

    # Wide crypto price supported
    ok, err = InputSanitizer.sanitize({"instrument": "BTC/KRW", "price": 95_000_000.0})
    assert ok is True, f"Failed high price: {err}"

    # Absurd price > 1e15 rejected
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL", "price": 1e16})
    assert ok is False

    # Sequence > 2**63 - 1 rejected
    ok, err = InputSanitizer.sanitize({"instrument": "AAPL", "price": 100.0, "sequence": 2**64})
    assert ok is False


def test_s11_append_audit_atomicity():
    store = Store(":memory:")
    sm = SecurityManager(store=store)
    h1 = sm.log_audit("LOGIN", actor="user1", role=Role.ADMIN, details="test1")
    h2 = sm.log_audit("LOGOUT", actor="user1", role=Role.ADMIN, details="test2")
    assert h1 != h2

    valid, msg, count = store.verify_audit_integrity()
    assert valid is True
    assert count == 2


def test_s12_audit_anchor_and_standalone_verification():
    store = Store(":memory:")
    sm = SecurityManager(store=store)
    h1 = sm.log_audit("OP1", actor="admin", role=Role.ADMIN)
    h2 = sm.log_audit("OP2", actor="admin", role=Role.ADMIN)

    # Valid with correct anchor
    ok, msg, cnt = store.verify_audit_integrity(anchor=(2, h2))
    assert ok is True

    # Tail truncation detection
    ok, msg, cnt = store.verify_audit_integrity(anchor=(3, "fake_future_hash"))
    assert ok is False
    assert "truncation" in msg.lower() or "mismatch" in msg.lower()

    # Standalone proof verification
    proof = store.export_audit_proof()
    ok, msg, cnt = Store.verify_standalone_proof(proof, anchor=(2, h2))
    assert ok is True

    # Tampered latest_hash in proof
    tampered_proof = dict(proof)
    tampered_proof["latest_hash"] = "tampered_hash"
    ok, msg, cnt = Store.verify_standalone_proof(tampered_proof)
    assert ok is False

    # Empty proof without anchor
    empty_proof = {
        "version": "2.0.0",
        "total_entries": 0,
        "latest_hash": "GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
        "entries": [],
    }
    ok, msg, cnt = Store.verify_standalone_proof(empty_proof)
    assert ok is False
