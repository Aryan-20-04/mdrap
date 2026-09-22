"""
Tests for Pipeline Hardening (P3, P5, P6, P7, P8, P12).
"""
import gc
import json
import os
import shutil
import pytest
from pipeline import tuned_gc, _safe_payload_json, _code_version, Pipeline
from storage import Store
from models import RawEvent


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


def test_flush_transactional_dead_letter_spill(tmp_path):
    class FailingStore:
        def __init__(self):
            self.write_count = 0
        def write_canonical_batch(self, batch):
            pass
        def write_quarantine_batch(self, batch):
            raise RuntimeError("Disk I/O Error simulated")
        def write_lineage_batch(self, batch):
            pass

    pipe = Pipeline(store=FailingStore())
    pipe._quarantine_batch.append(("evt-1", "AAPL", "FEEDX", "INVALID", "[]", "{}", 1000.0))
    
    deadletter_dir = os.path.join("data", "deadletter")
    if os.path.exists(deadletter_dir):
        shutil.rmtree(deadletter_dir)

    with pytest.raises(RuntimeError, match="Disk I/O Error simulated"):
        pipe.flush()

    assert os.path.isdir(deadletter_dir)
    files = os.listdir(deadletter_dir)
    assert len(files) >= 1
    with open(os.path.join(deadletter_dir, files[0]), "r", encoding="utf-8") as f:
        line = f.readline()
        record = json.loads(line)
        assert record["type"] == "quarantine"
        assert record["row"][0] == "evt-1"
