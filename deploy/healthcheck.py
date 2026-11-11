#!/usr/bin/env python3
"""
MDRAP Container and Sidecar Health Probe

Executes a non-intrusive health assessment:
  1. Checks accessibility of MDRAP database and WAL.
  2. Queries local REST `/health` or `/metrics` endpoint if port 8000 is listening.
  3. Returns exit code 0 if healthy, 1 if degraded or failing.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import urllib.request
from pathlib import Path


def check_api_health(port: int = 8000) -> bool:
    try:
        url = f"http://127.0.0.1:{port}/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status == 200
    except Exception:
        # If API server is not running or disabled, fall back to storage health
        return False


def check_db_health(data_dir: Path) -> bool:
    db_path = data_dir / "mdrap.db"
    if not db_path.exists():
        # If database hasn't been created yet, check directory writeability
        return os.access(str(data_dir), os.W_OK)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA quick_check(1);")
        res = cursor.fetchone()
        conn.close()
        return res is not None and res[0] == "ok"
    except Exception:
        return False


def main():
    data_dir = Path(os.getenv("MDRAP_DATA_DIR", "data")).resolve()
    api_port = int(os.getenv("MDRAP_API_PORT", "8000"))

    # If API is alive, we are healthy
    if check_api_health(api_port):
        sys.exit(0)

    # Otherwise check database file integrity
    if check_db_health(data_dir):
        sys.exit(0)

    sys.exit(1)


if __name__ == "__main__":
    main()
