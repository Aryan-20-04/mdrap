"""MDRAP Enterprise: Multi-Tenant RBAC, API Key Rotation, and TLS Reverse Proxy.

Quarantined commercial companion package for institutional deployment.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__version__ = "1.0.0"


class EnterpriseRole(str, Enum):
    """Institutional Role-Based Access Control tiers."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    TRADER = "TRADER"
    VIEWER = "VIEWER"
    AUDITOR = "AUDITOR"


@dataclass
class Tenant:
    """Multi-tenant entity declaring symbol access and operational boundaries."""

    tenant_id: str
    tenant_name: str
    allowed_symbols: list[str] = field(default_factory=lambda: ["*"])
    rate_limit_eps: int = 100_000
    is_active: bool = True
    created_at: float = field(default_factory=time.time)

    def is_symbol_allowed(self, symbol: str) -> bool:
        if "*" in self.allowed_symbols:
            return True
        return symbol in self.allowed_symbols or symbol.upper() in self.allowed_symbols


@dataclass
class HashedKeyRecord:
    """Cryptographically salted API key record."""

    key_id: str
    tenant_id: str
    role: EnterpriseRole
    salt: str
    token_hash: str
    created_at: float
    expires_at: float | None = None
    grace_expires_at: float | None = None
    is_revoked: bool = False

    def is_valid_at(self, current_ts: float | None = None) -> bool:
        now = time.time() if current_ts is None else current_ts
        if self.is_revoked:
            return False
        if self.expires_at is not None and now > self.expires_at:
            if self.grace_expires_at is not None and now <= self.grace_expires_at:
                return True
            return False
        return True


class ApiKeyRotator:
    """Manages secure generation, salted hashing, grace-period rotation, and revocation."""

    def __init__(self, default_grace_period_sec: float = 86400.0) -> None:
        self.default_grace_period_sec = default_grace_period_sec
        self._keys_by_id: dict[str, HashedKeyRecord] = {}
        self._salt_cache: dict[str, str] = {}

    @staticmethod
    def _hash_token(raw_token: str, salt: str) -> str:
        salted = (salt + raw_token).encode("utf-8")
        return hashlib.sha256(salted).hexdigest()

    def generate_key(
        self,
        tenant_id: str,
        role: EnterpriseRole = EnterpriseRole.TRADER,
        prefix: str = "mdrap_live_",
        salt: str | None = None,
        ttl_sec: float | None = None,
    ) -> tuple[str, HashedKeyRecord]:
        """Generate a cryptographically secure token and store only its salted hash."""
        raw_secret = secrets.token_urlsafe(32)
        raw_token = f"{prefix}{raw_secret}"
        key_salt = salt or secrets.token_hex(16)
        token_hash = self._hash_token(raw_token, key_salt)
        key_id = f"key_{token_hash[:16]}"

        now = time.time()
        record = HashedKeyRecord(
            key_id=key_id,
            tenant_id=tenant_id,
            role=role,
            salt=key_salt,
            token_hash=token_hash,
            created_at=now,
            expires_at=(now + ttl_sec) if ttl_sec else None,
        )
        self._keys_by_id[key_id] = record
        return raw_token, record

    def validate_token(
        self, raw_token: str, current_ts: float | None = None
    ) -> HashedKeyRecord | None:
        """Validate token by computing salted hashes against active records."""
        now = time.time() if current_ts is None else current_ts
        for record in self._keys_by_id.values():
            if not record.is_valid_at(now):
                continue
            cand_hash = self._hash_token(raw_token, record.salt)
            if hmac.compare_digest(cand_hash, record.token_hash):
                return record
        return None

    def rotate_key(
        self,
        old_token: str,
        grace_period_sec: float | None = None,
    ) -> tuple[str, HashedKeyRecord]:
        """Rotate existing key: expires old key after grace period and returns fresh key."""
        old_record = self.validate_token(old_token)
        if not old_record:
            raise ValueError("Cannot rotate: provided old token is invalid or already expired/revoked")

        grace = grace_period_sec if grace_period_sec is not None else self.default_grace_period_sec
        now = time.time()
        old_record.expires_at = now
        old_record.grace_expires_at = now + grace

        new_raw, new_rec = self.generate_key(
            tenant_id=old_record.tenant_id,
            role=old_record.role,
        )
        return new_raw, new_rec

    def revoke_key(self, key_id_or_token: str) -> bool:
        """Immediately revoke a key by ID or raw token."""
        if key_id_or_token in self._keys_by_id:
            self._keys_by_id[key_id_or_token].is_revoked = True
            return True
        record = self.validate_token(key_id_or_token)
        if record:
            record.is_revoked = True
            return True
        return False


class TenantManager:
    """Manages multi-tenant authorization boundaries and rate policies."""

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}

    def register_tenant(
        self,
        tenant_id: str,
        name: str,
        allowed_symbols: list[str] | None = None,
        rate_limit_eps: int = 100_000,
    ) -> Tenant:
        tenant = Tenant(
            tenant_id=tenant_id,
            tenant_name=name,
            allowed_symbols=allowed_symbols or ["*"],
            rate_limit_eps=rate_limit_eps,
        )
        self._tenants[tenant_id] = tenant
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def check_access(self, tenant_id: str, symbol: str) -> bool:
        tenant = self._tenants.get(tenant_id)
        if not tenant or not tenant.is_active:
            return False
        return tenant.is_symbol_allowed(symbol)


def generate_tls_reverse_proxy_config(
    domain: str = "mdrap.institutional.internal",
    upstream_port: int = 9000,
    client_mtls: bool = True,
    cert_path: str = "/etc/ssl/certs/mdrap.crt",
    key_path: str = "/etc/ssl/private/mdrap.key",
    client_ca_path: str = "/etc/ssl/certs/client_ca.crt",
) -> str:
    """Generate production Nginx reverse proxy configuration with TLS 1.3 and mTLS."""
    mtls_block = ""
    if client_mtls:
        mtls_block = f"""
    # Mutual TLS (mTLS) Client Certificate Authentication
    ssl_client_certificate {client_ca_path};
    ssl_verify_client on;
    ssl_verify_depth 2;
"""

    return f"""# MDRAP Production Hardened TLS Reverse Proxy Config
# Generated by mdrap-enterprise

upstream mdrap_core_backend {{
    server 127.0.0.1:{upstream_port} max_fails=3 fail_timeout=10s;
    keepalive 64;
}}

server {{
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name {domain};

    # TLS 1.3 Cipher Suite Hardening
    ssl_protocols TLSv1.3 TLSv1.2;
    ssl_ciphers 'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305';
    ssl_prefer_server_ciphers on;

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};
    ssl_session_timeout 1d;
    ssl_session_cache shared:MDRAP_SSL:10m;
    ssl_session_tickets off;
{mtls_block}
    # HTTP Strict Transport Security (HSTS)
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;

    # Primary API & WebSocket Stream Proxy
    location / {{
        proxy_pass http://mdrap_core_backend;
        proxy_http_version 1.1;

        # WebSocket Upgrade Headers
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Client Identity Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Client-DN $ssl_client_s_dn;

        # Institutional Low-Latency Buffer Settings
        proxy_buffering off;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        tcp_nodelay on;
    }}
}}
"""
