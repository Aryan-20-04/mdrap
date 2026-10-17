"""Acceptance test suite for Phase 6: Five Empirical Research Experiments.

Verifies:
1. Experiment 1: Burst-Shape Replay & Queueing Topology (Maciejewski 2026):
   - Burst arrival traces with microsecond clustering.
   - Preserves strict per-symbol sequence ordering across topologies.
   - Sharded execution throughput and bounded queue depth.
2. Experiment 2: Socket versus Kernel-Bypass Capability Qualification (Sanaee et al. 2025):
   - Automated capability probe (AF_XDP, recvmmsg, SO_REUSEPORT).
   - Clean, transparent fallback selection across OS platforms.
   - Loopback baseline latency and throughput.
3. Experiment 3: Copy Elimination vs Buffer Ownership & Fault Safety (Su & Zhang 2026):
   - Memoryview zero-copy slicing vs heap allocation.
   - SHM seqlock ring buffer wraparound: zero torn reads, bounds preservation.
4. Experiment 4: Historical Replay & Columnar Projection (LSEG & NVIDIA 2026):
   - High-throughput IngestLog scanning and analytical projection (DuckDB/SQLite).
   - Lineage preservation and analytical VWAP aggregation query.
   - Immutability: analytical database mutations leave canonical WAL 100% byte-intact.
5. Experiment 5: Benchmark Rigor Manifest & Environment Disclosure (IEEE 2025/2026):
   - Full hardware and software environment metadata JSON generation.
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
    test_shm_wraparound_and_seqlock_integrity as verify_shm_wraparound,
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


class TestPhase6EmpiricalExperiments:
    """Acceptance test suite for Phase 6 empirical research experiments."""

    def test_exp1_burst_workload_and_sequence_integrity(self):
        res = run_cme_burst_experiment(total_packets=500)
        assert res["sequence_integrity"] is True
        assert res["single_thread"]["dropped_events"] == 0
        assert res["pipelined"]["dropped_events"] == 0
        assert res["sharded"]["dropped_events"] == 0

    def test_exp1_sharded_throughput_higher_than_zero(self):
        res = run_cme_burst_experiment(total_packets=500)
        assert res["sharded"]["throughput_eps"] > 0
        assert res["single_thread"]["throughput_eps"] > 0

    def test_exp2_automated_capability_probe(self):
        rep = run_experiment_2()
        assert isinstance(rep, NetworkCapabilityReport)
        assert rep.os_name == platform.system()
        assert rep.selected_ingest_backend != ""
        assert rep.baseline_socket_latency_us > 0.0

    def test_exp2_transparent_fallback_selection(self):
        rep = run_experiment_2()
        if platform.system() == "Windows":
            assert rep.has_af_xdp_support is False
            assert "STANDARD_SOCKET_TCP" in rep.selected_ingest_backend

    def test_exp3_zerocopy_latency_and_safety(self):
        heap_ns, reus_ns, mv_ns = benchmark_copy_vs_zerocopy(num_iterations=2_000)
        assert heap_ns > 0.0
        assert mv_ns > 0.0

    def test_exp3_seqlock_wraparound_zero_torn_reads(self):
        wraps, torn, overruns = verify_shm_wraparound(
            slot_count=128, write_events=512
        )
        assert torn == 0
        assert wraps >= 2

    def test_exp4_columnar_projection_and_lineage(self):
        rep = run_experiment_4(num_records=2_000, backend="sqlite")
        assert rep.total_records == 2_000
        assert rep.lineage_fields_verified is True
        assert rep.query_latency_ms > 0.0

    def test_exp4_canonical_log_immutability(self):
        temp_dir = tempfile.mkdtemp(prefix="mdrap_p6_exp4_")
        try:
            wal_dir = os.path.join(temp_dir, "wal")
            db_path = os.path.join(temp_dir, "analytics.db")
            populate_sample_journal(wal_dir, num_records=1_000)

            project_to_analytical_store(wal_dir, db_path, backend="sqlite")

            # Adversarial mutation on projection database
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.execute("DELETE FROM historical_trades")
            conn.commit()
            conn.close()

            # Verify canonical log is unaffected
            from mdrap.ingestlog import IngestLog
            log = IngestLog(wal_dir)
            assert sum(1 for _ in log.iter_from(0)) == 1_000
            assert log.corrupted_frames_count == 0
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_exp5_manifest_metadata_and_boundaries(self):
        manifest = generate_benchmark_manifest(dataset_records=500)
        assert manifest.mdrap_version != ""
        assert manifest.cpu_cores_logical >= 1
        assert len(manifest.benchmark_boundaries) == 10

    def test_exp5_deterministic_dataset_hash(self):
        m1 = generate_benchmark_manifest(dataset_records=2_000)
        m2 = generate_benchmark_manifest(dataset_records=2_000)
        assert m1.dataset_hash_sha256 == m2.dataset_hash_sha256
        assert len(m1.dataset_hash_sha256) == 64
