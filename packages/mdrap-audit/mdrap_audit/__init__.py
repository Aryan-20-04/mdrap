"""MDRAP Audit: Cryptographic Merkle Tree Audit Log Attestation & External Anchoring.

Provides institutional compliance, tamper-evident audit logs, RFC 6962 Merkle tree
attestations, inclusion proofs, consistency proofs, and external timestamp anchoring.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

__version__ = "5.3.0"


def sha256_bytes(data: bytes) -> bytes:
    """Compute raw 32-byte SHA-256 digest."""
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    """Compute 64-char lowercase hexadecimal SHA-256 digest."""
    return hashlib.sha256(data).hexdigest()


def _largest_power_of_two_less_than(n: int) -> int:
    """Return the largest power of two strictly smaller than n (k < n <= 2k)."""
    if n <= 1:
        return 0
    return 1 << ((n - 1).bit_length() - 1)


@dataclass
class InclusionProof:
    """Cryptographic audit path verifying a leaf is member of a Merkle root.

    Compatible with both standard binary directional proofs and RFC 6962 paths.
    """

    leaf_index: int
    leaf_hash: str
    audit_path: list[tuple[str, str]] | list[str]  # list of (sibling_hash, direction) or list of sibling hashes
    root_hash: str
    tree_size: int = 0

    def verify(self) -> bool:
        """Verify inclusion proof by reconstructing the root hash."""
        if not self.audit_path and self.leaf_hash == self.root_hash:
            return True

        if not self.audit_path:
            return False

        # If audit path contains (hash, direction) tuples
        if isinstance(self.audit_path[0], (tuple, list)):
            current = self.leaf_hash
            for item in self.audit_path:
                sibling_hash, direction = item[0], item[1]
                if direction in ("left", "L"):
                    current = MerkleAuditTree.hash_internal_node(sibling_hash, current)
                else:
                    current = MerkleAuditTree.hash_internal_node(current, sibling_hash)
            return current == self.root_hash

        # RFC 6962 algorithm verification
        tree_size = self.tree_size if self.tree_size > 0 else (1 << len(self.audit_path))
        return MerkleAuditTree.verify_rfc6962_inclusion(
            leaf_index=self.leaf_index,
            tree_size=tree_size,
            leaf_hash_hex=self.leaf_hash,
            root_hash_hex=self.root_hash,
            audit_path_hex=self.audit_path,  # type: ignore[arg-type]
        )


@dataclass
class ConsistencyProof:
    """RFC 6962 Section 2.1.2 consistency proof between two historical tree sizes."""

    old_size: int
    new_size: int
    old_root: str
    new_root: str
    proof: list[str]

    def verify(self) -> bool:
        """Verify consistency proof establishing that old_root is a prefix of new_root."""
        return MerkleAuditTree.verify_consistency_proof(
            old_size=self.old_size,
            new_size=self.new_size,
            old_root=self.old_root,
            new_root=self.new_root,
            proof=self.proof,
        )


class MerkleAuditTree:
    """RFC 6962 compliant Merkle Tree over canonical market-data audit logs."""

    def __init__(
        self,
        leaf_records: Sequence[dict[str, Any] | str | bytes] | None = None,
    ) -> None:
        self.raw_leaves: list[bytes] = []
        self.leaf_hashes: list[str] = []
        if leaf_records:
            self.build(leaf_records)

    @staticmethod
    def serialize_record(record: dict[str, Any] | str | bytes) -> bytes:
        """Serialize an audit record into canonical bytes."""
        if isinstance(record, bytes):
            return record
        if isinstance(record, dict):
            return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return str(record).encode("utf-8")

    @classmethod
    def hash_leaf(cls, record: dict[str, Any] | str | bytes) -> str:
        """Compute leaf hash with RFC 6962 0x00 domain separator prefix."""
        payload = cls.serialize_record(record)
        return sha256_hex(b"\x00" + payload)

    @classmethod
    def hash_record(cls, record: dict[str, Any] | str | bytes) -> str:
        """Alias for hash_leaf."""
        return cls.hash_leaf(record)

    @staticmethod
    def hash_internal_node(left: str | bytes, right: str | bytes) -> str:
        """Combine two child node hashes with RFC 6962 0x01 domain separator prefix."""
        left_bytes = bytes.fromhex(left) if isinstance(left, str) else left
        right_bytes = bytes.fromhex(right) if isinstance(right, str) else right
        return sha256_hex(b"\x01" + left_bytes + right_bytes)

    @classmethod
    def _compute_mth(cls, leaf_bytes_list: Sequence[bytes]) -> bytes:
        """Recursive RFC 6962 Merkle Tree Hash (MTH) calculation."""
        n = len(leaf_bytes_list)
        if n == 0:
            return sha256_bytes(b"")
        if n == 1:
            return sha256_bytes(b"\x00" + leaf_bytes_list[0])
        k = _largest_power_of_two_less_than(n)
        left = cls._compute_mth(leaf_bytes_list[:k])
        right = cls._compute_mth(leaf_bytes_list[k:])
        return sha256_bytes(b"\x01" + left + right)

    @classmethod
    def compute_root(cls, leaf_data_list: Sequence[dict[str, Any] | str | bytes]) -> str:
        """Compute RFC 6962 root hash for a sequence of records."""
        if not leaf_data_list:
            return sha256_hex(b"")
        raw_list = [cls.serialize_record(x) for x in leaf_data_list]
        return cls._compute_mth(raw_list).hex()

    def build(self, leaf_records: Sequence[dict[str, Any] | str | bytes]) -> str:
        """Build tree from sequence of records and return root hash in hex."""
        self.raw_leaves = [self.serialize_record(r) for r in leaf_records]
        self.leaf_hashes = [self.hash_leaf(b) for b in self.raw_leaves]
        return self.root_hash

    @property
    def root_hash(self) -> str:
        """Return the current root hash in hex."""
        if not self.raw_leaves:
            return sha256_hex(b"")
        return self._compute_mth(self.raw_leaves).hex()

    @property
    def leaves(self) -> list[str]:
        """Backward compatibility alias for leaf_hashes."""
        return self.leaf_hashes

    def get_inclusion_proof(self, leaf_index: int) -> InclusionProof:
        """Generate audit inclusion path for the specified leaf index."""
        n = len(self.raw_leaves)
        if leaf_index < 0 or leaf_index >= n:
            raise IndexError(f"Leaf index {leaf_index} out of bounds (0..{n-1})")

        # Directional audit path for compatibility with existing tests
        directional_path: list[tuple[str, str]] = []

        def _build_path(m: int, leaves: Sequence[bytes]) -> list[bytes]:
            size = len(leaves)
            if size <= 1:
                return []
            k = _largest_power_of_two_less_than(size)
            if m < k:
                sub = _build_path(m, leaves[:k])
                right_hash = self._compute_mth(leaves[k:]).hex()
                directional_path.append((right_hash, "right"))
                return sub + [bytes.fromhex(right_hash)]
            else:
                sub = _build_path(m - k, leaves[k:])
                left_hash = self._compute_mth(leaves[:k]).hex()
                directional_path.append((left_hash, "left"))
                return sub + [bytes.fromhex(left_hash)]

        _build_path(leaf_index, self.raw_leaves)

        return InclusionProof(
            leaf_index=leaf_index,
            leaf_hash=self.leaf_hashes[leaf_index],
            audit_path=directional_path,
            root_hash=self.root_hash,
            tree_size=n,
        )

    def get_consistency_proof(self, m: int, n: int | None = None) -> ConsistencyProof:
        """Generate RFC 6962 consistency proof between tree size m and n."""
        total_leaves = len(self.raw_leaves)
        target_n = n if n is not None else total_leaves
        if m < 1 or m > target_n or target_n > total_leaves:
            raise ValueError(f"Invalid consistency proof parameters: m={m}, n={target_n}, total={total_leaves}")

        leaves_subset = self.raw_leaves[:target_n]

        def _subproof(sub_m: int, sub_leaves: Sequence[bytes], b: bool) -> list[bytes]:
            size = len(sub_leaves)
            if sub_m == size:
                if b:
                    return []
                return [self._compute_mth(sub_leaves)]
            k = _largest_power_of_two_less_than(size)
            if sub_m <= k:
                return _subproof(sub_m, sub_leaves[:k], b) + [self._compute_mth(sub_leaves[k:])]
            else:
                return _subproof(sub_m - k, sub_leaves[k:], False) + [self._compute_mth(sub_leaves[:k])]

        def _consistency_path(cons_m: int, cons_leaves: Sequence[bytes]) -> list[bytes]:
            size = len(cons_leaves)
            if cons_m == size:
                return []
            k = _largest_power_of_two_less_than(size)
            if cons_m <= k:
                return _subproof(cons_m, cons_leaves[:k], True) + [self._compute_mth(cons_leaves[k:])]
            else:
                return _subproof(cons_m - k, cons_leaves[k:], False) + [self._compute_mth(cons_leaves[:k])]

        proof_bytes = _consistency_path(m, leaves_subset)
        proof_hex = [p.hex() for p in proof_bytes]

        old_root = self._compute_mth(self.raw_leaves[:m]).hex()
        new_root = self._compute_mth(leaves_subset).hex()

        return ConsistencyProof(
            old_size=m,
            new_size=target_n,
            old_root=old_root,
            new_root=new_root,
            proof=proof_hex,
        )

    @classmethod
    def generate_proof(
        cls, leaf_data_list: Sequence[dict[str, Any] | str | bytes], leaf_index: int
    ) -> list[tuple[str, str]]:
        """Static helper to generate proof matching test expectations."""
        tree = cls(leaf_data_list)
        proof = tree.get_inclusion_proof(leaf_index)
        return proof.audit_path  # type: ignore[return-value]

    @classmethod
    def verify_proof(
        cls,
        leaf_data: dict[str, Any] | str | bytes,
        proof: list[tuple[str, str]],
        root_hash: str,
    ) -> bool:
        """Static helper to verify inclusion proof."""
        leaf_hash = cls.hash_leaf(leaf_data)
        inc_proof = InclusionProof(
            leaf_index=0,
            leaf_hash=leaf_hash,
            audit_path=proof,
            root_hash=root_hash,
        )
        return inc_proof.verify()

    @staticmethod
    def verify_rfc6962_inclusion(
        leaf_index: int,
        tree_size: int,
        leaf_hash_hex: str,
        root_hash_hex: str,
        audit_path_hex: Sequence[str],
    ) -> bool:
        """Verify inclusion proof according to RFC 6962 Section 2.1.1."""
        if tree_size == 1:
            return not audit_path_hex and leaf_hash_hex == root_hash_hex
        if leaf_index >= tree_size or leaf_index < 0:
            return False

        current = bytes.fromhex(leaf_hash_hex)
        fn = current
        sn = current
        p_m = leaf_index
        p_n = tree_size - 1

        for p_hex in audit_path_hex:
            p = bytes.fromhex(p_hex)
            if (p_m & 1) or (p_m == p_n):
                fn = sha256_bytes(b"\x01" + p + fn)
                sn = sha256_bytes(b"\x01" + p + sn)
                while not (p_m & 1) and p_m != 0:
                    p_m >>= 1
                    p_n >>= 1
            else:
                sn = sha256_bytes(b"\x01" + sn + p)
            p_m >>= 1
            p_n >>= 1

        return sn.hex() == root_hash_hex and p_m == 0 and p_n == 0

    @staticmethod
    def verify_consistency_proof(
        old_size: int,
        new_size: int,
        old_root: str,
        new_root: str,
        proof: Sequence[str],
    ) -> bool:
        """Verify consistency proof according to RFC 6962 Section 2.1.2."""
        if old_size == new_size:
            return len(proof) == 0 and old_root == new_root
        if old_size == 0 or old_size > new_size:
            return False

        old_root_b = bytes.fromhex(old_root)
        new_root_b = bytes.fromhex(new_root)

        m_prime = old_size - 1
        n_prime = new_size - 1

        while m_prime & 1:
            m_prime >>= 1
            n_prime >>= 1

        if m_prime != 0:
            if not proof:
                return False
            fn = bytes.fromhex(proof[0])
            sn = fn
            path_items = proof[1:]
        else:
            fn = old_root_b
            sn = old_root_b
            path_items = proof

        for p_hex in path_items:
            p = bytes.fromhex(p_hex)
            if n_prime == 0:
                return False
            if (m_prime & 1) or (m_prime == n_prime):
                fn = sha256_bytes(b"\x01" + p + fn)
                sn = sha256_bytes(b"\x01" + p + sn)
                while not (m_prime & 1) and m_prime != 0:
                    m_prime >>= 1
                    n_prime >>= 1
            else:
                sn = sha256_bytes(b"\x01" + sn + p)
            m_prime >>= 1
            n_prime >>= 1

        return fn == old_root_b and sn == new_root_b and m_prime == 0 and n_prime == 0


@dataclass
class AuditAttestationCertificate:
    """Verifiable external cryptographic attestation certificate."""

    merkle_root: str
    record_count: int
    first_sequence: int
    last_sequence: int
    timestamp_utc: str
    attestation_authority_id: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(asdict(self), indent=indent)


class TimestampAnchor:
    """Signs and anchors Merkle roots using institutional HMAC-SHA256 or digital signature."""

    def __init__(
        self,
        authority_id: str = "MDRAP-AUTHORITY-ROOT-1",
        secret_key: str | None = None,
    ) -> None:
        self.authority_id = authority_id
        resolved_key = secret_key or os.environ.get("MDRAP_AUDIT_SECRET_KEY")
        if not resolved_key:
            raise ValueError("MDRAP_AUDIT_SECRET_KEY must be provided or set in environment")
        self.secret_key = resolved_key.encode("utf-8")

    def create_attestation(
        self,
        merkle_root: str,
        record_count: int,
        first_sequence: int,
        last_sequence: int,
        timestamp_utc: str | None = None,
    ) -> AuditAttestationCertificate:
        ts = timestamp_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        statement = f"{merkle_root}:{record_count}:{first_sequence}:{last_sequence}:{ts}:{self.authority_id}".encode(
            "utf-8"
        )
        sig = hmac.new(self.secret_key, statement, hashlib.sha256).hexdigest()

        return AuditAttestationCertificate(
            merkle_root=merkle_root,
            record_count=record_count,
            first_sequence=first_sequence,
            last_sequence=last_sequence,
            timestamp_utc=ts,
            attestation_authority_id=self.authority_id,
            signature=sig,
        )

    def verify_attestation(self, cert: AuditAttestationCertificate) -> bool:
        statement = f"{cert.merkle_root}:{cert.record_count}:{cert.first_sequence}:{cert.last_sequence}:{cert.timestamp_utc}:{cert.attestation_authority_id}".encode(
            "utf-8"
        )
        expected_sig = hmac.new(self.secret_key, statement, hashlib.sha256).hexdigest()
        return hmac.compare_digest(cert.signature, expected_sig)
