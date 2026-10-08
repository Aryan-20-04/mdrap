"""
Tests for Pipeline Hardening (P3, P5, P6, P7, P8, P12).
"""

import gc
import json
import os
import shutil
import pytest
from mdrap.pipeline import (
    tuned_gc,
    _safe_payload_json,
    _code_version,
    Pipeline,
    replay_dead_letter_spills,
)
from mdrap.storage import Store
from mdrap.models import RawEvent


def test_safe_payload_json_truncation():
    small = {"k": "v"}
    assert json.loads(_safe_payload_json(small)) == {"k": "v"}

    huge_val = "x" * 70000
    huge = {"data": huge_val}
    serialized = _safe_payload_json(huge)
    parsed = json.loads(serialized)
    assert parsed["truncated"] is True
    assert "sha256" in parsed
    assert parsed["orig_bytes"] > 65536
    assert len(parsed["prefix"]) == 65536


def test_tuned_gc_context_manager():
    initial = gc.get_threshold()
    with tuned_gc():
        current = gc.get_threshold()
        assert current[0] == 50_000
    assert gc.get_threshold() == initial


def test_code_version_directory_resolution():
    ver = _code_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


def test_flush_transactional_dead_letter_spill(tmp_path, monkeypatch):
    class FailingStore:
        def __init__(self):
            self.write_count = 0

        def write_canonical_batch(self, batch):
            pass

        def write_quarantine_batch(self, batch):
            raise RuntimeError("Disk I/O Error simulated")

        def write_lineage_batch(self, batch):
            pass

    deadletter_dir = tmp_path / "spills"
    monkeypatch.setenv("MDRAP_DEAD_LETTER_DIR", str(deadletter_dir))
    pipe = Pipeline(store=FailingStore(), async_writer=False)
    pipe._quarantine_batch.append(
        ("evt-1", "AAPL", "FEEDX", "INVALID", "[]", "{}", 1000.0)
    )

    with pytest.raises(RuntimeError, match="Disk I/O Error simulated"):
        pipe.flush()

    assert deadletter_dir.is_dir()
    files = list(deadletter_dir.iterdir())
    assert len(files) >= 1
    with files[0].open("r", encoding="utf-8") as f:
        line = f.readline()
        record = json.loads(line)
        assert record["type"] == "quarantine"
        assert record["row"][0] == "evt-1"


def test_storage_and_spill_double_failure_degrades_and_raises(monkeypatch):
    from mdrap.pipeline import WriterFailure

    class FailingStore:
        def write_batches_atomic(self, *args, **kwargs):
            raise OSError("database unavailable")

    pipe = Pipeline(store=FailingStore(), async_writer=False)
    pipe._quarantine_batch.append(
        ("evt-lost", "AAPL", "FEEDX", "INVALID", "[]", "{}", 1.0)
    )

    def fail_spill(*args, **kwargs):
        raise OSError("spill unavailable")

    pipe._spill_dead_letter = fail_spill
    with pytest.raises(WriterFailure, match="Storage write and dead-letter spill"):
        pipe.flush()

    assert pipe.degraded is True
    assert pipe.metrics.writer_failures == 1
    assert pipe.metrics.writer_events_at_risk == 1
    with pytest.raises(WriterFailure):
        pipe.process_batch([RawEvent(source="TEST", payload={})])


def test_async_writer_double_failure_is_visible_and_shutdown_is_clean():
    from mdrap.pipeline import WriterFailure

    class FailingStore:
        def write_batches_atomic(self, *args, **kwargs):
            raise OSError("database unavailable")

    pipe = Pipeline(store=FailingStore(), async_writer=True)
    pipe._quarantine_batch.append(
        ("evt-async-lost", "AAPL", "FEEDX", "INVALID", "[]", "{}", 1.0)
    )

    def fail_spill(*args, **kwargs):
        raise OSError("spill unavailable")

    pipe._spill_dead_letter = fail_spill
    with pytest.raises(WriterFailure):
        pipe.flush(wait=True)
    assert pipe.degraded is True
    with pytest.raises(WriterFailure):
        pipe.process_one(RawEvent(source="TEST", payload={}))
    with pytest.raises(WriterFailure):
        pipe.finish()
    assert not pipe._writer_thread.is_alive()


def test_dead_letter_replay_does_not_silently_skip_corrupt_spill(tmp_path):
    spill = tmp_path / "spill-corrupt.jsonl"
    spill.write_text('{"_kind":"quarantine"\n', encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid dead-letter JSON"):
        replay_dead_letter_spills(Store(":memory:"), str(tmp_path))
    assert spill.exists(), "Failed spill must remain available for repair or inspection"
