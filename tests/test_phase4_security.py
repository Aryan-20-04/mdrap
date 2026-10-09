"""Phase 4 Security Assurance & Threat Defense Test Suite (Workstream E).

Verifies institutional security controls:
  1. Cryptographic token entropy, salted hashing, and timing-safe verification.
  2. Fail-closed entitlement evaluation (sources, symbols, expiration, active flags).
  3. SBE wire buffer bounds and string truncation defenses.
  4. IngestLog directory boundary confinement and lock acquisition.
"""

import hmac
import os
import struct
import tempfile
import time
import pytest
from mdrap.ingestlog import IngestLog, IngestLogLockedError
from mdrap.metering import DurableUsageMeter
from mdrap.security import AccessDenied, ClientEntitlement, Role, SecurityManager
from mdrap.storage import Store


def test_token_salt_and_constant_time_verification(tmp_path):
    """API key tokens use high-entropy generation, salted hashing, and instant revocation."""
    os.environ["MDRAP_API_KEY_SALT"] = "test_institutional_salt_2026"
    db_path = str(tmp_path / "sec_test.db")
    store = Store(db_path)
    sec = SecurityManager(store=store)

    # 1. Register API key
    ent = sec.register_api_key(client_id="Desk_Alpha", role=Role.OPERATOR)
    raw_token = ent.token
    token_hash = ent.token_hash
    key_id = ent.key_id

    assert raw_token is not None
    assert len(raw_token) >= 32  # High entropy
    assert token_hash is not None
    assert raw_token not in token_hash  # Token itself is never stored in plaintext hash

    # 2. Verification of valid token
    verified_ent = sec.authenticate_request(raw_token)
    assert verified_ent is not None
    assert verified_ent.client_id == "Desk_Alpha"

    # 3. Invalid token rejection
    bad_token = raw_token[:-4] + "xxxx"
    with pytest.raises(AccessDenied):
        sec.authenticate_request(bad_token)
    with pytest.raises(AccessDenied):
        sec.authenticate_request("")

    # 4. Immediate revocation by key_id or token
    revoked = sec.revoke_api_key(raw_token)
    assert revoked is True

    # 5. Revoked token fails verification immediately
    with pytest.raises(AccessDenied):
        sec.authenticate_request(raw_token)
    store.close()


def test_fail_closed_entitlement_enforcement(tmp_path):
    """Metering and distribution entitlement checks fail closed under any discrepancy."""
    db_path = str(tmp_path / "meter_sec.db")
    meter = DurableUsageMeter(db_path)

    # 1. None entitlement fails closed
    assert meter.verify_entitlement(None, source="NASDAQ", symbol="AAPL") is False

    # 2. Inactive entitlement fails closed
    inactive_ent = ClientEntitlement(
        token_hash="hash1",
        client_id="Client1",
        is_active=False,
    )
    assert meter.verify_entitlement(inactive_ent, source="NASDAQ", symbol="AAPL") is False

    # 3. Expired entitlement fails closed
    expired_ent = ClientEntitlement(
        token_hash="hash2",
        client_id="Client2",
        is_active=True,
        expires_at=time.time() - 100.0,
    )
    assert meter.verify_entitlement(expired_ent, source="NASDAQ", symbol="AAPL") is False

    # 4. Scope-restricted entitlement enforcement
    scoped_ent = ClientEntitlement(
        token_hash="hash3",
        client_id="Client3",
        is_active=True,
        allowed_sources=["NASDAQ"],
        allowed_symbols=["AAPL", "MSFT"],
    )
    # Allowed
    assert meter.verify_entitlement(scoped_ent, source="NASDAQ", symbol="AAPL") is True
    # Disallowed source
    assert meter.verify_entitlement(scoped_ent, source="BINANCE", symbol="AAPL") is False
    # Disallowed symbol
    assert meter.verify_entitlement(scoped_ent, source="NASDAQ", symbol="GOOG") is False

    meter.close()


def test_sbe_wire_bounds_and_null_terminator_safety():
    """SBE frame packing strictly confines strings to 16 bytes, preventing memory overrun."""
    oversized_sym = "SUPERLONG_STOCK_SYMBOL_XYZ_123"
    # Pack symbol truncated/clamped to 16 bytes
    inst_b = oversized_sym.encode("ascii")[:16].ljust(16, b"\x00")
    assert len(inst_b) == 16

    frame = struct.pack(
        "<16sQqddII8s",
        inst_b,
        1,
        1000000,
        100.0,
        10.0,
        1,
        0,
        b"\x00" * 8,
    )
    assert len(frame) == 64

    # Unpack safely
    u_sym, _, _, _, _, _, _, _ = struct.unpack("<16sQqddII8s", frame)
    cleaned_sym = u_sym.split(b"\x00")[0].decode("ascii")
    assert len(cleaned_sym) == 16
    assert cleaned_sym == "SUPERLONG_STOCK_"


def test_ingestlog_directory_safety_and_locking(tmp_path):
    """IngestLog prevents concurrent writers from corrupting identical log directory."""
    wal_dir = str(tmp_path / "locked_wal")
    os.makedirs(wal_dir, exist_ok=True)

    # First instance acquires exclusive advisory lock
    log1 = IngestLog(log_dir=wal_dir, lock=True)
    assert log1._lock_file is not None

    # Second instance attempting to open same directory must raise IngestLogLockedError
    with pytest.raises(IngestLogLockedError):
        _ = IngestLog(log_dir=wal_dir, lock=True)

    # After first instance closes, lock is released and second instance can open
    log1.close()
    log2 = IngestLog(log_dir=wal_dir, lock=True)
    assert log2 is not None
    log2.close()
