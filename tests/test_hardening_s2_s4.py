import os
import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from security import SecurityManager


def test_s2_s4_verify_payload_never_raises():
    sec = SecurityManager()
    payload = {"instrument": "AAPL", "price": 150.0}
    
    # Non-str inputs that crashed the unhardened code:
    assert sec.verify_payload("FEEDX", payload, 123) is False
    assert sec.verify_payload("FEEDX", payload, ["x"]) is False
    assert sec.verify_payload("FEEDX", payload, b"abc") is False
    assert sec.verify_payload("FEEDX", payload, None) is False
    assert sec.verify_payload("FEEDX", payload, object()) is False
    
    # Non-hex / non-ascii strings:
    assert sec.verify_payload("FEEDX", payload, "é") is False
    assert sec.verify_payload("FEEDX", payload, "invalid-hex-string") is False
    assert sec.verify_payload("FEEDX", payload, "a" * 129) is False  # too long
    
    # Legitimate signature must verify
    sig = sec.sign_payload("FEEDX", payload)
    assert sec.verify_payload("FEEDX", payload, sig) is True
    
    # Tampered payload must fail
    tampered = dict(payload, price=151.0)
    assert sec.verify_payload("FEEDX", tampered, sig) is False
