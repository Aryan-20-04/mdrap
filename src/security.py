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


class Tier(str, enum.Enum):
    FREE = "FREE"
    PRO = "PRO"
    INSTITUTIONAL = "INSTITUTIONAL"


@dataclass
class ClientEntitlement:
    """Client entitlement, permissions, and rate tier definition."""
    token: str
    client_id: str
    tier: Tier
    rate_limit_eps: float
    can_access_l2: bool
    can_use_binary: bool
    can_use_shm: bool
    max_replay_events: int
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    is_active: bool = True

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "client_id": self.client_id,
            "tier": self.tier.value if isinstance(self.tier, Tier) else str(self.tier),
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
        tier_val = data.get("tier", "FREE")
        tier = Tier(tier_val) if tier_val in Tier._value2member_map_ else Tier.FREE
        return cls(
            token=str(data.get("token", "")),
            client_id=str(data.get("client_id", "")),
            tier=tier,
            rate_limit_eps=float(data.get("rate_limit_eps", 100.0)),
            can_access_l2=bool(data.get("can_access_l2", False)),
            can_use_binary=bool(data.get("can_use_binary", False)),
            can_use_shm=bool(data.get("can_use_shm", False)),
            max_replay_events=int(data.get("max_replay_events", 50)),
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data["expires_at"]) if data.get("expires_at") is not None else None,
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

    def allow(self, source: str = "default", tokens: float = 1.0) -> bool:
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

    DEFAULT_KEYS = {
        "mdrap_demo_free_key": {
            "client_id": "Demo_Retail_Client",
            "tier": Tier.FREE,
            "rate_limit_eps": 100.0,
            "can_access_l2": False,
            "can_use_binary": False,
            "can_use_shm": False,
            "max_replay_events": 50,
        },
        "mdrap_demo_pro_key": {
            "client_id": "Demo_Pro_Quant",
            "tier": Tier.PRO,
            "rate_limit_eps": 5000.0,
            "can_access_l2": True,
            "can_use_binary": True,
            "can_use_shm": False,
            "max_replay_events": 5000,
        },
        "mdrap_demo_inst_key": {
            "client_id": "Demo_Institutional_HFT",
            "tier": Tier.INSTITUTIONAL,
            "rate_limit_eps": 50000.0,
            "can_access_l2": True,
            "can_use_binary": True,
            "can_use_shm": True,
            "max_replay_events": 50000,
        },
    }

    def __init__(self, store: Optional[Any] = None, rate_limit: float = 20_000.0):
        self.store = store
        self._secrets: Dict[str, bytes] = {
            src: key.encode("utf-8") for src, key in self.DEFAULT_SECRETS.items()
        }
        # Pluggable secrets: load environment overrides (e.g. MDRAP_SECRET_FEEDX=...)
        import os
        for k, v in os.environ.items():
            if k.startswith("MDRAP_SECRET_"):
                source_name = k[len("MDRAP_SECRET_"):].upper()
                self._secrets[source_name] = v.encode("utf-8")

        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit)
        self.sanitizer = InputSanitizer()
        self._verified_count = 0
        self._tampered_count = 0
        self._rate_limited_count = 0

        self._api_keys: Dict[str, ClientEntitlement] = {}
        for tok, cfg in self.DEFAULT_KEYS.items():
            self._api_keys[tok] = ClientEntitlement(
                token=tok,
                client_id=cfg["client_id"],
                tier=cfg["tier"],
                rate_limit_eps=cfg["rate_limit_eps"],
                can_access_l2=cfg["can_access_l2"],
                can_use_binary=cfg["can_use_binary"],
                can_use_shm=cfg["can_use_shm"],
                max_replay_events=cfg["max_replay_events"],
            )
        if self.store and hasattr(self.store, "load_api_keys"):
            try:
                for ent in self.store.load_api_keys():
                    self._api_keys[ent.token] = ent
            except Exception:
                pass

    def register_feed_secret(self, source: str, secret_key: str) -> None:
        """Register or rotate a pre-shared cryptographic key for a market data feed."""
        self._secrets[source.upper()] = secret_key.encode("utf-8")

    def sign_payload(self, source: str, payload: dict) -> str:
        """
        Generate HMAC-SHA256 signature for a feed payload.
        Keys are sorted to guarantee canonical determinism.
        """
        src = source.upper()
        secret = self._secrets.get(src)
        if not secret:
            secret = f"default_secret_{src}".encode("utf-8")
            self._secrets[src] = secret

        # Exclude existing signature field if present to avoid recursive self-reference
        filtered = {k: v for k, v in payload.items() if k != "signature"}
        serialized = json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
        return hmac.new(secret, serialized, hashlib.sha256).hexdigest()

    def verify_payload(self, source: str, payload: dict, signature: str) -> bool:
        """
        Verify HMAC-SHA256 signature using constant-time digest comparison.
        Shields against timing attacks.
        """
        src = source.upper()
        if not signature:
            self._tampered_count += 1
            if self.store:
                self.log_audit("HMAC_MISSING", actor=src, role=Role.VIEWER, details="Payload arrived with no signature")
            return False

        secret = self._secrets.get(src)
        if not secret:
            self._tampered_count += 1
            if self.store:
                self.log_audit("HMAC_UNKNOWN_FEED", actor=src, role=Role.VIEWER, details="No secret registered for feed")
            return False

        filtered = {k: v for k, v in payload.items() if k != "signature"}
        serialized = json.dumps(filtered, sort_keys=True, default=str).encode("utf-8")
        expected_sig = hmac.new(secret, serialized, hashlib.sha256).hexdigest()

        is_valid = hmac.compare_digest(expected_sig, signature)
        if is_valid:
            self._verified_count += 1
        else:
            self._tampered_count += 1
            if self.store:
                self.log_audit("HMAC_SIGNATURE_INVALID", actor=src, role=Role.VIEWER, details="Payload HMAC signature mismatch")
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

    def register_api_key(
        self,
        client_id: str,
        tier: Tier | str = Tier.FREE,
        token: Optional[str] = None,
        rate_limit_eps: Optional[float] = None,
        can_access_l2: Optional[bool] = None,
        can_use_binary: Optional[bool] = None,
        can_use_shm: Optional[bool] = None,
        max_replay_events: Optional[int] = None,
        expires_at: Optional[float] = None,
    ) -> ClientEntitlement:
        """Generate and register a new client API key entitlement."""
        if isinstance(tier, str):
            tier = Tier(tier.upper()) if tier.upper() in Tier._value2member_map_ else Tier.FREE

        if not token:
            tier_name = tier.value.lower()
            token = f"mdrap_{tier_name}_{secrets.token_hex(12)}"

        # Default permissions according to tier
        if tier == Tier.FREE:
            d_rate = 100.0
            d_l2 = False
            d_bin = False
            d_shm = False
            d_replay = 50
        elif tier == Tier.PRO:
            d_rate = 5000.0
            d_l2 = True
            d_bin = True
            d_shm = False
            d_replay = 5000
        else:  # INSTITUTIONAL
            d_rate = 50000.0
            d_l2 = True
            d_bin = True
            d_shm = True
            d_replay = 50000

        ent = ClientEntitlement(
            token=token,
            client_id=client_id,
            tier=tier,
            rate_limit_eps=rate_limit_eps if rate_limit_eps is not None else d_rate,
            can_access_l2=can_access_l2 if can_access_l2 is not None else d_l2,
            can_use_binary=can_use_binary if can_use_binary is not None else d_bin,
            can_use_shm=can_use_shm if can_use_shm is not None else d_shm,
            max_replay_events=max_replay_events if max_replay_events is not None else d_replay,
            expires_at=expires_at,
            is_active=True,
        )

        self._api_keys[token] = ent
        if self.store and hasattr(self.store, "save_api_key"):
            try:
                self.store.save_api_key(ent)
            except Exception:
                pass
        return ent

    def revoke_api_key(self, token: str) -> bool:
        """Revoke an active API key immediately."""
        ent = self._api_keys.get(token)
        if ent:
            ent.is_active = False
            if self.store and hasattr(self.store, "revoke_api_key"):
                try:
                    self.store.revoke_api_key(token)
                except Exception:
                    pass
            return True
        return False

    def get_entitlement(self, token: str) -> Optional[ClientEntitlement]:
        """Lookup entitlement by token. Returns None if invalid or missing."""
        return self._api_keys.get(token)

    def list_api_keys(self) -> List[ClientEntitlement]:
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
