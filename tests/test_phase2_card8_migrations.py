import os
import sys
import tempfile
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mdrap.storage import Store, CURRENT_SCHEMA_VERSION


def test_schema_migrations_initialization():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = Store(db_path)
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION

        migrations = store.get_applied_migrations()
        assert len(migrations) >= 3
        versions = [m["version"] for m in migrations]
        assert versions == [1, 2, 3]

        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_schema_rejects_newer_database_version():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Pre-create SQLite DB with future schema version
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA user_version = 99;")
        conn.commit()
        conn.close()

        with pytest.raises(
            RuntimeError, match="Unsupported database schema version 99"
        ):
            Store(db_path)
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass


def test_schema_auto_migrates_legacy_v1_api_keys():
    """Verify that a legacy database with v1 api_keys is auto-migrated without data loss."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE api_keys (
                token TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                expires_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO api_keys VALUES ('secret_tok_123456789', 'LegacyClient', 1, 1000.0, NULL)"
        )
        conn.commit()
        conn.close()

        # Open store which triggers auto-migration
        store = Store(db_path)
        cur = store.conn.execute(
            "SELECT token_hash, client_id, key_prefix, role FROM api_keys"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "LegacyClient"
        assert rows[0][2].startswith("secret_tok_")
        assert rows[0][3] == "VIEWER"
        store.close()
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
