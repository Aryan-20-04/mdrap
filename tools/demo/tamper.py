#!/usr/bin/env python3
"""Tamper with a single audit_log record in SQLite to demonstrate Merkle chain failure."""
import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else "data/demo_recording.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("UPDATE audit_log SET details = 'Tampered record injected by adversary' WHERE rowid = 1")
conn.commit()
conn.close()
print(f"[tamper] Injected malicious alteration into {db_path} rowid 1.")
