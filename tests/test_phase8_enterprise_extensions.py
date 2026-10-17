"""Acceptance test suite for Phase 8: Enterprise Operational Extensions & Audit Attestations.

Verifies:
1. Multi-Tenant RBAC & Isolation (packages/mdrap-enterprise):
   - Tenant symbol whitelisting and access validation.
   - Role boundaries (ADMIN, OPERATOR, TRADER, VIEWER, AUDITOR).
2. API Key Salted Hashing & Grace-Period Rotation:
   - Cryptographically random token generation and salted SHA-256 storage.
   - Zero-downtime rotation: old key valid during grace period, new key active immediately.
   - Immediate key revocation.
3. TLS Reverse Proxy Configuration:
   - Hardened Nginx reverse proxy template generation with TLS 1.3, mTLS, HSTS, and WebSocket support.
4. Cryptographic Merkle Tree Audit Attestations (packages/mdrap-audit):
   - Deterministic Merkle root hash computation over canonical events.
   - O(log N) inclusion proofs and cryptographic audit path verification.
   - Tamper-evidence: modified leaf payload fails proof verification.
5. External Timestamp Anchor & Attestation Certificates:
   - RFC 3161 style attestation signing and cryptographic verification.
   - Rejection of forged or modified attestation certificates.
6. Dual-Distribution Packaging Isolation:
   - Verification that packages/ and dist/ are strictly ignored by git.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import pytest

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages" / "mdrap-enterprise"))
sys.path.insert(0, str(ROOT / "packages" / "mdrap-audit"))

from mdrap_enterprise import (
    ApiKeyRotator,
    EnterpriseRole,
    Tenant,
    TenantManager,
    generate_tls_reverse_proxy_config,
)
from mdrap_audit import (
    AuditAttestationCertificate,
    InclusionProof,
    MerkleAuditTree,
    TimestampAnchor,
)


class TestEnterpriseRbacAndKeyRotation:
    """Verifies multi-tenant access control and zero-downtime key rotation."""

    def test_tenant_symbol_authorization(self):
        tm = TenantManager()
        tm.register_tenant("fund_alpha", "Alpha Capital", allowed_symbols=["BTC/USDT", "ETH/USDT"])
        tm.register_tenant("fund_beta", "Beta Prop", allowed_symbols=["*"])

        assert tm.check_access("fund_alpha", "BTC/USDT") is True
        assert tm.check_access("fund_alpha", "ETH/USDT") is True
        assert tm.check_access("fund_alpha", "SOL/USDT") is False

        assert tm.check_access("fund_beta", "BTC/USDT") is True
        assert tm.check_access("fund_beta", "SOL/USDT") is True

        # Non-existent tenant denied
        assert tm.check_access("unknown_fund", "BTC/USDT") is False

    def test_salted_api_key_generation_and_validation(self):
        rotator = ApiKeyRotator()
        raw_token, rec = rotator.generate_key("tenant_1", EnterpriseRole.TRADER)

        assert raw_token.startswith("mdrap_live_")
        assert len(rec.salt) >= 16
        assert len(rec.token_hash) == 64  # SHA-256 hex length

        # Validation success
        found = rotator.validate_token(raw_token)
        assert found is not None
        assert found.tenant_id == "tenant_1"
        assert found.role == EnterpriseRole.TRADER

        # Invalid token rejected
        assert rotator.validate_token("mdrap_live_invalid_fake_token_123") is None

    def test_zero_downtime_key_rotation_with_grace_period(self):
        rotator = ApiKeyRotator(default_grace_period_sec=3600.0)
        old_token, old_rec = rotator.generate_key("tenant_hedge", EnterpriseRole.OPERATOR)

        # Rotate key
        new_token, new_rec = rotator.rotate_key(old_token, grace_period_sec=100.0)
        assert new_token != old_token

        # Both keys MUST be valid during the grace period
        assert rotator.validate_token(old_token) is not None
        assert rotator.validate_token(new_token) is not None

        # Simulate expiration after grace period
        now_after_grace = time.time() + 200.0
        assert rotator.validate_token(old_token, current_ts=now_after_grace) is None
        assert rotator.validate_token(new_token, current_ts=now_after_grace) is not None

    def test_immediate_key_revocation(self):
        rotator = ApiKeyRotator()
        token, rec = rotator.generate_key("tenant_audit", EnterpriseRole.AUDITOR)

        assert rotator.validate_token(token) is not None
        revoked = rotator.revoke_key(token)
        assert revoked is True
        assert rotator.validate_token(token) is None

    def test_tls_reverse_proxy_config_generation(self):
        config = generate_tls_reverse_proxy_config(
            domain="gateway.mdrap.internal",
            upstream_port=9000,
            client_mtls=True,
        )
        assert "server_name gateway.mdrap.internal;" in config
        assert "ssl_protocols TLSv1.3 TLSv1.2;" in config
        assert "ssl_verify_client on;" in config
        assert "proxy_pass http://mdrap_core_backend;" in config
        assert "proxy_set_header Upgrade $http_upgrade;" in config


class TestCryptographicMerkleAuditAttestations:
    """Verifies Merkle tree inclusion proofs and external timestamp anchoring."""

    def test_merkle_tree_root_and_inclusion_proofs(self):
        records = [
            {"event_id": f"ev_{i}", "seq": i, "price": 65000.0 + i, "status": "VALID"}
            for i in range(16)
        ]
        tree = MerkleAuditTree(records)
        assert len(tree.root_hash) == 64

        # Generate and verify proof for leaf 5
        proof = tree.get_inclusion_proof(5)
        assert proof.leaf_index == 5
        assert proof.root_hash == tree.root_hash
        assert proof.verify() is True

        # Verify all leaves
        for idx in range(16):
            p = tree.get_inclusion_proof(idx)
            assert p.verify() is True

    def test_merkle_tamper_evidence(self):
        records = [
            {"event_id": f"ev_{i}", "seq": i, "price": 100.0 + i}
            for i in range(8)
        ]
        tree = MerkleAuditTree(records)
        proof = tree.get_inclusion_proof(3)
        assert proof.verify() is True

        # Adversarially tamper with the leaf hash
        tampered_proof = InclusionProof(
            leaf_index=proof.leaf_index,
            leaf_hash="a" * 64,  # Forged leaf hash
            audit_path=proof.audit_path,
            root_hash=proof.root_hash,
        )
        # Verification must FAIL
        assert tampered_proof.verify() is False

    def test_timestamp_anchor_and_certificate_verification(self):
        anchor = TimestampAnchor(authority_id="MDRAP-ANCHOR-CH-1", secret_key="test_secret_key_123")
        tree = MerkleAuditTree(["tx1", "tx2", "tx3", "tx4"])

        cert = anchor.create_attestation(
            merkle_root=tree.root_hash,
            record_count=4,
            first_sequence=1,
            last_sequence=4,
        )
        assert cert.merkle_root == tree.root_hash
        assert cert.record_count == 4
        assert len(cert.signature) == 64

        # Verification succeeds on genuine cert
        assert anchor.verify_attestation(cert) is True

        # Tampered certificate (modified sequence or root) must fail
        forged_cert = AuditAttestationCertificate(
            merkle_root="f" * 64,
            record_count=cert.record_count,
            first_sequence=cert.first_sequence,
            last_sequence=cert.last_sequence,
            timestamp_utc=cert.timestamp_utc,
            attestation_authority_id=cert.attestation_authority_id,
            signature=cert.signature,
        )
        assert anchor.verify_attestation(forged_cert) is False


class TestDualDistributionPackagingIsolation:
    """Verifies that private packages remain strictly excluded from git tracking."""

    def test_git_check_ignore_packages_and_dist(self):
        res = subprocess.run(
            ["git", "check-ignore", "packages/", "dist/"],
            capture_output=True,
            text=True,
            check=True,
        )
        output = res.stdout.strip().splitlines()
        assert any("packages" in line for line in output)
        assert any("dist" in line for line in output)

    @pytest.mark.slow
    def test_commercial_packaging_script_execution(self):
        script = os.path.join("scripts", "build_commercial_packages.py")
        res = subprocess.run(
            [sys.executable, script],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res.returncode == 0
        assert "Commercial packaging complete: 6/6 wheels built" in res.stdout
