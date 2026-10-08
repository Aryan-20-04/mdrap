"""
Tests for Institutional Security, CIDR Filtering, Key Rotation, and Merkle Anchoring (src/security.py).
"""

import time
import pytest
from mdrap.security import (
    AccessDenied,
    CIDRFilter,
    ClientEntitlement,
    MerkleAuditTree,
    Role,
    SecurityManager,
)
from mdrap.storage import Store


def test_cidr_filter_ipv4_and_ipv6():
    """Verify CIDR filter correctly checks IPv4 and IPv6 subnet memberships."""
    filt = CIDRFilter(["192.168.1.0/24", "10.0.0.0/8", "::1/128", "127.0.0.1/32"])

    # Permitted IPs
    assert filt.matches("192.168.1.50") is True
    assert filt.matches("10.254.1.1") is True
    assert filt.matches("127.0.0.1") is True
    assert filt.matches("::1") is True

    # Denied IPs
    assert filt.matches("192.168.2.1") is False
    assert filt.matches("172.16.0.1") is False
    assert filt.matches("8.8.8.8") is False
    assert filt.matches("invalid_ip") is False
    assert filt.matches("") is False


def test_merkle_audit_tree_root_and_inclusion_proof():
    """Verify Merkle tree root calculation, RFC 6962 domain separation, and inclusion proofs."""
    leaves = [f"audit_hash_{i}_{i * 7}" for i in range(7)]

    # Compute root
    root = MerkleAuditTree.compute_root(leaves)
    assert isinstance(root, str)
    assert len(root) == 64  # SHA256 hex string

    # Determinism
    assert MerkleAuditTree.compute_root(leaves) == root

    # Single leaf
    single_root = MerkleAuditTree.compute_root(["only_one"])
    assert single_root == MerkleAuditTree.hash_leaf("only_one")

    # Generate and verify proof for every leaf
    for idx, leaf in enumerate(leaves):
        proof = MerkleAuditTree.generate_proof(leaves, idx)
        assert MerkleAuditTree.verify_proof(leaf, proof, root) is True

        # Tampered leaf must fail verification
        assert MerkleAuditTree.verify_proof(leaf + "_tampered", proof, root) is False


def test_authenticate_request_cidr_restriction():
    """Verify authenticate_request enforces allowed_cidrs on entitlements."""
    sm = SecurityManager()
    ent = sm.register_api_key(
        client_id="SubnetRestrictedClient",
        role=Role.OPERATOR,
    )
    ent.allowed_cidrs = ["10.0.0.0/16", "127.0.0.1/32"]

    # Allowed client IP
    auth_ent = sm.authenticate_request(ent.token, client_ip="10.0.5.12")
    assert auth_ent.client_id == "SubnetRestrictedClient"

    # Unauthorized client IP
    with pytest.raises(AccessDenied) as exc:
        sm.authenticate_request(ent.token, client_ip="192.168.1.100")
    assert "Client IP '192.168.1.100' is not permitted" in str(exc.value)

    # Missing / invalid token
    with pytest.raises(AccessDenied):
        sm.authenticate_request("invalid_tok_123")


def test_zero_downtime_api_key_rotation():
    """Verify rotate_api_key issues new key and preserves old key during grace period."""
    sm = SecurityManager()
    old_ent = sm.register_api_key(
        client_id="InstitutionalFund_A",
        role=Role.OPERATOR,
    )
    old_token = old_ent.token

    # Rotate key with a short 2.0s grace period
    new_ent, updated_old = sm.rotate_api_key(old_token, grace_period_s=2.0)

    assert new_ent.client_id == "InstitutionalFund_A"
    assert new_ent.role == Role.OPERATOR
    assert new_ent.is_active is True
    assert updated_old.is_rotating is True
    assert updated_old.expires_at is not None

    # Both tokens must authenticate successfully during grace period
    assert sm.authenticate_request(new_ent.token).client_id == "InstitutionalFund_A"
    assert sm.authenticate_request(old_token).client_id == "InstitutionalFund_A"

    # Simulate expiration of grace period
    updated_old.expires_at = time.time() - 1.0
    revoked = sm.cleanup_expired_rotated_keys()
    assert revoked == 1
    assert updated_old.is_active is False

    # Old token now fails, new token continues to work
    with pytest.raises(AccessDenied):
        sm.authenticate_request(old_token)
    assert sm.authenticate_request(new_ent.token).client_id == "InstitutionalFund_A"


def test_role_tiered_rate_limiting():
    """Verify rate limits adjust according to client role tier."""
    sm = SecurityManager()

    # VIEWER: 1,000 req/s, capacity 2,000
    assert sm.allow_for_role(Role.VIEWER, actor="user1", tokens=500.0) is True
    assert sm.allow_for_role(Role.VIEWER, actor="user1", tokens=1600.0) is False

    # ADMIN: 50,000 req/s, capacity 100,000
    assert sm.allow_for_role(Role.ADMIN, actor="admin1", tokens=50_000.0) is True
    assert sm.allow_for_role(Role.ADMIN, actor="admin1", tokens=40_000.0) is True


def test_anchor_audit_batch():
    """Verify Merkle batch anchoring records and returns cryptographic root."""
    store = Store(":memory:")
    sm = SecurityManager(store=store)

    # Log several actions
    sm.log_audit(
        "CONFIG_UPDATE", actor="admin", role=Role.ADMIN, details="param=buffer_size"
    )
    sm.log_audit(
        "SOURCE_BLOCK", actor="operator", role=Role.OPERATOR, details="source=FEED_BAD"
    )
    store.commit()

    root = sm.anchor_audit_batch(batch_size=10)
    assert isinstance(root, str)
    assert len(root) == 64

    # Verify anchor entry was logged
    entries = store.query_audit_log(limit=5)
    actions = [e["action"] for e in entries]
    assert "MERKLE_BATCH_ANCHOR" in actions
    store.close()
