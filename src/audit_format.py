"""
MDRAP Audit Trail Payload Canonicalization.

Shared between security.py, storage.py, and standalone audit verifier.
Guarantees collision-free hash chaining across diverse inputs.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

__stability__ = "stable"


def audit_bytes_v1(
    prev_hash: str, ts: float, actor: str, role: str, action: str, details: str
) -> bytes:
    """Legacy v1 delimiter-escaped audit entry serialization."""
    esc_actor = str(actor).replace("|", r"\|")
    esc_role = str(role).replace("|", r"\|")
    esc_action = str(action).replace("|", r"\|")
    esc_details = str(details).replace("|", r"\|")
    return f"{prev_hash}|{ts:.6f}|{esc_actor}|{esc_role}|{esc_action}|{esc_details}".encode("utf-8")


def audit_bytes_v2(
    prev_hash: str, ts: float | int, actor: str, role: str, action: str, details: str
) -> bytes:
    """
    Format v2 collision-free audit entry serialization (canonical JSON array).
    [2, prev_hash, ts, actor, role, action, details]
    """
    payload = [2, str(prev_hash), ts, str(actor), str(role), str(action), str(details)]
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def compute_audit_hash(
    prev_hash: str,
    ts: float | int,
    actor: str,
    role: str,
    action: str,
    details: str,
    format_version: int = 2,
) -> str:
    """Compute SHA-256 hash of audit entry based on format version."""
    if format_version == 1:
        data = audit_bytes_v1(prev_hash, float(ts), actor, role, action, details)
    else:
        data = audit_bytes_v2(prev_hash, ts, actor, role, action, details)
    return hashlib.sha256(data).hexdigest()
