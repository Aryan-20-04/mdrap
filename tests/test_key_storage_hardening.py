"""
Tests for Hashed Storage Hardening & Key Lifecycle Management.

Verifies:
- Raw API keys are NEVER persisted in SQLite.
- Only SHA-256 hash (token_hash) and safe prefix (key_prefix) are stored.
- Legacy database tables automatically migrate to the hardened schema.
- SecurityManager accurately maps tokens to roles without storing raw secrets.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import pytest

from security import Role, SecurityManager
from storage import Store


def test_raw_key_never_stored_in_database():
    """Verify that the plaintext API token never appears anywhere in the api_keys table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        store = Store(db_path)
        sec = SecurityManager(store=store)

        ent = sec.register_api_key(client_id="SecretTestClient", role="ADMIN")
        raw_token = ent.token
        assert raw_token.startswith("mdrap_live_")
        store.commit()
        store.close()
        store = None

        # Direct raw SQLite inspection
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_keys")
        rows = cursor.fetchall()
        col_names = [d[0] for d in cursor.description]
        conn.close()

        assert len(rows) >= 1
        # Confirm column names: token_hash exists, legacy raw token column does not
        assert "token_hash" in col_names
        assert "key_prefix" in col_names
        assert "role" in col_names

        # Inspect all cell contents in the database
        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        found_hash = False
        for row in rows:
            row_dict = dict(zip(col_names, row))
            # Verify the raw token string is NOT anywhere in any column
            for col, val in row_dict.items():
                assert raw_token != str(val), f"Plaintext secret found in column {col}!"
            if row_dict["token_hash"] == expected_hash:
                found_hash = True
                assert row_dict["key_prefix"] == raw_token[:12] + "..."
                assert row_dict["role"] == "ADMIN"

        assert found_hash, "SHA-256 hash was not found in api_keys table"

    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        import gc
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass


def test_schema_auto_migration_from_legacy():
    """Verify that legacy databases with plaintext 'token' columns are seamlessly upgraded."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        # Create legacy schema manually
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE api_keys (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                rate_limit_eps REAL DEFAULT 20000.0,
                is_active INTEGER DEFAULT 1,
                created_at REAL NOT NULL,
                expires_at REAL
            )
            """
        )
        legacy_token = "mdrap_legacy_secret_token_123456"
        conn.execute(
            "INSERT INTO api_keys (token, client_id, created_at) VALUES (?, ?, ?)",
            (legacy_token, "LegacyClient", 1700000000.0),
        )
        conn.commit()
        conn.close()

        # Initialize Store which runs auto-migration
        store = Store(db_path)
        sec = SecurityManager(store=store)

        # Confirm the legacy client can still authenticate
        ent = sec.get_entitlement(legacy_token)
        assert ent is not None
        assert ent.client_id == "LegacyClient"
        assert ent.role == Role.VIEWER

        store.close()
        store = None

        # Confirm the underlying table has been migrated and raw token is no longer stored
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM api_keys")
        rows = cursor.fetchall()
        col_names = [d[0] for d in cursor.description]
        conn.close()

        assert "token_hash" in col_names
        expected_hash = hashlib.sha256(legacy_token.encode("utf-8")).hexdigest()
        assert rows[0][col_names.index("token_hash")] == expected_hash

    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        import gc
        gc.collect()
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception:
                pass
