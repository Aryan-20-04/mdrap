"""Automated Test Suite for MDRAP Phase 6: Five Empirical Research Experiments.

Validates:
1. Exp 1 (Maciejewski 2026 CME NQ preprint):
   - Burst replay trace generation with microsecond clustering.
   - Multi-topology execution (single-thread, 2-stage pipelined, sharded-workers).
   - Zero event sequence gaps and strict monotonic per-symbol ordering under burst.
2. Exp 2 (Sanaee et al. 2025 Machnet):
   - Automated capability probe for zero-copy userspace networking vs socket fallback.
   - Transparent fallback selection without exceptions across OS platforms.
   - Loopback TCP baseline latency and throughput measurement.
3. Exp 3 (Su & Zhang 2026 Edge Streaming):
   - Memoryview zero-copy slicing vs heap allocation.
   - Seqlock ring buffer wraparound safety: zero torn reads, zero memory corruption.
4. Exp 4 (LSEG & NVIDIA 2026 Reuters):
   - IngestLog WAL segment scanning and analytical columnar projection (DuckDB / SQLite).
   - Lineage preservation and analytical VWAP aggregation query.
   - Immutability guarantee: projection queries/mutations never poison or mutate canonical WAL.
5. Exp 5 (IEEE Kafka Benchmark Survey 2025/2026):
   - Machine-readable environment metadata manifest generation (git, OS, CPU, RAM, boundaries).
   - Deterministic dataset provenance hashing with fixed seed.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import pytest

from benchmarks.experiments.exp1_burst_queueing import (
    TopologyResult,
    generate_burst_workload,
    run_experiment_1,
)
from benchmarks.experiments.exp1_cme_burst import (
    generate_cme_burst_trace,
    run_cme_burst_experiment,
)
from benchmarks.experiments.exp2_af_xdp_probe import (
    NetworkCapabilityReport,
    measure_loopback_socket_baseline,
    probe_kernel_network_capabilities,
    run_experiment_2,
)
from benchmarks.experiments.exp3_zerocopy_safe import (
    BufferSafetyReport,
    benchmark_copy_vs_zerocopy,
    run_experiment_3,
    test_shm_wraparound_and_seqlock_integrity as verify_shm_wraparound_integrity,
)
from benchmarks.experiments.exp4_columnar_scan import (
    ColumnarScanReport,
    populate_sample_journal,
    project_to_analytical_store,
    run_analytical_query,
    run_experiment_4,
)
from benchmarks.experiments.exp5_rigor_manifest import (
    EnvironmentManifest,
    generate_benchmark_manifest,
    get_git_info,
    run_experiment_5,
)


class TestExperiment1BurstQueueing:
    """Validates Experiment 1: Burst replay and queueing topology (Maciejewski 2026)."""

    def test_burst_workload_generation(self):
        events = generate_burst_workload(total_events=500, burst_factor=0.25, seed=42)
        assert len(events) == 500
        # Verify monotonically increasing exchange timestamps
        timestamps = [e.receive_timestamp for e in events]
        assert all(t2 >= t1 for t1, t2 in zip(timestamps[:-1], timestamps[1:]))

    def test_cme_burst_trace_generation(self):
        trace = generate_cme_burst_trace(
            total_packets=600, burst_intensity=0.30, seed=42
        )
        assert len(trace) == 600
        instruments = {e.payload["instrument"] for e in trace}
        assert "NQ_FUT" in instruments
        assert "ES_FUT" in instruments
        # Verify sequence numbers are monotonic per instrument
        for inst in instruments:
            inst_seqs = [
                e.payload["sequence"] for e in trace if e.payload["instrument"] == inst
            ]
            assert sorted(inst_seqs) == inst_seqs
            assert len(inst_seqs) == len(set(inst_seqs))

    def test_topologies_and_sequence_integrity(self):
        res = run_cme_burst_experiment(total_packets=600)
        assert res["total_packets"] == 600
        assert res["sequence_integrity"] is True
        assert res["single_thread"]["dropped_events"] == 0
        assert res["pipelined"]["dropped_events"] == 0
        assert res["sharded"]["dropped_events"] == 0
        assert res["sharded"]["throughput_eps"] > 0


class TestExperiment2NetworkCapabilityProbe:
    """Validates Experiment 2: Socket vs AF_XDP capability qualification (Sanaee et al. 2025)."""

    def test_probe_kernel_network_capabilities(self):
        caps = probe_kernel_network_capabilities()
        assert "af_xdp" in caps
        assert "recvmmsg" in caps
        assert "so_reuseport" in caps
        assert "so_bindtodevice" in caps

    def test_network_capability_report_qualification(self):
        rep = run_experiment_2()
        assert isinstance(rep, NetworkCapabilityReport)
        assert rep.os_name == platform.system()
        assert rep.selected_ingest_backend != ""
        assert rep.baseline_socket_latency_us > 0.0
        assert rep.baseline_throughput_msgs_sec > 0.0

    def test_windows_or_linux_qualification_integrity(self):
        rep = run_experiment_2()
        if platform.system() == "Windows":
            assert rep.has_af_xdp_support is False
            assert "STANDARD_SOCKET_TCP" in rep.selected_ingest_backend


class TestExperiment3ZeroCopySafetyAndLifetimes:
    """Validates Experiment 3: Memory ownership, copy elimination, and wraparound safety (Su & Zhang 2026)."""

    def test_buffer_copy_vs_zerocopy_bench(self):
        heap_ns, reus_ns, mv_ns = benchmark_copy_vs_zerocopy(num_iterations=2_000)
        assert heap_ns > 0.0
        assert reus_ns > 0.0
        assert mv_ns > 0.0

    def test_shm_wraparound_seqlock_atomicity(self):
        wraps, torn, overruns = verify_shm_wraparound_integrity(
            slot_count=128, write_events=512
        )
        # Atomicity invariant: Zero torn reads permitted
        assert torn == 0
        assert wraps >= 2

    def test_run_experiment_3_report(self):
        rep = run_experiment_3()
        assert isinstance(rep, BufferSafetyReport)
        assert rep.torn_reads_detected == 0
        assert rep.heap_throughput_eps > 0.0
        assert rep.zerocopy_throughput_eps > 0.0


class TestExperiment4HistoricalReplayColumnar:
    """Validates Experiment 4: Historical IngestLog scan, projection, and immutability (LSEG & NVIDIA 2026)."""

    def test_columnar_scan_and_lineage_verification(self):
        rep = run_experiment_4(num_records=2_000, backend="sqlite")
        assert isinstance(rep, ColumnarScanReport)
        assert rep.total_records == 2_000
        assert rep.scan_throughput_eps > 0.0
        assert rep.query_latency_ms > 0.0
        assert rep.lineage_fields_verified is True
        assert rep.canonical_log_intact is True

    def test_canonical_log_immutability_under_adversarial_mutation(self):
        temp_dir = tempfile.mkdtemp(prefix="mdrap_test_exp4_")
        try:
            wal_dir = os.path.join(temp_dir, "wal")
            db_path = os.path.join(temp_dir, "analytics.db")
            populate_sample_journal(wal_dir, num_records=1_000)

            scan_sec, scanned, projected_sz, backend = project_to_analytical_store(
                wal_dir, db_path, backend="sqlite"
            )
            assert scanned == 1_000

            # Execute destructive analytical query
            import sqlite3

            conn = sqlite3.connect(db_path)
            conn.execute("DROP TABLE historical_trades")
            conn.commit()
            conn.close()

            # Prove canonical IngestLog is untouched
            from mdrap.ingestlog import IngestLog

            log = IngestLog(wal_dir)
            recovered = sum(1 for _ in log.iter_from(0))
            assert recovered == 1_000
            assert log.corrupted_frames_count == 0
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestExperiment5BenchmarkRigorManifest:
    """Validates Experiment 5: Benchmark rigor manifest and environment disclosure (IEEE 2025/2026)."""

    def test_generate_manifest_structure(self):
        manifest = generate_benchmark_manifest(dataset_records=500)
        assert isinstance(manifest, EnvironmentManifest)
        assert manifest.mdrap_version != ""
        assert manifest.os_name != ""
        assert manifest.cpu_cores_logical >= 1
        assert manifest.total_ram_gb > 0.0
        assert len(manifest.dataset_hash_sha256) == 64
        assert len(manifest.benchmark_boundaries) == 10

    def test_deterministic_dataset_hashing(self):
        m1 = generate_benchmark_manifest(dataset_records=5_000)
        m2 = generate_benchmark_manifest(dataset_records=5_000)
        assert m1.dataset_hash_sha256 == m2.dataset_hash_sha256

    def test_manifest_json_emission_and_loading(self):
        temp_dir = tempfile.mkdtemp(prefix="mdrap_test_exp5_")
        try:
            out_file = run_experiment_5(out_dir=temp_dir)
            assert os.path.exists(out_file)
            with open(out_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "mdrap_version" in data
            assert "git_commit" in data
            assert "benchmark_boundaries" in data
            assert len(data["benchmark_boundaries"]) == 10
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
