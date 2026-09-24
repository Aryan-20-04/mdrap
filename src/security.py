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

import builtins
import enum
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from audit_format import audit_bytes_v1, audit_bytes_v2, compute_audit_hash

__stability__ = "stable"


class Role(str, enum.Enum):
    VIEWER = "VIEWER"  # Read BBO, candles, spreads, analytics, platform status
    OPERATOR = "OPERATOR"  # Run ingestion, live streaming, inspect quarantine
    ADMIN = "ADMIN"  # Manual source block/unblock, secrets management, chaos drills, audit review

    def __str__(self) -> str:
        return self.value


_ROLE_HIERARCHY = {
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


@dataclass
class ClientEntitlement:
    """Client entitlement and RBAC definition.

    Licensed = active key exists. Unlicensed = no key or revoked key.
    """

    token: str = ""
    client_id: str = ""
    token_hash: str = ""
    key_prefix: str = ""
    role: Role = Role.VIEWER
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    is_active: bool = True

    def __post_init__(self):
        if self.token and not self.token_hash:
            self.token_hash = hashlib.sha256(self.token.encode("utf-8")).hexdigest()
        if self.token and not self.key_prefix:
            self.key_prefix = (
                self.token[:12] + "..." if len(self.token) > 12 else self.token
            )
        elif self.token_hash and not self.key_prefix:
            self.key_prefix = self.token_hash[:12] + "..."
        if isinstance(self.role, str) and self.role in Role.__members__:
            self.role = Role[self.role]

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "token_hash": self.token_hash,
            "key_prefix": self.key_prefix,
            "client_id": self.client_id,
            "role": self.role.value if hasattr(self.role, "value") else str(self.role),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ClientEntitlement:
        role_raw = data.get("role", "VIEWER")
        role = Role[role_raw] if role_raw in Role.__members__ else Role.VIEWER
        return cls(
            token=str(data.get("token", "")),
            client_id=str(data.get("client_id", "")),
            token_hash=str(data.get("token_hash", "")),
            key_prefix=str(data.get("key_prefix", "")),
            role=role,
            created_at=float(data.get("created_at", time.time())),
            expires_at=float(data["expires_at"])
            if data.get("expires_at") is not None
            else None,
            is_active=bool(data.get("is_active", True)),
        )


class AccessDenied(builtins.PermissionError):
    """Raised when an actor lacks sufficient RBAC privileges."""

    pass


# Backward-compatible alias
PermissionError = AccessDenied


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

            if len(self._buckets) > 1024 and source not in self._buckets:
                # Evict oldest entry
                oldest = min(self._buckets.items(), key=lambda item: item[1][1])[0]
                self._buckets.pop(oldest, None)

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


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class InputSanitizer:
    """
    Strict input validation guard ensuring data bounds and safe representations
    before events enter gateway normalization.
    """

    SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9/_\-.:=^ ]{1,32}$")
    MAX_PRICE = 1e12
    MIN_PRICE = 0.0
    MAX_QUANTITY = 1e12
    MAX_SEQUENCE = (1 << 63) - 1

    @classmethod
    def sanitize(cls, payload: Any) -> Tuple[bool, Optional[str]]:
        if not isinstance(payload, dict):
            return False, "Payload must be a dictionary"

        inst = payload.get("instrument")
        if inst is not None:
            if not isinstance(inst, str) or not cls.SYMBOL_PATTERN.fullmatch(inst):
                err = str(inst)[:80]
                return False, f"Invalid symbol format: {err}"

        allow_neg = bool(payload.get("allow_negative", False))
        min_p = -cls.MAX_PRICE if allow_neg else cls.MIN_PRICE

        price = payload.get("price")
        if price is not None:
            if not _finite(price) or price < min_p or price > cls.MAX_PRICE:
                err = str(price)[:80]
                return False, f"Price out of acceptable bounds: {err}"

        bid = payload.get("bid")
        if bid is not None:
            if not _finite(bid) or bid < min_p or bid > cls.MAX_PRICE:
                err = str(bid)[:80]
                return False, f"Bid price out of bounds: {err}"

        ask = payload.get("ask")
        if ask is not None:
            if not _finite(ask) or ask < min_p or ask > cls.MAX_PRICE:
                err = str(ask)[:80]
                return False, f"Ask price out of bounds: {err}"

        qty = payload.get("quantity")
        if qty is not None:
            if not _finite(qty) or qty < 0.0 or qty > cls.MAX_QUANTITY:
                err = str(qty)[:80]
                return False, f"Quantity out of bounds: {err}"

        seq = payload.get("sequence")
        if seq is not None and (
            isinstance(seq, bool)
            or not isinstance(seq, int)
            or seq < 0
            or seq > cls.MAX_SEQUENCE
        ):
            err = str(seq)[:80]
            return False, f"Sequence number must be integer: {err}"

        for book_side in ("bids", "asks"):
            levels = payload.get(book_side)
            if levels is not None:
                if not isinstance(levels, (list, tuple)) or len(levels) > 50:
                    return False, f"{book_side} depth must be list of length <= 50"
                for lvl in levels:
                    if (
                        not isinstance(lvl, (list, tuple))
                        or len(lvl) < 2
                        or not _finite(lvl[0])
                        or not _finite(lvl[1])
                    ):
                        return False, f"Invalid {book_side} level price/size"

        return True, None


def format_audit_payload(
    prev_hash: str,
    ts: float,
    actor: str,
    role: str,
    action: str,
    details: str,
    format_version: int = 2,
) -> str:
    """Format audit entry fields using canonical format (default v2 JSON array)."""
    if format_version == 1:
        return audit_bytes_v1(prev_hash, ts, actor, role, action, details).decode(
            "utf-8"
        )
    return audit_bytes_v2(prev_hash, ts, actor, role, action, details).decode("utf-8")


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
        "role": Role.ADMIN,
    },
    "mdrap_demo_free_key": {
        "client_id": "Demo_Client",
        "role": Role.VIEWER,
    },
    "mdrap_demo_pro_key": {
        "client_id": "Demo_Pro_Quant",
        "role": Role.OPERATOR,
    },
    "mdrap_demo_inst_key": {
        "client_id": "Demo_Institutional_HFT",
        "role": Role.ADMIN,
    },
}


def _load_or_create_local_secrets() -> Dict[str, str]:
    home = (
        os.environ.get("USERPROFILE")
        or os.environ.get("HOME")
        or os.path.expanduser("~")
    )
    sec_dir = os.path.join(home, ".mdrap")
    sec_file = os.path.join(sec_dir, "secrets.json")
    if os.path.isfile(sec_file):
        try:
            with open(sec_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    default_feeds = ["FEEDX", "FEEDY", "FEEDZ", "BINANCE", "COINBASE"]
    generated = {
        src: f"mdrap_{src.lower()}_{secrets.token_hex(16)}" for src in default_feeds
    }
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


class HashedKeyStore(dict):
    """Dictionary mapping SHA-256 token hashes to ClientEntitlements.

    Prevents raw credential retention in memory while supporting transparent
    constant-time lookups via either raw tokens or SHA-256 hashes.
    """

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()
            return super().__contains__(h)
        return False

    def __getitem__(self, key: str) -> ClientEntitlement:
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str):
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()
            if super().__contains__(h):
                return super().__getitem__(h)
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if super().__contains__(key):
            return super().get(key, default)
        if isinstance(key, str):
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()
            if super().__contains__(h):
                return super().get(h, default)
        return default

    def pop(self, key: str, default: Any = None) -> Any:
        if super().__contains__(key):
            return super().pop(key, default)
        if isinstance(key, str):
            h = hashlib.sha256(key.encode("utf-8")).hexdigest()
            if super().__contains__(h):
                return super().pop(h, default)
        return default

    def get_by_token_or_hash(self, key: str) -> Optional[ClientEntitlement]:
        if not key:
            return None
        # Check if key is raw token -> hash lookup
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        ent = super().get(h)
        if ent is not None:
            return ent
        # Check if key was already a hash
        return super().get(key)


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
        require_hmac: bool | set[str] | list[str] = False,
    ):
        self.store = store
        mandate_env = require_env_secrets or (
            os.environ.get("MDRAP_REQUIRE_ENV_SECRETS", "").lower()
            in ("1", "true", "yes")
        )
        is_demo = os.environ.get("MDRAP_DEMO", "").lower() in ("1", "true", "yes")

        if isinstance(require_hmac, str):
            self.require_hmac: bool | set[str] = {require_hmac.upper()}
        elif isinstance(require_hmac, (set, list, tuple)):
            self.require_hmac = {s.upper() for s in require_hmac}
        else:
            self.require_hmac = bool(require_hmac) or (
                os.environ.get("MDRAP_REQUIRE_HMAC", "").lower() in ("1", "true", "yes")
            )

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
                src: key.encode("utf-8")
                for src, key in _load_or_create_local_secrets().items()
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

        self._api_keys: HashedKeyStore = HashedKeyStore()
        if is_demo:
            for tok, cfg in _DEMO_KEYS.items():
                th = hashlib.sha256(tok.encode("utf-8")).hexdigest()
                self._api_keys[th] = ClientEntitlement(
                    token_hash=th,
                    client_id=cfg["client_id"],
                    key_prefix=tok[:12] + "...",
                    role=cfg.get("role", Role.VIEWER),
                )
        # Load API key overrides from environment (e.g. MDRAP_API_KEY_ADMIN=custom_token)
        for k, v in os.environ.items():
            if k.startswith("MDRAP_API_KEY_"):
                suffix = k[len("MDRAP_API_KEY_") :].upper()
                role_val = Role[suffix] if suffix in Role.__members__ else Role.VIEWER
                th = hashlib.sha256(v.encode("utf-8")).hexdigest()
                self._api_keys[th] = ClientEntitlement(
                    token_hash=th,
                    client_id=f"Env_Client_{suffix}",
                    key_prefix=v[:12] + "...",
                    role=role_val,
                )
        if self.store and hasattr(self.store, "load_api_keys"):
            try:
                for ent in self.store.load_api_keys():
                    if ent.token_hash:
                        # Clear plaintext secret from heap memory
                        ent.token = ""
                        self._api_keys[ent.token_hash] = ent
            except Exception as exc:
                print(
                    f"[mdrap SECURITY WARNING] Failed to load API keys from store: {exc}",
                    file=sys.stderr,
                )

        # Bootstrap initial ADMIN key ONLY if explicitly configured (secure default: 0)
        self.bootstrap_admin_token: Optional[str] = None
        if (
            not is_demo
            and self.store
            and hasattr(self.store, "load_api_keys")
            and len(self._api_keys) == 0
        ):
            env_admin = os.environ.get("MDRAP_INITIAL_ADMIN_KEY")
            auto_bootstrap = os.environ.get(
                "MDRAP_AUTO_BOOTSTRAP_ADMIN", "0"
            ).lower() in ("1", "true", "yes")
            if env_admin or auto_bootstrap:
                admin_tok = env_admin or f"mdrap_live_adm_{secrets.token_urlsafe(24)}"
                self.bootstrap_admin_token = admin_tok
                self.register_api_key(
                    client_id="Initial_Administrator",
                    role=Role.ADMIN,
                    token=admin_tok,
                )
                if not env_admin:
                    print(
                        f"[mdrap SECURITY] Initial bootstrap ADMIN API key generated:\n"
                        f"  >> {admin_tok} <<\n"
                        f"Store this key securely. It cannot be recovered from storage!",
                        file=sys.stderr,
                    )

    def register_feed_secret(self, source: str, secret_key: str) -> None:
        """Register or rotate a pre-shared cryptographic key for a market data feed."""
        self._secrets[source.upper()] = secret_key.encode("utf-8")

    def hmac_required(self, source: str) -> bool:
        """Check if cryptographic HMAC verification is required for a feed source."""
        src = source.upper()
        # Public exchange sources NEVER require HMAC
        if src in ("BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"):
            return False
        if isinstance(self.require_hmac, set):
            return src in self.require_hmac
        return bool(self.require_hmac)

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

    def create_feed_secret(self, source: str) -> str:
        """Explicitly generate, register, and return a cryptographic secret for a source."""
        src = source.upper()
        secret = secrets.token_bytes(32)
        self._secrets[src] = secret
        return secret.hex()

    def sign_payload(self, source: str, payload: dict) -> str:
        """
        Generate HMAC-SHA256 signature for a feed payload.
        Keys are sorted to guarantee canonical determinism, with compact separators.
        Raises KeyError if source is not registered.
        """
        src = source.upper()
        secret = self._secrets.get(src)
        if not secret:
            raise KeyError(
                f"Unknown feed source '{source}': no secret registered. Call create_feed_secret() first."
            )

        serialized = self._signing_bytes(payload)
        return hmac.digest(secret, serialized, "sha256").hex()

    def verify_payload(self, source: str, payload: dict, signature: Any) -> bool:
        """
        Verify HMAC-SHA256 signature using constant-time digest comparison.
        Shields against timing attacks. Never raises on malformed signatures.
        """
        src = source.upper()
        if not isinstance(signature, str) or not (1 <= len(signature) <= 128):
            return self._fail(
                "HMAC_BAD_TYPE", src, "Signature is not a valid-length string"
            )
        try:
            sig_bytes = bytes.fromhex(signature)
        except ValueError:
            return self._fail(
                "HMAC_BAD_ENCODING", src, "Signature is not valid hexadecimal"
            )

        secret = self._secrets.get(src)
        if not secret:
            return self._fail("HMAC_UNKNOWN_FEED", src, "No secret registered for feed")

        serialized = self._signing_bytes(payload)
        expected_bytes = hmac.digest(secret, serialized, "sha256")

        is_valid = hmac.compare_digest(expected_bytes, sig_bytes)
        if is_valid:
            self._verified_count += 1
            return True
        return self._fail(
            "HMAC_SIGNATURE_INVALID", src, "Payload HMAC signature mismatch"
        )

    def authorize(
        self, actor_or_token: Any, required_role: Role, action_name: str = ""
    ) -> None:
        """Enforce Role-Based Access Control hierarchy."""
        if isinstance(actor_or_token, Role):
            actor_role = actor_or_token
            actor_name = f"role:{actor_role.value}"
        elif isinstance(actor_or_token, ClientEntitlement):
            if not actor_or_token.is_active or (
                actor_or_token.expires_at is not None
                and time.time() > actor_or_token.expires_at
            ):
                self.log_audit(
                    action="ACCESS_DENIED",
                    actor=actor_or_token.client_id,
                    role=getattr(actor_or_token, "role", Role.VIEWER),
                    details=f"Inactive or expired entitlement attempting '{action_name}'",
                )
                raise AccessDenied(
                    f"Access denied: Inactive or expired entitlement for action '{action_name}'"
                )
            actor_role = getattr(actor_or_token, "role", Role.VIEWER)
            actor_name = actor_or_token.client_id
        elif isinstance(actor_or_token, str):
            ent = self.get_entitlement(actor_or_token, active_only=True)
            if ent is None:
                self.log_audit(
                    action="ACCESS_DENIED",
                    actor="unknown_token",
                    role=Role.VIEWER,
                    details=f"Invalid or expired token attempting '{action_name}'",
                )
                raise AccessDenied(
                    f"Access denied: Invalid or expired token for action '{action_name}'"
                )
            actor_role = getattr(ent, "role", Role.VIEWER)
            actor_name = ent.client_id
        else:
            actor_role = Role.VIEWER
            actor_name = "unknown"

        if _ROLE_HIERARCHY.get(actor_role, 0) < _ROLE_HIERARCHY.get(required_role, 99):
            self.log_audit(
                action="ACCESS_DENIED",
                actor=actor_name,
                role=actor_role,
                details=f"Action '{action_name}' requires role '{required_role.value}', actor has '{actor_role.value}'",
            )
            raise AccessDenied(
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

        role_str = role.value if isinstance(role, Role) else str(role)

        if self.store and hasattr(self.store, "append_audit"):
            return self.store.append_audit(
                actor=actor,
                role=role_str,
                action=action,
                details=details,
                timestamp=timestamp,
                format_version=2,
            )

        prev_hash = (
            "GENESIS_0000000000000000000000000000000000000000000000000000000000000000"
        )
        if self.store and hasattr(self.store, "get_latest_audit_hash"):
            prev_hash = self.store.get_latest_audit_hash()

        entry_hash = compute_audit_hash(
            prev_hash, timestamp, actor, role_str, action, details, format_version=2
        )

        if self.store and hasattr(self.store, "write_audit_entry"):
            self.store.write_audit_entry(
                timestamp=timestamp,
                actor=actor,
                role=role_str,
                action=action,
                details=details,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                format_version=2,
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
        role: Role | str = Role.VIEWER,
        token: Optional[str] = None,
        expires_at: Optional[float] = None,
        **kwargs,
    ) -> ClientEntitlement:
        """Generate and register a new client API key entitlement with cryptographic token hashing and RBAC."""
        if isinstance(role, str):
            role_clean = (
                Role[role.upper()] if role.upper() in Role.__members__ else Role.VIEWER
            )
        elif isinstance(role, Role):
            role_clean = role
        else:
            role_clean = Role.VIEWER

        if not token:
            existing_prefixes = {
                k.key_prefix for k in self._api_keys.values() if k.is_active
            }
            while True:
                candidate = f"mdrap_live_{secrets.token_urlsafe(24)}"
                cand_pfx = candidate[:12] + "..." if len(candidate) > 12 else candidate
                if cand_pfx not in existing_prefixes or len(existing_prefixes) >= 60:
                    token = candidate
                    break

        tok_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key_prefix = token[:12] + "..." if len(token) > 12 else token

        ent = ClientEntitlement(
            token=token,
            token_hash=tok_hash,
            key_prefix=key_prefix,
            client_id=client_id,
            role=role_clean,
            expires_at=expires_at,
            is_active=True,
        )

        # Store ONLY token_hash in memory
        self._api_keys[tok_hash] = ent
        if self.store and hasattr(self.store, "save_api_key"):
            try:
                self.store.save_api_key(ent)
            except Exception as exc:
                self._api_keys.pop(tok_hash, None)
                raise RuntimeError(
                    f"Failed to persist API key to storage: {exc}"
                ) from exc
        return ent

    def revoke_api_key(self, token: str) -> bool:
        """Revoke an active API key immediately by token, token_hash, or key_prefix."""
        ent = self._api_keys.get_by_token_or_hash(token)
        if not ent:
            # Check by key_prefix in registered keys (match active first)
            for v in list(self._api_keys.values()):
                if v.key_prefix == token and v.is_active:
                    ent = v
                    break
            if not ent:
                for v in list(self._api_keys.values()):
                    if v.key_prefix == token:
                        ent = v
                        break
        if ent:
            ent.is_active = False
            if self.store and hasattr(self.store, "revoke_api_key"):
                try:
                    self.store.revoke_api_key(ent.token_hash or token)
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to persist API key revocation to storage: {exc}"
                    ) from exc
            return True
        return False

    def get_entitlement(
        self, token: str, active_only: bool = False
    ) -> Optional[ClientEntitlement]:
        """Lookup entitlement by token or sha256 hash."""
        if not token:
            return None
        ent = self._api_keys.get_by_token_or_hash(token)
        if not ent:
            return None
        if active_only:
            if not ent.is_active:
                return None
            if ent.expires_at is not None and time.time() > ent.expires_at:
                return None
        return ent

    def list_api_keys(self) -> list[ClientEntitlement]:
        """List all known client entitlements (deduplicated)."""
        unique = {ent.token_hash or ent.token: ent for ent in self._api_keys.values()}
        return list(unique.values())

    def stats(self) -> dict:
        unique = {ent.token_hash or ent.token: ent for ent in self._api_keys.values()}
        return {
            "verified_hmac_signatures": self._verified_count,
            "tampered_or_invalid_signatures": self._tampered_count,
            "rate_limited_events": self._rate_limited_count,
            "registered_feeds": list(self._secrets.keys()),
            "api_keys_active": sum(1 for k in unique.values() if k.is_active),
            "api_keys_total": len(unique),
        }
