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
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


class Role(str, enum.Enum):
    VIEWER = "VIEWER"  # Read BBO, candles, spreads, analytics, platform status
    OPERATOR = "OPERATOR"  # Run ingestion, live streaming, inspect quarantine
    ADMIN = "ADMIN"  # Manual source block/unblock, secrets management, chaos drills, audit review


_ROLE_HIERARCHY = {
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


class Tier(str, enum.Enum):
    """Client entitlement tier (unified platform capabilities)."""

    STANDARD = "STANDARD"
    FREE = "STANDARD"
    PRO = "STANDARD"
    INSTITUTIONAL = "STANDARD"


@dataclass
class ClientEntitlement:
    """Client entitlement, permissions, and rate limit definition."""

    token: str
    client_id: str
    rate_limit_eps: float = 20_000.0
    tier: Tier | str = Tier.STANDARD
    can_access_l2: bool = True
    can_use_binary: bool = True
    can_use_shm: bool = True
    max_replay_events: int = 100_000
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "client_id": self.client_id,
            "tier": "STANDARD",
            "rate_limit_eps": self.rate_limit_eps,
            "can_access_l2": self.can_access_l2,
            "can_use_binary": self.can_use_binary,
            "can_use_shm": self.can_use_shm,
            "max_replay_events": self.max_replay_events,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClientEntitlement:
        return cls(
            token=str(data.get("token", "")),
            client_id=str(data.get("client_id", "")),
            tier=Tier.STANDARD,
            rate_limit_eps=float(data.get("rate_limit_eps", 20000.0)),
            can_access_l2=bool(data.get("can_access_l2", True)),
            can_use_binary=bool(data.get("can_use_binary", True)),
            can_use_shm=bool(data.get("can_use_shm", True)),
            max_replay_events=int(data.get("max_replay_events", 100_000)),
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data["expires_at"])
            if data.get("expires_at") is not None
            else None,
            is_active=bool(data.get("is_active", True)),
        )


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
        self._lock = threading.Lock()

    def allow(self, source: str = "default", tokens: float = 1.0) -> bool:
        with self._lock:
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
        with self._lock:
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
            if (
                isinstance(price, bool)
                or not isinstance(price, (int, float))
                or price < cls.MIN_PRICE
                or price > cls.MAX_PRICE
            ):
                return False, f"Price out of acceptable bounds: {price}"

        bid = payload.get("bid")
        if bid is not None:
            if (
                isinstance(bid, bool)
                or not isinstance(bid, (int, float))
                or bid < 0.0
                or bid > cls.MAX_PRICE
            ):
                return False, f"Bid price out of bounds: {bid}"

        ask = payload.get("ask")
        if ask is not None:
            if (
                isinstance(ask, bool)
                or not isinstance(ask, (int, float))
                or ask < 0.0
                or ask > cls.MAX_PRICE
            ):
                return False, f"Ask price out of bounds: {ask}"

        qty = payload.get("quantity")
        if qty is not None:
            if (
                isinstance(qty, bool)
                or not isinstance(qty, (int, float))
                or qty < 0.0
                or qty > cls.MAX_QUANTITY
            ):
                return False, f"Quantity out of bounds: {qty}"

        seq = payload.get("sequence")
        if seq is not None and (
            isinstance(seq, bool) or not isinstance(seq, int) or seq < 0
        ):
            return False, f"Sequence number must be integer: {seq}"

        return True, None


def format_audit_payload(
    prev_hash: str, ts: float, actor: str, role: str, action: str, details: str
) -> str:
    """Format and escape audit entry fields to prevent delimiter collision/injection."""
    esc_actor = str(actor).replace("|", r"\|")
    esc_role = str(role).replace("|", r"\|")
    esc_action = str(action).replace("|", r"\|")
    esc_details = str(details).replace("|", r"\|")
    return f"{prev_hash}|{ts:.6f}|{esc_actor}|{esc_role}|{esc_action}|{esc_details}"


_DEMO_SECRETS = {
    "FEEDX": "mdrap_feed_secret_x_7f9a2b1c",
    "FEEDY": "mdrap_feed_secret_y_3d8e5f0a",
    "FEEDZ": "mdrap_feed_secret_z_9c4b1a7d",
    "BINANCE": "mdrap_pub_binance_key_001",
    "COINBASE": "mdrap_pub_coinbase_key_002",
}

_DEMO_KEYS = {
    "mdrap_demo_key": {
        "client_id": "Demo_Client",
        "rate_limit_eps": 50000.0,
    },
    "mdrap_demo_free_key": {
        "client_id": "Demo_Client",
        "rate_limit_eps": 50000.0,
    },
    "mdrap_demo_pro_key": {
        "client_id": "Demo_Pro_Quant",
        "rate_limit_eps": 50000.0,
    },
    "mdrap_demo_inst_key": {
        "client_id": "Demo_Institutional_HFT",
        "rate_limit_eps": 50000.0,
    },
}


def _load_or_create_local_secrets() -> Dict[str, str]:
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or os.path.expanduser("~")
    sec_dir = os.path.join(home, ".mdrap")
    sec_file = os.path.join(sec_dir, "secrets.json")
    if os.path.isfile(sec_file):
        try:
            with open(sec_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    default_feeds = ["FEEDX", "FEEDY", "FEEDZ", "BINANCE", "COINBASE"]
    generated = {src: f"mdrap_{src.lower()}_{secrets.token_hex(16)}" for src in default_feeds}
    try:
        os.makedirs(sec_dir, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(sec_file, flags, mode)
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(generated, f, indent=2)
    except Exception:
        pass
    return generated


class SecurityManager:
    """
    Central security and cryptographic coordinator for MDRAP.
    Manages HMAC feed authentication, RBAC authorization, and tamper-evident audit logs.
    """

    def __init__(
        self,
        store: Optional[Any] = None,
        rate_limit: float = 20_000.0,
        require_env_secrets: bool = False,
    ):
        self.store = store
        mandate_env = require_env_secrets or (
            os.environ.get("MDRAP_REQUIRE_ENV_SECRETS", "").lower()
            in ("1", "true", "yes")
        )
        is_demo = os.environ.get("MDRAP_DEMO", "").lower() in ("1", "true", "yes")

        self._secrets: Dict[str, bytes] = {}
        if is_demo:
            print(
                "[SECURITY WARNING] MDRAP demo mode active (MDRAP_DEMO=1). "
                "Demo keys and secrets are enabled. NEVER run this in production or on non-loopback interfaces!",
                file=sys.stderr,
            )
            self._secrets = {
                src: key.encode("utf-8") for src, key in _DEMO_SECRETS.items()
            }
        elif not mandate_env:
            self._secrets = {
                src: key.encode("utf-8") for src, key in _load_or_create_local_secrets().items()
            }

        # Pluggable secrets: load environment overrides (e.g. MDRAP_SECRET_FEEDX=...)
        for k, v in os.environ.items():
            if k.startswith("MDRAP_SECRET_"):
                source_name = k[len("MDRAP_SECRET_") :].upper()
                self._secrets[source_name] = v.encode("utf-8")

        if mandate_env and not self._secrets:
            raise ValueError(
                "MDRAP_REQUIRE_ENV_SECRETS enabled but no MDRAP_SECRET_* variables defined"
            )

        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit)
        self.sanitizer = InputSanitizer()
        self._verified_count = 0
        self._tampered_count = 0
        self._rate_limited_count = 0

        self._api_keys: Dict[str, ClientEntitlement] = {}
        if is_demo:
            for tok, cfg in _DEMO_KEYS.items():
                self._api_keys[tok] = ClientEntitlement(
                    token=tok,
                    client_id=cfg["client_id"],
                    tier=Tier.STANDARD,
                    rate_limit_eps=cfg["rate_limit_eps"],
                    can_access_l2=True,
                    can_use_binary=True,
                    can_use_shm=True,
                    max_replay_events=100_000,
                )
        # Load API key overrides from environment (e.g. MDRAP_API_KEY_PRO=custom_token)
        for k, v in os.environ.items():
            if k.startswith("MDRAP_API_KEY_"):
                suffix = k[len("MDRAP_API_KEY_") :].upper()
                self._api_keys[v] = ClientEntitlement(
                    token=v,
                    client_id=f"Env_Client_{suffix}",
                    tier=Tier.STANDARD,
                    rate_limit_eps=50000.0,
                    can_access_l2=True,
                    can_use_binary=True,
                    can_use_shm=True,
                    max_replay_events=100_000,
                )
        if self.store and hasattr(self.store, "load_api_keys"):
            try:
                for ent in self.store.load_api_keys():
                    self._api_keys[ent.token] = ent
            except Exception as exc:
                print(
                    f"[mdrap SECURITY WARNING] Failed to load API keys from store: {exc}",
                    file=sys.stderr,
                )

    def register_feed_secret(self, source: str, secret_key: str) -> None:
        """Register or rotate a pre-shared cryptographic key for a market data feed."""
        self._secrets[source.upper()] = secret_key.encode("utf-8")

    def _fail(self, reason: str, source: str, details: str = "") -> bool:
        self._tampered_count += 1
        if self.store:
            try:
                self.log_audit(
                    reason,
                    actor=source,
                    role=Role.VIEWER,
                    details=details or reason,
                )
            except Exception:
                pass
        return False

    def _signing_bytes(self, payload: dict) -> bytes:
        filtered = {k: v for k, v in payload.items() if k != "signature"}
        return json.dumps(
            filtered, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")

    def sign_payload(self, source: str, payload: dict) -> str:
        """
        Generate HMAC-SHA256 signature for a feed payload.
        Keys are sorted to guarantee canonical determinism, with compact separators.
        """
        src = source.upper()
        secret = self._secrets.get(src)
        if not secret:
            secret = secrets.token_bytes(32)
            self._secrets[src] = secret

        serialized = self._signing_bytes(payload)
        return hmac.digest(secret, serialized, "sha256").hex()

    def verify_payload(self, source: str, payload: dict, signature: Any) -> bool:
        """
        Verify HMAC-SHA256 signature using constant-time digest comparison.
        Shields against timing attacks. Never raises on malformed signatures.
        """
        src = source.upper()
        if not isinstance(signature, str) or not (1 <= len(signature) <= 128):
            return self._fail("HMAC_BAD_TYPE", src, "Signature is not a valid-length string")
        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            return self._fail("HMAC_BAD_ENCODING", src, "Signature is not valid hexadecimal")

        secret = self._secrets.get(src)
        if not secret:
            return self._fail("HMAC_UNKNOWN_FEED", src, "No secret registered for feed")

        serialized = self._signing_bytes(payload)
        expected_bytes = hmac.digest(secret, serialized, "sha256")

        is_valid = hmac.compare_digest(expected_bytes, sig_bytes)
        if is_valid:
            self._verified_count += 1
            return True
        return self._fail("HMAC_SIGNATURE_INVALID", src, "Payload HMAC signature mismatch")

    def authorize(
        self, actor_role: Role, required_role: Role, action_name: str = ""
    ) -> None:
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

        prev_hash = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        if self.store and hasattr(self.store, "get_latest_audit_hash"):
            prev_hash = self.store.get_latest_audit_hash()

        payload_str = format_audit_payload(
            prev_hash, timestamp, actor, role.value, action, details
        )
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

    def register_api_key(
        self,
        client_id: str,
        tier: Any = None,
        token: Optional[str] = None,
        rate_limit_eps: Optional[float] = None,
        can_access_l2: Optional[bool] = None,
        can_use_binary: Optional[bool] = None,
        can_use_shm: Optional[bool] = None,
        max_replay_events: Optional[int] = None,
        expires_at: Optional[float] = None,
        **kwargs,
    ) -> ClientEntitlement:
        """Generate and register a new client API key entitlement with full platform capability."""
        if not token:
            token = f"mdrap_key_{secrets.token_hex(12)}"

        ent = ClientEntitlement(
            token=token,
            client_id=client_id,
            tier=Tier.STANDARD,
            rate_limit_eps=rate_limit_eps if rate_limit_eps is not None else 20000.0,
            can_access_l2=can_access_l2 if can_access_l2 is not None else True,
            can_use_binary=can_use_binary if can_use_binary is not None else True,
            can_use_shm=can_use_shm if can_use_shm is not None else True,
            max_replay_events=max_replay_events
            if max_replay_events is not None
            else 100_000,
            expires_at=expires_at,
            is_active=True,
        )

        self._api_keys[token] = ent
        if self.store and hasattr(self.store, "save_api_key"):
            try:
                self.store.save_api_key(ent)
            except Exception as exc:
                self._api_keys.pop(token, None)
                raise RuntimeError(
                    f"Failed to persist API key to storage: {exc}"
                ) from exc
        return ent

    def revoke_api_key(self, token: str) -> bool:
        """Revoke an active API key immediately."""
        ent = self._api_keys.get(token)
        if ent:
            if self.store and hasattr(self.store, "revoke_api_key"):
                try:
                    self.store.revoke_api_key(token)
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to persist API key revocation to storage: {exc}"
                    ) from exc
            ent.is_active = False
            return True
        return False

    def get_entitlement(self, token: str) -> Optional[ClientEntitlement]:
        """Lookup entitlement by token. Returns None if invalid or missing."""
        return self._api_keys.get(token)

    def list_api_keys(self) -> list[ClientEntitlement]:
        """List all known client entitlements."""
        return list(self._api_keys.values())

    def stats(self) -> dict:
        return {
            "verified_hmac_signatures": self._verified_count,
            "tampered_or_invalid_signatures": self._tampered_count,
            "rate_limited_events": self._rate_limited_count,
            "registered_feeds": list(self._secrets.keys()),
            "api_keys_active": sum(1 for k in self._api_keys.values() if k.is_active),
            "api_keys_total": len(self._api_keys),
        }
