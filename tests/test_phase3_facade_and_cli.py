"""Acceptance test suite for Phase 3: Developer Onboarding, Facade & CLI Unification.

Verifies:
1. High-Level Facade (`mdrap.stream`):
   - Simulated tick generation ('sim:AAPL', 'sim:BTCUSDT').
   - Streaming from IngestLog directory ('wal:<path>').
   - Streaming from JSON/JSONL file ('file:<path>').
   - Streaming from seqlock SHM ring buffer ('shm:<name>').
   - Context manager ergonomics (`with mdrap.connect(...)`, `with mdrap.stream(...)`).
   - Session stats & throughput metrics.
2. System Diagnostics (`mdrap.doctor`):
   - Environment introspection (OS, CPU features, RAM).
   - Hardware profile recommendations (LITE / STANDARD / ULTRA).
   - Fsync I/O write throughput & latency.
3. Unified CLI Command Set:
   - `mdrap init`: Scaffolding validated `mdrap.toml` across profiles (auto, lite, ultra).
   - `mdrap capture`: Headless capture to IngestLog with explicit durability modes.
   - `mdrap verify`: Auditing WAL segment checksums, manifest hash chains, and gap counts.
   - `mdrap doctor`: Diagnostics and health reporting with `--json` support.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import mdrap
from mdrap.config_loader import load_config, validate_config
from mdrap.facade import StreamSession, connect, doctor, stream
from mdrap.ingestlog import IngestLog
from mdrap.models import QualityStatus, RawEvent
from mdrap.profile import HardwareProfile, ProfileMode
from mdrap.shm import HAS_SHM, SHMWriter


class TestFacadeStreamAndConnect:
    """Verifies mdrap.stream() and mdrap.connect() developer ergonomics."""

    def test_stream_sim_ticks(self):
        ticks = list(stream("sim:AAPL", count=25))
        assert len(ticks) == 25
        for t in ticks:
            assert t.instrument_id == "AAPL"
            assert t.price is not None
            assert t.price > 0
            assert t.quality_status == QualityStatus.VALID

    def test_stream_sim_crypto_pair(self):
        ticks = list(stream("sim:BTCUSDT", count=10))
        assert len(ticks) == 10
        for t in ticks:
            assert t.instrument_id == "BTCUSDT"
            assert t.price is not None
            assert 60000.0 <= t.price <= 70000.0

    def test_stream_context_manager_and_stats(self):
        with stream("sim:ETHUSD", count=50) as session:
            assert isinstance(session, StreamSession)
            count = 0
            for tick in session:
                count += 1
                if count >= 15:
                    break
            stats = session.stats()
            assert stats["produced"] >= 15
            assert stats["valid"] >= 15
            assert stats["elapsed_s"] >= 0
            assert stats["rate_eps"] > 0

    def test_connect_context_manager(self):
        with connect("sim:AAPL") as s:
            ticks = []
            for t in s:
                ticks.append(t)
                if len(ticks) >= 10:
                    break
            assert len(ticks) == 10
            assert s._stopped is False
        assert s._stopped is True

    def test_stream_from_wal_directory(self, tmp_path):
        wal_dir = str(tmp_path / "test_wal_stream")
        log = IngestLog(log_dir=wal_dir, fsync_policy="grouped_by_time")
        for i in range(30):
            raw = RawEvent(
                source="CME",
                payload={"instrument": "NQ", "price": 18500.0 + i, "quantity": 5.0, "sequence": i + 1, "exchange_ts": 1729000000.0 + i},
                receive_timestamp=1729000000.0 + i,
            )
            log.append(raw)
        log.close()

        streamed = list(stream(f"wal:{wal_dir}", count=30))
        assert len(streamed) == 30
        assert streamed[0].instrument_id == "NQ"
        assert streamed[0].price == 18500.0
        assert streamed[-1].price == 18529.0

    def test_stream_from_jsonl_file(self, tmp_path):
        jsonl_path = tmp_path / "events.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for i in range(20):
                record = {"instrument": "SPY", "price": 500.0 + i * 0.1, "quantity": 100.0, "sequence": i + 1, "exchange_ts": 1729000000.0}
                f.write(json.dumps(record) + "\n")

        streamed = list(stream(f"file:{jsonl_path}", count=20))
        assert len(streamed) == 20
        assert streamed[0].instrument_id == "SPY"
        assert streamed[0].price == pytest.approx(500.0)

    @pytest.mark.skipif(not HAS_SHM, reason="Requires shared memory support")
    def test_stream_from_shm_ring(self):
        shm_name = f"mdrap_test_shm_{os.getpid()}"
        writer = SHMWriter(shm_name, slot_count=1024)
        try:
            for i in range(1, 15):
                writer.write_tick(seq=i, symbol="MSFT", price=420.0 + i, size=10.0, exchange_ts=1729000000.0 + i, source="TEST")

            ticks = list(stream(f"shm:{shm_name}", count=10))
            assert len(ticks) >= 1
            assert ticks[0].instrument_id == "MSFT"
        finally:
            writer.close()


class TestFacadeDoctor:
    """Verifies system diagnostics and environment discovery."""

    def test_doctor_programmatic_dict(self):
        diag = doctor(verbose=False, as_json=False)
        assert diag["status"] == "HEALTHY"
        assert "environment" in diag
        assert "hardware_profile" in diag
        assert "acceleration" in diag
        assert "storage_io" in diag
        assert "config" in diag

        env = diag["environment"]
        assert env["cpu_cores_logical"] >= 1
        assert env["total_ram_gb"] > 0

        prof = diag["hardware_profile"]
        assert prof["active_mode"] in ("LITE", "STANDARD", "ULTRA")
        assert prof["recommended_mode"] in ("LITE", "STANDARD", "ULTRA")

        storage = diag["storage_io"]
        assert storage["disk_sync_speed_mbps"] > 0 or storage["disk_sync_speed_mbps"] == -1.0

    def test_doctor_json_flag(self):
        diag = doctor(verbose=False, as_json=True)
        assert isinstance(diag, dict)
        assert diag["status"] == "HEALTHY"


class TestCLICommands:
    """Verifies unified CLI commands: init, capture, verify, doctor."""

    def test_cli_init_command(self, tmp_path):
        target_toml = tmp_path / "test_init_mdrap.toml"
        cmd = [
            sys.executable,
            "cli.py",
            "init",
            str(target_toml),
            "--profile",
            "lite",
            "--venue",
            "binance",
            "--symbol",
            "ETHUSDT",
            "--json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data["status"] == "OK"
        assert target_toml.is_file()

        # Validate generated TOML content
        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib
        with open(target_toml, "rb") as f:
            cfg = tomllib.load(f)
        validate_config(cfg)
        assert cfg["defaults"]["staleness_threshold_s"] == 0.5
        assert cfg["storage"]["max_segment_bytes"] == 10 * 1024 * 1024
        assert "ETHUSDT" in cfg["venue"]["binance"]["instrument"]

    def test_cli_init_force_flag(self, tmp_path):
        target_toml = tmp_path / "existing_mdrap.toml"
        target_toml.write_text("[defaults]\nstaleness_threshold_s = 0.05\n", encoding="utf-8")

        # Without --force, must fail
        cmd_no_force = [sys.executable, "cli.py", "init", str(target_toml)]
        res_fail = subprocess.run(cmd_no_force, capture_output=True, text=True)
        assert res_fail.returncode != 0

        # With --force, succeeds
        cmd_force = [sys.executable, "cli.py", "init", str(target_toml), "--force", "--json"]
        res_ok = subprocess.run(cmd_force, capture_output=True, text=True, check=True)
        assert res_ok.returncode == 0
        assert json.loads(res_ok.stdout)["status"] == "OK"

    def test_cli_capture_and_verify(self, tmp_path):
        wal_dir = str(tmp_path / "cli_capture_wal")

        # 1. Capture 50 events
        cmd_cap = [
            sys.executable,
            "cli.py",
            "capture",
            "-s",
            "sim:AAPL",
            "-w",
            wal_dir,
            "-e",
            "50",
            "--durability",
            "grouped_by_time",
            "--json",
        ]
        res_cap = subprocess.run(cmd_cap, capture_output=True, text=True, check=True)
        cap_data = json.loads(res_cap.stdout)
        assert cap_data["events_captured"] == 50
        assert cap_data["status"] == "STOPPED"
        assert cap_data["total_segments"] >= 1
        assert Path(wal_dir).is_dir()
        assert (Path(wal_dir) / "manifest.json").is_file()

        # 2. Verify captured WAL
        cmd_ver = [
            sys.executable,
            "cli.py",
            "verify",
            wal_dir,
            "--json",
        ]
        res_ver = subprocess.run(cmd_ver, capture_output=True, text=True, check=True)
        ver_data = json.loads(res_ver.stdout)
        assert ver_data["is_clean"] is True
        assert ver_data["wal"]["valid_frames"] == 50
        assert ver_data["wal"]["corrupted_frames"] == 0
        assert ver_data["wal"]["manifest_verified"] is True

    def test_cli_doctor_json(self):
        cmd = [sys.executable, "cli.py", "doctor", "--json"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        assert data["status"] == "HEALTHY"
        assert "environment" in data
        assert "hardware_profile" in data
        assert "acceleration" in data
