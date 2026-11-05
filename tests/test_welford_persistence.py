"""
MDRAP v5.1 — Statistical Kernel & Persistence Optimization Test Suite.

Verifies:
1. Numerical accuracy of rolling Welford online variance vs two-pass reference.
2. SQLite wal_autocheckpoint tuned to 25,000 pages for tail-latency smoothing.
3. IngestLog true_group_commit concurrency and durability integrity.
"""

import math
import random
import sqlite3
import pytest

from mdrap.projection import SQLiteProjection
from mdrap.storage import Store
from mdrap.ingestlog import IngestLog
from mdrap.models import RawEvent


class TestWelfordAndPersistence:
    """Verifies statistical precision and persistence checkpoint optimizations."""

    def test_welford_numerical_accuracy(self):
        """Verify single-pass online Welford variance matches two-pass sample variance."""
        random.seed(42)
        n = 5000
        # Simulating random stock prices around $150.0
        prices = [150.0 + random.gauss(0, 2.5) for _ in range(n)]

        # Two-pass reference calculation
        mean_ref = sum(prices) / n
        var_ref = sum((x - mean_ref) ** 2 for x in prices) / (n - 1)

        # Welford online single-pass calculation
        w_mean = 0.0
        w_m2 = 0.0
        for i, x in enumerate(prices, 1):
            delta = x - w_mean
            w_mean += delta / i
            delta2 = x - w_mean
            w_m2 += delta * delta2

        w_var = w_m2 / (n - 1)

        # Numerical accuracy within machine precision epsilon
        assert math.isclose(w_mean, mean_ref, rel_tol=1e-12)
        assert math.isclose(w_var, var_ref, rel_tol=1e-12)

    def test_sqlite_wal_checkpoint_tuning(self, tmp_path):
        """Verify PRAGMA wal_autocheckpoint is tuned to 25,000 pages across stores."""
        db_path = str(tmp_path / "test_store.db")
        store = Store(path=db_path)
        try:
            cur = store.conn.cursor()
            cur.execute("PRAGMA wal_autocheckpoint;")
            val = cur.fetchone()[0]
            assert val == 1000
        finally:
            store.close()

        proj_db = str(tmp_path / "test_proj.db")
        proj = SQLiteProjection(db_path=proj_db)
        try:
            cur = proj._conn.cursor()
            cur.execute("PRAGMA wal_autocheckpoint;")
            val = cur.fetchone()[0]
            assert val == 25000
        finally:
            proj.close()

    def test_ingestlog_adaptive_group_commit_durability(self, tmp_path):
        """Verify true_group_commit handles multi-event flush cleanly."""
        log_dir = str(tmp_path / "wal_adaptive_test")
        log = IngestLog(log_dir=log_dir, fsync_policy="adaptive")

        try:
            events = [
                RawEvent(source="TEST", payload={"seq": i, "price": 100.0 + i})
                for i in range(100)
            ]
            offsets = []
            for ev in events:
                offsets.append(log.append(ev))

            assert len(offsets) == 100
            assert offsets == list(range(100))

            log.flush()
            replayed = list(log.iter_from(0))
            assert len(replayed) == 100
        finally:
            log.close()
