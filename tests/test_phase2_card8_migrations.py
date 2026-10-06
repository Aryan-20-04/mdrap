import os
import sys
import tempfile
import sqlite3
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from storage import Store, CURRENT_SCHEMA_VERSION


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

        with pytest.raises(RuntimeError, match="Unsupported database schema version 99"):
            Store(db_path)
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
