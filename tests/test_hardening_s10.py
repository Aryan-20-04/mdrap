import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from audit_format import audit_bytes_v1, audit_bytes_v2, compute_audit_hash


def test_s10_v1_collision_demonstration():
    # Demonstrates the vulnerability in v1
    prev_hash = "GENESIS"
    ts = 1.0
    v1_a = audit_bytes_v1(prev_hash, ts, "", "", "\\", "|")
    v1_b = audit_bytes_v1(prev_hash, ts, "", "", "|\\", "")
    assert v1_a == v1_b, "v1 should collide on backslash and pipe"


def test_s10_v2_collision_resistance():
    # Proves v2 is immune to delimiter injection and collisions
    prev_hash = "GENESIS"
    ts = 1.0
    v2_a = audit_bytes_v2(prev_hash, ts, "", "", "\\", "|")
    v2_b = audit_bytes_v2(prev_hash, ts, "", "", "|\\", "")
    assert v2_a != v2_b, "v2 must never collide"
    hash_a = compute_audit_hash(prev_hash, ts, "", "", "\\", "|", format_version=2)
    hash_b = compute_audit_hash(prev_hash, ts, "", "", "|\\", "", format_version=2)
    assert hash_a != hash_b, "v2 hashes must differ"
