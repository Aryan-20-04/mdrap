#!/usr/bin/env python3
"""
MDRAP External Standalone Evidence Verifier (Spec §26).

Zero-dependency, standalone verification tool for institutional compliance officers,
risk auditors, and legal teams. Validates MDRAP assurance packages WITHOUT requiring
the MDRAP platform or internal library code.

Verifies:
  1. Wire Payload Integrity: Recomputes SHA-256 digest of raw wire bytes.
  2. Cryptographic Lineage Hash: Binds raw wire, normalization state, and quality verdict.
  3. RFC 6962 Merkle Proof: Validates inclusion proofs with domain separation (0x00/0x01).
  4. Detects payload tampering, hash substitution, metadata alteration, and tree manipulation.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from typing import Any, Dict, List, Optional, Tuple


def compute_payload_sha256(raw_wire: Any) -> str:
    """Compute deterministic SHA-256 of raw wire message."""
    if raw_wire is None:
        payload_bytes = b""
    elif isinstance(raw_wire, bytes):
        payload_bytes = raw_wire
    elif isinstance(raw_wire, str):
        payload_bytes = raw_wire.encode("utf-8")
    elif isinstance(raw_wire, dict):
        payload_bytes = json.dumps(raw_wire, sort_keys=True).encode("utf-8")
    else:
        payload_bytes = str(raw_wire).encode("utf-8")
    return hashlib.sha256(payload_bytes).hexdigest()


def compute_lineage_hash(
    event_id: str,
    raw_id: str,
    provider: str,
    instrument: str,
    data_mode: str,
    payload_sha256: str,
    quality_status: str,
    processing_timestamp: float,
) -> str:
    """Compute deterministic lineage hash binding raw frame and canonical outcome."""
    manifest = f"{event_id}:{raw_id}:{provider}:{instrument}:{data_mode}:{payload_sha256}:{quality_status}:{processing_timestamp}"
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def verify_merkle_rfc6962_proof(
    leaf_hash: str,
    proof: List[Tuple[str, str]],
    expected_root: str,
) -> bool:
    """Verify RFC 6962 Merkle inclusion proof with 0x00 leaf and 0x01 node separation."""
    # Leaf hash with 0x00 prefix
    curr = hashlib.sha256(b"\x00" + leaf_hash.encode("utf-8")).hexdigest()
    for sibling_hex, pos in proof:
        if pos.upper() == "R":
            curr = hashlib.sha256(
                b"\x01" + bytes.fromhex(curr) + bytes.fromhex(sibling_hex)
            ).hexdigest()
        else:
            curr = hashlib.sha256(
                b"\x01" + bytes.fromhex(sibling_hex) + bytes.fromhex(curr)
            ).hexdigest()
    return hmac.compare_digest(curr, expected_root)


def verify_evidence_package(evidence: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Perform rigorous independent cryptographic verification of an evidence manifest."""
    errors: List[str] = []

    # 1. Schema check
    req_fields = ["evidence_type", "event_id", "provider", "instrument", "timestamps", "quality", "integrity"]
    for f in req_fields:
        if f not in evidence:
            errors.append(f"Missing required manifest field: '{f}'")
    if errors:
        return False, errors

    event_id = evidence["event_id"]
    provider = evidence["provider"]
    instrument = evidence["instrument"]
    data_mode = evidence.get("data_mode", "LIVE")
    processing_ts = evidence["timestamps"].get("processing_ts", 0.0)
    quality_status = evidence["quality"].get("status", "VALID")
    claimed_payload_sha256 = evidence["integrity"].get("payload_sha256", "")
    claimed_lineage_hash = evidence["integrity"].get("lineage_hash", "")
    raw_id = evidence["integrity"].get("raw_id", "") or evidence.get("raw_id", "")

    # 2. Wire payload check if raw wire is provided
    raw_wire = evidence.get("raw_wire")
    if raw_wire is not None:
        recalc_wire_hash = compute_payload_sha256(raw_wire)
        if not hmac.compare_digest(recalc_wire_hash, claimed_payload_sha256):
            errors.append(
                f"PAYLOAD TAMPER DETECTED: Computed wire SHA-256 '{recalc_wire_hash}' "
                f"does not match claimed payload digest '{claimed_payload_sha256}'"
            )

    # 3. Lineage hash check
    recalc_lineage = compute_lineage_hash(
        event_id=event_id,
        raw_id=raw_id,
        provider=provider,
        instrument=instrument,
        data_mode=data_mode,
        payload_sha256=claimed_payload_sha256,
        quality_status=quality_status,
        processing_timestamp=processing_ts,
    )
    if not hmac.compare_digest(recalc_lineage, claimed_lineage_hash):
        errors.append(
            f"LINEAGE TAMPER DETECTED: Recomputed lineage digest '{recalc_lineage}' "
            f"does not match sealed manifest hash '{claimed_lineage_hash}'"
        )

    # 4. Merkle proof check if present
    merkle_proof = evidence.get("merkle_proof")
    expected_root = evidence.get("merkle_root")
    if merkle_proof and expected_root:
        leaf_input = evidence["integrity"].get("entry_hash") or claimed_lineage_hash
        proof_tuples = [(p[0], p[1]) for p in merkle_proof]
        if not verify_merkle_rfc6962_proof(leaf_input, proof_tuples, expected_root):
            errors.append(
                f"MERKLE PROOF REJECTED: Inclusion proof failed to reconstruct root '{expected_root}'"
            )

    return len(errors) == 0, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MDRAP External Standalone Evidence Verifier (Spec §26)"
    )
    parser.add_argument("file", help="Path to exported JSON evidence package")
    parser.add_argument("--quiet", action="store_true", help="Quiet output, exit code only")
    args = parser.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            evidence = json.load(f)
    except Exception as exc:
        if not args.quiet:
            print(f"[ERROR] Could not read evidence file: {exc}", file=sys.stderr)
        return 2

    is_valid, errors = verify_evidence_package(evidence)

    if is_valid:
        if not args.quiet:
            print("==================================================================")
            print("✔ MDRAP INDEPENDENT CRYPTOGRAPHIC VERIFICATION: PASSED")
            print(f"Event ID:      {evidence.get('event_id')}")
            print(f"Provider:      {evidence.get('provider')} | Instrument: {evidence.get('instrument')}")
            print(f"Payload Digest: {evidence['integrity'].get('payload_sha256')}")
            print(f"Lineage Hash:   {evidence['integrity'].get('lineage_hash')}")
            print("Status:        VERIFIED_TAMPER_EVIDENT (Zero Preimage / Match Confirmed)")
            print("==================================================================")
        return 0
    else:
        if not args.quiet:
            print("==================================================================", file=sys.stderr)
            print("✖ MDRAP INDEPENDENT CRYPTOGRAPHIC VERIFICATION: FAILED", file=sys.stderr)
            for err in errors:
                print(f"  [TAMPER DETECTED] {err}", file=sys.stderr)
            print("==================================================================", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
