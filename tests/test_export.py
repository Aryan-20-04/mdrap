"""
Unit and Integration Tests for MDRAP Data Export (Parquet, JSON, and CSV).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mdrap.export import export_data, MarketDataExporter
from mdrap.storage import Store
from mdrap.pipeline import Pipeline
from mdrap.simulator import FeedSimulator, SimulatorConfig


@pytest.fixture
def temp_env():
    """Create isolated temporary directory for test outputs and databases."""
    temp_dir = tempfile.mkdtemp(prefix="mdrap_export_test_")
    db_path = os.path.join(temp_dir, "test_mdrap.db")

    # Pre-populate database with a controlled run
    store = Store(db_path)
    pipe = Pipeline(store=store)
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=1000))
    for raw, _label in sim.generate():
        pipe.process_one(raw)
    pipe.finish()
    store.close()

    yield {
        "dir": temp_dir,
        "db": db_path,
    }

    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass


def test_export_data_csv(temp_env):
    """Verify export_data produces a valid CSV file with correct headers."""
    csv_out = os.path.join(temp_env["dir"], "test_export.csv")
    count = export_data(
        temp_env["db"], table="canonical_events", output_path=csv_out, fmt="csv"
    )
    assert count > 0
    assert os.path.exists(csv_out)

    with open(csv_out, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        assert "event_id" in header
        assert "instrument_id" in header
        rows = list(reader)
        assert len(rows) == count


def test_export_data_json_and_sql_injection_defense(temp_env):
    """Verify export_data exports valid JSON and defends against SQL injection."""
    # 1. Valid export to JSON
    json_out = os.path.join(temp_env["dir"], "valid.json")
    count = export_data(
        temp_env["db"], table="canonical_events", output_path=json_out, fmt="json"
    )
    assert count > 0
    assert os.path.exists(json_out)

    with open(json_out, "r", encoding="utf-8") as f:
        loaded = json.load(f)
        assert len(loaded) == count
        assert "instrument_id" in loaded[0]

    # 2. SQL injection attempt in table name rejected
    malicious_out = os.path.join(temp_env["dir"], "hacked.json")
    with pytest.raises(ValueError, match="does not exist in database"):
        export_data(
            temp_env["db"],
            table="canonical_events; DROP TABLE canonical_events; --",
            output_path=malicious_out,
            fmt="json",
        )


def test_export_data_parquet_or_skip(temp_env):
    """Verify export_data handles Parquet export or graceful missing dependency error."""
    parquet_out = os.path.join(temp_env["dir"], "export.parquet")
    try:
        import pyarrow  # noqa: F401

        has_pyarrow = True
    except ImportError:
        has_pyarrow = False

    if has_pyarrow:
        count = export_data(
            temp_env["db"],
            table="canonical_events",
            output_path=parquet_out,
            fmt="parquet",
        )
        assert count > 0
        assert os.path.exists(parquet_out)
    else:
        with pytest.raises(ImportError, match="pyarrow"):
            export_data(
                temp_env["db"],
                table="canonical_events",
                output_path=parquet_out,
                fmt="parquet",
            )


def test_cli_export_command(temp_env):
    """Verify CLI parser dispatches 'export' subcommand correctly to CSV."""
    from cli import build_parser

    parser = build_parser()
    out_file = os.path.join(temp_env["dir"], "cli_out.csv")
    args = parser.parse_args(
        ["export", "AAPL", "--db", temp_env["db"], "-o", out_file, "--format", "csv"]
    )
    assert args.func is not None
    assert args.symbol == "AAPL"
    assert args.db == temp_env["db"]
    assert args.output == out_file

    # Execute func directly
    args.func(args)
    assert os.path.exists(out_file)
    assert os.path.getsize(out_file) > 100


def test_market_data_exporter_compat_stub(temp_env):
    """Verify deprecated MarketDataExporter compatibility stub delegates correctly."""
    exporter = MarketDataExporter(db_path=temp_env["db"])
    out_dir = os.path.join(temp_env["dir"], "compat_out")
    files = exporter.export_csv("AAPL", output_dir=out_dir)
    assert len(files) == 1
    assert os.path.exists(files[0])
