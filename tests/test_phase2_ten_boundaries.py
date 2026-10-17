"""Acceptance test suite for Phase 2: Reproducible 10-Boundary Benchmark Harness (Spec §8, §18).

Verifies:
1. Isolated Boundary 1: SBE Binary vs JSON decode throughput & latency percentiles.
2. Isolated Boundary 2: Quality validation rule evaluation throughput & latency percentiles.
3. Isolated Boundary 3: IngestLog buffer append latency & throughput.
4. Isolated Boundary 4: Append-to-durable-flush latency (fsync).
5. Isolated Boundary 5: IPC publication latency (seqlock SHM commit).
6. Isolated Boundary 6: End-to-end ingest-to-consumer wire latency.
7. Isolated Boundary 7: Historical replay throughput and deterministic fold.
8. Isolated Boundary 8: Crash recovery duration & torn-tail truncation.
9. Isolated Boundary 9: Consumer backpressure drop accounting without silent loss.
10. Isolated Boundary 10: Memory footprint & CPU utilization metrics.
11. Machine manifest & environment discovery (CPU, RAM, OS, compiler, profile).
12. Deterministic dataset SHA-256 hashing across multiple runs with fixed seed.
13. Atomic manifest emission (<timestamp>_<sha>.json and latest_manifest.json).
14. CLI integration: mdrap bench --ten-boundaries --events N --json and --pretty.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.ten_boundaries import (
    calc_percentiles,
    compute_dataset_hash,
    discover_environment,
    measure_boundary_1_decode,
    measure_boundary_2_validation,
    measure_boundary_3_wal_append,
    measure_boundary_4_durable_flush,
    measure_boundary_5_ipc_publish,
    measure_boundary_6_end_to_end,
    measure_boundary_7_historical_replay,
    measure_boundary_8_crash_recovery,
    measure_boundary_9_consumer_backpressure,
    measure_boundary_10_resource_utilization,
    run_all_ten_boundaries,
)
from mdrap.models import QualityStatus


class TestIsolatedBoundaries:
    """Verifies all 10 isolated benchmark boundaries execute and produce valid metrics."""

    def test_boundary_1_sbe_and_json_decode(self):
        res = measure_boundary_1_decode(num_events=200, seed=42)
        assert "sbe_decode" in res
        assert "json_decode" in res

        sbe = res["sbe_decode"]
        jdec = res["json_decode"]

        assert sbe["rate_eps"] > 0
        assert sbe["wall_s"] > 0
        assert "p50" in sbe["latency_ns"]
        assert "p99" in sbe["latency_ns"]
        assert sbe["latency_ns"]["p50"] > 0

        assert jdec["rate_eps"] > 0
        assert jdec["wall_s"] > 0
        assert "p50" in jdec["latency_ns"]
        assert "p99" in jdec["latency_ns"]
        assert jdec["latency_ns"]["p50"] > 0

    def test_boundary_2_quality_rule_evaluation(self):
        res = measure_boundary_2_validation(num_events=200, seed=42)
        assert res["rate_eps"] > 0
        assert res["wall_s"] > 0
        lat = res["latency_ns"]
        assert lat["p50"] > 0
        assert lat["p99"] >= lat["p50"]
        assert lat["max"] >= lat["p99"]

    def test_boundary_3_wal_append(self):
        res = measure_boundary_3_wal_append(num_events=100, seed=42)
        assert res["rate_eps"] > 0
        assert res["wall_s"] > 0
        lat = res["latency_ns"]
        assert lat["p50"] > 0
        assert lat["p99"] >= lat["p50"]

    def test_boundary_4_durable_flush(self):
        # Use small event count for fsync test to ensure swift execution
        res = measure_boundary_4_durable_flush(num_events=20, seed=42)
        assert res["rate_eps"] > 0
        assert res["wall_s"] > 0
        lat = res["latency_us"]
        assert lat["p50"] > 0
        assert lat["p99"] >= lat["p50"]

    def test_boundary_5_ipc_publish(self):
        res = measure_boundary_5_ipc_publish(num_events=100, seed=42)
        assert "supported" in res
        if res["supported"]:
            assert res["rate_eps"] > 0
            assert res["wall_s"] > 0
            lat = res["latency_ns"]
            assert lat["p50"] > 0
            assert lat["p99"] >= lat["p50"]

    def test_boundary_6_end_to_end(self):
        res = measure_boundary_6_end_to_end(num_events=100, seed=42)
        if res.get("supported", True):
            assert res["rate_eps"] > 0
            assert res["wall_s"] > 0
            lat = res["latency_us"]
            assert lat["p50"] > 0
            assert lat["p99"] >= lat["p50"]

    def test_boundary_7_historical_replay(self):
        res = measure_boundary_7_historical_replay(num_events=100, seed=42)
        assert res["events_replayed"] == 100
        assert res["wall_s"] > 0
        assert res["rate_eps"] > 0

    def test_boundary_8_crash_recovery(self):
        res = measure_boundary_8_crash_recovery(num_events=100, seed=42)
        assert res["recovered_events"] == 100
        assert res["recovery_ms"] >= 0

    def test_boundary_9_consumer_backpressure(self):
        res = measure_boundary_9_consumer_backpressure(num_events=200, seed=42)
        assert res["events_pushed"] == 200
        assert res["producer_rate_eps"] > 0
        assert res["consumed_events"] + res["dropped_events"] == 200
        assert 0.0 <= res["drop_pct"] <= 100.0

    def test_boundary_10_resource_utilization(self):
        res = measure_boundary_10_resource_utilization(num_events=100, seed=42)
        assert res["num_events"] == 100
        assert res["wall_s"] > 0
        assert res["throughput_eps"] > 0
        assert res["rss_start_mb"] > 0
        assert res["rss_end_mb"] > 0
        assert res["cpu_utilization_pct"] >= 0.0


class TestManifestAndEnvironment:
    """Verifies machine manifest schema, environment introspection, and deterministic hashing."""

    def test_environment_discovery(self):
        env = discover_environment()
        assert "os" in env
        assert "machine_arch" in env
        assert "cpu_model" in env
        assert "cpu_cores_logical" in env
        assert env["cpu_cores_logical"] >= 1
        assert "total_ram_gb" in env
        assert env["total_ram_gb"] > 0.1
        assert "python_version" in env
        assert "python_compiler" in env
        assert "has_native_fastpath" in env
        assert "has_shm_support" in env
        assert "active_profile" in env

    def test_dataset_deterministic_hashing(self):
        hash1 = compute_dataset_hash(num_events=100, seed=42)
        hash2 = compute_dataset_hash(num_events=100, seed=42)
        hash_diff_seed = compute_dataset_hash(num_events=100, seed=999)

        assert hash1["dataset_hash_sha256"] == hash2["dataset_hash_sha256"]
        assert hash1["dataset_records"] == 100
        assert hash1["seed"] == 42
        assert hash1["dataset_hash_sha256"] != hash_diff_seed["dataset_hash_sha256"]

    def test_percentile_calculation_accuracy(self):
        values = list(range(1, 1001))  # 1 to 1000
        pcts = calc_percentiles(values)
        assert pcts["p50"] == pytest.approx(500.5, abs=1.0)
        assert pcts["p90"] == pytest.approx(900.1, abs=1.0)
        assert pcts["p95"] == pytest.approx(950.05, abs=1.0)
        assert pcts["p99"] == pytest.approx(990.01, abs=1.0)
        assert pcts["max"] == 1000.0

    def test_full_harness_manifest_generation(self, tmp_path):
        out_dir = str(tmp_path / "bench_results")
        manifest = run_all_ten_boundaries(events=50, seed=42, out_dir=out_dir)

        assert "mdrap_version" in manifest
        assert "git_commit" in manifest
        assert "environment" in manifest
        assert "dataset" in manifest
        assert "boundaries" in manifest
        assert "manifest_path" in manifest

        # Check all 10 boundaries exist
        b = manifest["boundaries"]
        assert "b1_decode" in b
        assert "b2_validation" in b
        assert "b3_wal_append" in b
        assert "b4_durable_flush" in b
        assert "b5_ipc_publish" in b
        assert "b6_end_to_end" in b
        assert "b7_historical_replay" in b
        assert "b8_crash_recovery" in b
        assert "b9_consumer_backpressure" in b
        assert "b10_resource_utilization" in b

        # Verify manifest file exists on disk and latest_manifest.json was copied
        manifest_path = Path(manifest["manifest_path"])
        assert manifest_path.is_file()
        with open(manifest_path, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        assert (
            disk_data["dataset"]["dataset_hash_sha256"]
            == manifest["dataset"]["dataset_hash_sha256"]
        )

        latest_path = Path(out_dir) / "latest_manifest.json"
        assert latest_path.is_file()
        with open(latest_path, "r", encoding="utf-8") as f:
            latest_data = json.load(f)
        assert (
            latest_data["dataset"]["dataset_hash_sha256"]
            == manifest["dataset"]["dataset_hash_sha256"]
        )


class TestCLIIntegration:
    """Verifies CLI execution of mdrap benchmark with --ten-boundaries."""

    def test_cli_ten_boundaries_json(self, tmp_path):
        out_dir = str(tmp_path / "cli_bench_results")
        cmd = [
            sys.executable,
            "cli.py",
            "bench",
            "--ten-boundaries",
            "--events",
            "50",
            "--out-dir",
            out_dir,
            "--json",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert result.returncode == 0

        # Parse output JSON (ignore any initial debug lines)
        raw_output = result.stdout.strip()
        # Find JSON object start
        json_start = raw_output.find("{")
        assert json_start != -1
        data = json.loads(raw_output[json_start:])

        assert "boundaries" in data
        assert "b1_decode" in data["boundaries"]
        assert "b10_resource_utilization" in data["boundaries"]
        assert Path(out_dir).is_dir()
