"""
Security, Cryptographic Integrity & Audit Logging (MDRAP Spec Section 19).

Implements enterprise-grade market data infrastructure security:
- HMAC-SHA256 feed payload signing and constant-time signature verification
- Role-Based Access Control (RBAC) separating VIEWER, OPERATOR, and ADMIN
- Cryptographically chained (Merkle-style) tamper-evident audit logging
- Token bucket high-throughput rate limiter for DoS / flood mitigation
- Strict input sanitization and schema bounds validation
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


class Role(str, enum.Enum):
    VIEWER = "VIEWER"       # Read BBO, candles, spreads, analytics, platform status
    OPERATOR = "OPERATOR"   # Run ingestion, live streaming, inspect quarantine
    ADMIN = "ADMIN"         # Manual source block/unblock, secrets management, chaos drills, audit review


_ROLE_HIERARCHY = {
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


class PermissionError(Exception):
    """Raised when an actor lacks sufficient RBAC privileges."""
    pass


class TokenBucketRateLimiter:
    """
    High-performance token-bucket rate limiter per source.
    Protects against malformed quote flooding and DoS.
    """

    def __init__(self, rate: float = 20_000.0, capacity: float = 40_000.0):
        self.rate = rate  # tokens added per second
        self.capacity = capacity
        # source -> (tokens, last_update_time)
        self._buckets: Dict[str, Tuple[float, float]] = {}

    def allow(self, source: str, tokens: float = 1.0) -> bool:
        now = time.perf_counter()
        current_tokens, last_time = self._buckets.get(source, (self.capacity, now))
        
        # Refill tokens based on elapsed time
        elapsed = now - last_time
        current_tokens = min(self.capacity, current_tokens + elapsed * self.rate)

        if current_tokens >= tokens:
            self._buckets[source] = (current_tokens - tokens, now)
            return True
        else:
            self._buckets[source] = (current_tokens, now)
            return False

    def reset(self, source: Optional[str] = None):
        if source:
            self._buckets.pop(source, None)
        else:
            self._buckets.clear()


class InputSanitizer:
    """
    Strict input validation guard ensuring data bounds and safe representations
    before events enter gateway normalization.
    """
    SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9/_\-\.]{1,20}$")
    MAX_PRICE = 10_000_000.0
    MIN_PRICE = 0.00000001
    MAX_QUANTITY = 1_000_000_000.0

    @classmethod
    def sanitize(cls, payload: Any) -> Tuple[bool, Optional[str]]:
        if not isinstance(payload, dict):
            return False, "Payload must be a dictionary"

        inst = payload.get("instrument")
        if inst is not None:
            if not isinstance(inst, str) or not cls.SYMBOL_PATTERN.match(inst):
                return False, f"Invalid symbol format: {inst}"

        price = payload.get("price")
        if price is not None:
            if not isinstance(price, (int, float)) or price < cls.MIN_PRICE or price > cls.MAX_PRICE:
                return False, f"Price out of acceptable bounds: {price}"

        bid = payload.get("bid")
        if bid is not None:
            if not isinstance(bid, (int, float)) or bid < 0.0 or bid > cls.MAX_PRICE:
                return False, f"Bid price out of bounds: {bid}"

        ask = payload.get("ask")
        if ask is not None:
            if not isinstance(ask, (int, float)) or ask < 0.0 or ask > cls.MAX_PRICE:
                return False, f"Ask price out of bounds: {ask}"

        qty = payload.get("quantity")
        if qty is not None:
            if not isinstance(qty, (int, float)) or qty < 0.0 or qty > cls.MAX_QUANTITY:
                return False, f"Quantity out of bounds: {qty}"

        seq = payload.get("sequence")
        if seq is not None and not isinstance(seq, int):
            return False, f"Sequence number must be integer: {seq}"

        return True, None


class SecurityManager:
    """
    Central security and cryptographic coordinator for MDRAP.
    Manages HMAC feed authentication, RBAC authorization, and tamper-evident audit logs.
    """

    DEFAULT_SECRETS = {
        "FEEDX": "mdrap_feed_secret_x_7f9a2b1c",
        "FEEDY": "mdrap_feed_secret_y_3d8e5f0a",
        "FEEDZ": "mdrap_feed_secret_z_9c4b1a7d",
        "BINANCE": "mdrap_pub_binance_key_001",
        "COINBASE": "mdrap_pub_coinbase_key_002",
    }

    def __init__(self, store: Optional[Any] = None, rate_limit: float = 20_000.0):
        self.store = store
        self._secrets: Dict[str, bytes] = {
            src: key.encode("utf-8") for src, key in self.DEFAULT_SECRETS.items()
        }
        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit)
        self.sanitizer = InputSanitizer()
        self._verified_count = 0
        self._tampered_count = 0
        self._rate_limited_count = 0

    def register_feed_secret(self, source: str, secret_key: str) -> None:
        """Register or rotate a pre-shared cryptographic key for a market data feed."""
        self._secrets[source] = secret_key.encode("utf-8")

    def sign_payload(self, source: str, payload: dict) -> str:
        """
        Generate HMAC-SHA256 signature for a feed payload.
        Keys are sorted to guarantee canonical determinism.
        """
        secret = self._secrets.get(source)
        if not secret:
            secret = f"default_secret_{source}".encode("utf-8")
            self._secrets[source] = secret

        # Exclude existing signature field if present to avoid recursive self-reference
        filtered = {k: v for k, v in payload.items() if k != "signature"}
        serialized = json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
        return hmac.new(secret, serialized, hashlib.sha256).hexdigest()

    def verify_payload(self, source: str, payload: dict, signature: str) -> bool:
        """
        Verify HMAC-SHA256 signature using constant-time digest comparison.
        Shields against timing attacks.
        """
        if not signature:
            self._tampered_count += 1
            return False

        secret = self._secrets.get(source)
        if not secret:
            self._tampered_count += 1
            return False

        filtered = {k: v for k, v in payload.items() if k != "signature"}
        serialized = json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
        expected_sig = hmac.new(secret, serialized, hashlib.sha256).hexdigest()

        is_valid = hmac.compare_digest(expected_sig, signature)
        if is_valid:
            self._verified_count += 1
        else:
            self._tampered_count += 1
        return is_valid

    def authorize(self, actor_role: Role, required_role: Role, action_name: str = "") -> None:
        """Enforce Role-Based Access Control hierarchy."""
        if _ROLE_HIERARCHY.get(actor_role, 0) < _ROLE_HIERARCHY.get(required_role, 99):
            raise PermissionError(
                f"Access denied: Action '{action_name}' requires role '{required_role.value}', "
                f"but actor has '{actor_role.value}'"
            )

    def log_audit(
        self,
        action: str,
        actor: str = "system",
        role: Role = Role.OPERATOR,
        details: str = "",
        timestamp: Optional[float] = None,
    ) -> str:
        """
        Record a cryptographically chained audit log entry.
        Each entry seals the previous entry's hash in a tamper-evident Merkle link.
        """
        if timestamp is None:
            timestamp = time.time()

        prev_hash = "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        if self.store and hasattr(self.store, "get_latest_audit_hash"):
            prev_hash = self.store.get_latest_audit_hash()

        payload_str = f"{prev_hash}|{timestamp:.6f}|{actor}|{role.value}|{action}|{details}"
        entry_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        if self.store and hasattr(self.store, "write_audit_entry"):
            self.store.write_audit_entry(
                timestamp=timestamp,
                actor=actor,
                role=role.value,
                action=action,
                details=details,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
            )
            if hasattr(self.store, "commit"):
                self.store.commit()

        return entry_hash

    def verify_audit_trail(self) -> Tuple[bool, str, int]:
        """Validate entire audit trail integrity from genesis to the latest entry."""
        if not self.store or not hasattr(self.store, "verify_audit_integrity"):
            return False, "No persistent store configured for audit validation", 0
        return self.store.verify_audit_integrity()

    def stats(self) -> dict:
        return {
            "verified_hmac_signatures": self._verified_count,
            "tampered_or_invalid_signatures": self._tampered_count,
            "rate_limited_events": self._rate_limited_count,
            "registered_feeds": list(self._secrets.keys()),
        }
