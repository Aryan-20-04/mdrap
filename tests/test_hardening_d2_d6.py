import os
import sqlite3
import time
import pytest
from models import CanonicalEvent, EventType, QualityStatus
from storage import Store


def _make_event(event_id: str, ts: float, proc_ts: float | None = None) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=ts,
        receive_timestamp=ts,
        processing_timestamp=proc_ts if proc_ts is not None else ts,
        source="FEED1",
        sequence_number=1,
        price=150.0,
        quantity=10.0,
        bid_price=None,
        bid_size=None,
        ask_price=None,
        ask_size=None,
        quality_status=QualityStatus.VALID,
        reasons=[],
        raw_id=f"raw-{event_id}",
    )


def test_d2_durability_modes(tmp_path):
    p_fast = str(tmp_path / "fast.db")
    with Store(p_fast, durability="fast") as s_fast:
        res = s_fast.conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert res == 0  # OFF
        assert s_fast.durability == "fast"

    p_bal = str(tmp_path / "bal.db")
    with Store(p_bal, durability="balanced") as s_bal:
        res = s_bal.conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert res == 1  # NORMAL
        assert s_bal.durability == "balanced"

    p_comp = str(tmp_path / "comp.db")
    with Store(p_comp, durability="compliance") as s_comp:
        res = s_comp.conn.execute("PRAGMA synchronous;").fetchone()[0]
        assert res == 2  # FULL
        assert s_comp.durability == "compliance"


def test_d3_schema_indexes():
    with Store(":memory:") as store:
        cur = store.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {r[0] for r in cur.fetchall()}
        # idx_canonical_instrument should be dropped in favor of covering index
        assert "idx_canonical_instrument" not in indexes
        assert "idx_canonical_covering" in indexes
        assert "idx_canonical_proc_ts" in indexes
        assert "idx_canonical_src_seq" in indexes


def test_d5_chunked_retention(tmp_path):
    p = str(tmp_path / "retention.db")
    with Store(p) as store:
        now = time.time()
        old_ts = now - (35 * 86400)
        recent_ts = now - (5 * 86400)

        # Insert 6000 old events (to test chunking > 5000) and 100 recent events
        old_events = [_make_event(f"old_{i}", ts=old_ts, proc_ts=old_ts) for i in range(6000)]
        recent_events = [_make_event(f"recent_{i}", ts=recent_ts, proc_ts=recent_ts) for i in range(100)]
        store.write_canonical_batch(old_events)
        store.write_canonical_batch(recent_events)
        store.commit()

        # Insert old quarantine records
        old_q = [
            (f"q_old_{i}", "AAPL", "FEED1", "INVALID", '["BAD"]', '{"raw":1}', old_ts)
            for i in range(50)
        ]
        store.write_quarantine_batch(old_q)
        store.commit()

        # Run retention with retain_days=30, quarantine_days=30
        res = store.retention_compact(retain_days=30, quarantine_days=30)
        assert res["deleted_canonical"] == 6000
        assert res["deleted_quarantine"] == 50

        # Verify only recent events remain
        counts = store.counts()
        assert counts.get("VALID", 0) == 100


def test_d6_transaction_context_and_retry():
    with Store(":memory:") as store:
        # Test transaction commit
        with store.transaction():
            store.conn.execute(
                "INSERT INTO canonical_events (event_id, instrument_id, quality_status) VALUES ('tx1', 'AAPL', 'VALID')"
            )
        cur = store.conn.execute("SELECT COUNT(*) FROM canonical_events WHERE event_id='tx1'")
        assert cur.fetchone()[0] == 1

        # Test transaction rollback
        with pytest.raises(ValueError):
            with store.transaction():
                store.conn.execute(
                    "INSERT INTO canonical_events (event_id, instrument_id, quality_status) VALUES ('tx2', 'AAPL', 'VALID')"
                )
                raise ValueError("Force rollback")

        cur = store.conn.execute("SELECT COUNT(*) FROM canonical_events WHERE event_id='tx2'")
        assert cur.fetchone()[0] == 0


def test_d7_query_limit_capping():
    with Store(":memory:") as store:
        # Request limit = 500000; should be capped internally
        res = store.query_events(limit=500000)
        assert isinstance(res, list)


def test_d10_atomic_audit_export(tmp_path):
    p = str(tmp_path / "audit.db")
    with Store(p) as store:
        store.write_audit_entry(
            timestamp=100.0,
            actor="admin",
            role="SUPERUSER",
            action="LOGIN",
            details="Successful login",
            prev_hash="GENESIS_0000000000000000000000000000000000000000000000000000000000000000",
            entry_hash="hash1",
            format_version=2,
        )
        store.commit()

        out_json = str(tmp_path / "proof.json")
        proof = store.export_audit_proof(out_json)
        assert os.path.exists(out_json)
        assert proof["total_entries"] == 1
        assert proof["latest_hash"] == "hash1"
