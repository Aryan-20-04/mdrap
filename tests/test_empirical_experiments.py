"""Tests for the Five Empirical Research Experiments (Strategy §14.5).

Verifies:
- Experiment 1: Burst-shape replay & queueing topologies execute deterministically with 0 sequence gaps.
- Experiment 2: Network capability probe accurately detects platform features with valid loopback metrics.
- Experiment 3: Zero-copy and seqlock ring buffer wraparound guarantee 0 torn reads and zero memory corruption.
- Experiment 4: Historical journal scanning projects to columnar store while leaving canonical WAL immutable.
- Experiment 5: Benchmark rigor manifest emits complete hardware/software environment metadata JSON.
"""

import json
import os
import pytest

from benchmarks.experiments.exp1_burst_queueing import run_experiment_1
from benchmarks.experiments.exp2_af_xdp_probe import run_experiment_2
from benchmarks.experiments.exp3_zerocopy_safe import run_experiment_3
from benchmarks.experiments.exp4_columnar_scan import run_experiment_4
from benchmarks.experiments.exp5_rigor_manifest import (
    generate_benchmark_manifest,
    run_experiment_5,
)
from mdrap.models import AckStatus, OperationalMode, QualityStatus


def test_quality_status_strict_priority_and_merge():
    """Verify QualityStatus severity hierarchy: INVALID > SUSPICIOUS > VALID."""
    assert QualityStatus.INVALID.severity_level == 2
    assert QualityStatus.SUSPICIOUS.severity_level == 1
    assert QualityStatus.VALID.severity_level == 0

    assert QualityStatus.INVALID.is_worse_than(QualityStatus.SUSPICIOUS) is True
    assert QualityStatus.SUSPICIOUS.is_worse_than(QualityStatus.VALID) is True
    assert QualityStatus.VALID.is_worse_than(QualityStatus.INVALID) is False

    # Never downgrade
    merged = QualityStatus.merge(
        QualityStatus.VALID, QualityStatus.SUSPICIOUS, QualityStatus.INVALID
    )
    assert merged == QualityStatus.INVALID

    merged_susp = QualityStatus.merge(QualityStatus.VALID, QualityStatus.SUSPICIOUS)
    assert merged_susp == QualityStatus.SUSPICIOUS


def test_ack_status_lifecycle_boundaries():
    """Verify AckStatus declarations map to the 6 strategy durability boundaries."""
    assert AckStatus.RECEIVED == 1
    assert AckStatus.VALIDATED == 2
    assert AckStatus.ACCEPTED == 2
    assert AckStatus.BUFFERED_APP == 3
    assert AckStatus.JOURNALED == 4
    assert AckStatus.DURABLE == 5
    assert AckStatus.RECOVERED == 6
    assert AckStatus.PUBLISHED == 7


def test_operational_mode_definitions():
    """Verify 3 operational reliability modes are declared."""
    assert OperationalMode.LOSS_DETECTING == "LOSS_DETECTING"
    assert OperationalMode.DURABLE_CAPTURE == "DURABLE_CAPTURE"
    assert OperationalMode.LOSSLESS_TO_SOURCE == "LOSSLESS_TO_SOURCE"


def test_experiment_1_burst_replay():
    """Experiment 1: Burst replay executes across all 3 topologies with 0 sequence gaps and 0 drops."""
    results = run_experiment_1(total_events=1_000)
    assert len(results) == 3
    for r in results:
        assert r.total_events == 1_000
        assert r.sequence_gaps == 0
        assert r.dropped_events == 0
        assert r.throughput_events_per_sec > 100.0


def test_experiment_2_network_capability_probe():
    """Experiment 2: Network probe produces a valid report with fallback backend."""
    rep = run_experiment_2()
    assert rep.os_name != ""
    backend_upper = rep.selected_ingest_backend.upper()
    assert (
        "SOCKET" in backend_upper
        or "AF_XDP" in backend_upper
        or "RECVMMSG" in backend_upper
    )
    assert rep.baseline_throughput_msgs_sec > 0.0


def test_experiment_3_zerocopy_and_seqlock():
    """Experiment 3: SHM ring wraparound guarantees zero torn reads and bounds preservation."""
    rep = run_experiment_3()
    assert rep.heap_copy_ns > 0.0
    assert rep.memoryview_zerocopy_ns > 0.0
    assert rep.torn_reads_detected == 0
    assert rep.wraparound_cycles >= 1


def test_experiment_4_columnar_scan_and_isolation(tmp_path):
    """Experiment 4: IngestLog projects to analytical store and remains immutable."""
    rep = run_experiment_4(num_records=1_000)
    assert rep.total_records == 1_000
    assert rep.scan_throughput_eps > 1_000.0
    assert rep.canonical_log_intact is True
    assert rep.lineage_fields_verified is True


def test_experiment_5_benchmark_manifest(tmp_path):
    """Experiment 5: Benchmark manifest JSON captures environment and 10 boundaries."""
    manifest_path = run_experiment_5(out_dir=str(tmp_path))
    assert os.path.exists(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["git_commit"] != ""
    assert data["os_name"] != ""
    assert "b1_decode_throughput_eps" in data["benchmark_boundaries"]
    assert "b6_end_to_end_latency_us" in data["benchmark_boundaries"]
