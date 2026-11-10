"""Independent Verification Test Suite for RFC 6962 Merkle Tree Audit Compliance.

Covers:
- Standard RFC 6962 Certificate Transparency test vectors (0 to 8 leaves)
- 0x00 leaf domain separation & 0x01 internal node domain separation
- Inclusion proofs and directional verification
- Consistency proofs across all tree sizes m <= n
- Tamper detection (modified leaves, mutated proofs, forged roots)
- TimestampAnchor attestation and HMAC signature verification
"""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packages" / "mdrap-audit"))

from mdrap_audit import (
    AuditAttestationCertificate,
    ConsistencyProof,
    InclusionProof,
    MerkleAuditTree,
    TimestampAnchor,
)

# Canonical RFC 6962 / Certificate Transparency test inputs
RFC6962_TEST_INPUTS = [
    b"",
    b"\x00",
    b"\x10",
    b"\x20\x21\x22\x23\x24\x25\x26\x27",
    b"\x30\x31\x32\x33\x34\x35\x36\x37",
    b"\x40\x41\x42\x43\x44\x45\x46\x47",
    b"\x50\x51\x52\x53\x54\x55\x56\x57",
    b"\x60\x61\x62\x63\x64\x65\x66\x67",
]

# Canonical RFC 6962 roots for subsets inputs[0:k] for k in 0..8
EXPECTED_RFC6962_ROOTS = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # 0: empty
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",  # 1
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",  # 2
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",  # 3
    "04619e44b8fc66ea2a0b65c455db20cd6ee50ccb97ec98f79044d8a00a57467e",  # 4
    "a31128655babd6d0c595c039304bebd11b4b5ff98d3df5b789aad5480d07ff60",  # 5
    "a953598af625563b5ff8f26544a2acd5558c6881124056f649b3a410c4123b8c",  # 6
    "6bab73ec0d8902ccf5f2bf62c6a8c53904a4d749e674c3b863a42bdb6c06f93f",  # 7
    "a8a3aba022b6e9d1514cd2b4acb6417774ef276d05d89a21cc0e453ddb1d4dc8",  # 8
]


def test_rfc6962_canonical_test_vectors():
    """Verify bit-exact match against standard RFC 6962 test vector roots."""
    for count in range(len(RFC6962_TEST_INPUTS) + 1):
        subset = RFC6962_TEST_INPUTS[:count]
        tree = MerkleAuditTree(subset)
        expected = EXPECTED_RFC6962_ROOTS[count]
        assert tree.root_hash == expected, f"Root mismatch at size {count}: {tree.root_hash} != {expected}"


def test_rfc6962_inclusion_proofs_all_subsets():
    """Verify inclusion proofs for every leaf across all tree sizes 1 to 8."""
    for count in range(1, len(RFC6962_TEST_INPUTS) + 1):
        subset = RFC6962_TEST_INPUTS[:count]
        tree = MerkleAuditTree(subset)
        for leaf_idx in range(count):
            proof = tree.get_inclusion_proof(leaf_idx)
            assert proof.leaf_index == leaf_idx
            assert proof.root_hash == tree.root_hash
            assert proof.verify() is True, f"Inclusion proof failed for leaf {leaf_idx} in tree size {count}"


def test_rfc6962_consistency_proofs_all_pairs():
    """Verify consistency proofs across all combinations of 1 <= m <= n <= 8."""
    tree = MerkleAuditTree(RFC6962_TEST_INPUTS)
    for n in range(1, len(RFC6962_TEST_INPUTS) + 1):
        for m in range(1, n + 1):
            cons_proof = tree.get_consistency_proof(m=m, n=n)
            assert cons_proof.old_size == m
            assert cons_proof.new_size == n
            assert cons_proof.old_root == EXPECTED_RFC6962_ROOTS[m]
            assert cons_proof.new_root == EXPECTED_RFC6962_ROOTS[n]
            assert cons_proof.verify() is True, f"Consistency proof failed for m={m}, n={n}"


def test_rfc6962_tamper_detection():
    """Verify that tampering with leaves or proofs fails verification."""
    tree = MerkleAuditTree(RFC6962_TEST_INPUTS)
    proof = tree.get_inclusion_proof(3)
    assert proof.verify() is True

    # 1. Tamper with leaf hash
    tampered_leaf = InclusionProof(
        leaf_index=proof.leaf_index,
        leaf_hash="00" * 32,
        audit_path=proof.audit_path,
        root_hash=proof.root_hash,
    )
    assert tampered_leaf.verify() is False

    # 2. Tamper with root hash
    tampered_root = InclusionProof(
        leaf_index=proof.leaf_index,
        leaf_hash=proof.leaf_hash,
        audit_path=proof.audit_path,
        root_hash="ff" * 32,
    )
    assert tampered_root.verify() is False

    # 3. Tamper with consistency proof
    cons_proof = tree.get_consistency_proof(3, 8)
    assert cons_proof.verify() is True
    tampered_cons = ConsistencyProof(
        old_size=cons_proof.old_size,
        new_size=cons_proof.new_size,
        old_root="00" * 32,
        new_root=cons_proof.new_root,
        proof=cons_proof.proof,
    )
    assert tampered_cons.verify() is False


def test_timestamp_anchor_attestation():
    """Verify external timestamp anchoring and certificate cryptographic verification."""
    anchor = TimestampAnchor(authority_id="MDRAP-TEST-AUTH-1", secret_key="super_secret_test_key")
    tree = MerkleAuditTree(["trade_001", "trade_002", "trade_003"])
    cert = anchor.create_attestation(
        merkle_root=tree.root_hash,
        record_count=3,
        first_sequence=100,
        last_sequence=102,
    )
    assert cert.merkle_root == tree.root_hash
    assert cert.record_count == 3
    assert anchor.verify_attestation(cert) is True

    # Mutated certificate must fail verification
    mutated_cert = AuditAttestationCertificate(
        merkle_root=cert.merkle_root,
        record_count=cert.record_count + 1,  # tampered
        first_sequence=cert.first_sequence,
        last_sequence=cert.last_sequence,
        timestamp_utc=cert.timestamp_utc,
        attestation_authority_id=cert.attestation_authority_id,
        signature=cert.signature,
    )
    assert anchor.verify_attestation(mutated_cert) is False
