"""Unit tests for MDRAP Hardware-Adaptive Profiler (profile.py).

Verifies:
- Accurate host CPU and physical RAM detection.
- Adaptive profile selection (Lite / Standard / Ultra).
- Low-resource bounds enforcement for 'potato PC' environments.
"""

from __future__ import annotations

import os
import sys
import pytest

from mdrap.profile import (
    HardwareProfile,
    ProfileMode,
    current_profile,
    get_total_ram_bytes,
)


def test_get_total_ram_bytes():
    """Verify physical RAM detection returns a positive integer greater than 256MB."""
    ram = get_total_ram_bytes()
    assert isinstance(ram, int)
    assert ram > 256 * 1024 * 1024  # At least 256 MB


def test_auto_detect_profile():
    """Verify that auto-detect returns a valid HardwareProfile matching host hardware."""
    prof = HardwareProfile.detect()
    assert isinstance(prof, HardwareProfile)
    assert prof.cpu_cores > 0
    assert prof.total_ram_gb > 0.0
    assert prof.mode in (ProfileMode.LITE, ProfileMode.STANDARD, ProfileMode.ULTRA)
    assert prof.ring_buffer_slots > 0
    assert prof.ring_buffer_bytes > 0


def test_profile_lite_potato_mode_bounds():
    """Verify that ProfileMode.LITE enforces strict low-memory bounds for potato PCs."""
    prof = HardwareProfile.for_mode(ProfileMode.LITE, cpu_cores=2, total_ram_gb=2.0)
    assert prof.mode == ProfileMode.LITE
    # 4MB ring buffer to protect small RAM budgets
    assert prof.ring_buffer_bytes == 4 * 1024 * 1024
    assert prof.ring_buffer_slots == 32768
    # 100ms batching to minimize disk IOPS on slow HDDs/SATA SSDs
    assert prof.wal_flush_interval_s == 0.100
    assert prof.sqlite_cache_size_kb == 8192  # 8MB cache
    assert prof.max_queue_size == 2048
    assert prof.spin_iterations == 0  # No CPU-wasting busy-spins


def test_profile_ultra_server_mode_bounds():
    """Verify that ProfileMode.ULTRA allocates high-throughput buffers for 64-core servers."""
    prof = HardwareProfile.for_mode(ProfileMode.ULTRA, cpu_cores=64, total_ram_gb=128.0)
    assert prof.mode == ProfileMode.ULTRA
    # 128MB ring buffer for multi-million tick burst absorption
    assert prof.ring_buffer_bytes == 128 * 1024 * 1024
    assert prof.ring_buffer_slots == 1048576
    # 1ms high-frequency sync
    assert prof.wal_flush_interval_s == 0.001
    assert prof.sqlite_cache_size_kb == 1048576  # 1GB cache
    assert prof.max_queue_size == 131072
    assert prof.spin_iterations == 100  # Busy-spin on hot seqlocks


def test_profile_summary_dict():
    """Verify summary() returns a serializable dictionary with all required telemetry keys."""
    prof = HardwareProfile.for_mode(ProfileMode.STANDARD)
    s = prof.summary()
    assert s["mode"] == "STANDARD"
    assert "cpu_cores" in s
    assert "total_ram_gb" in s
    assert "ring_buffer_mb" in s
    assert "wal_flush_interval_ms" in s
    assert "sqlite_cache_mb" in s
    assert "platform" in s
    assert "python_version" in s


def test_environment_variable_override(monkeypatch):
    """Verify MDRAP_PROFILE environment variable overrides auto-detection."""
    monkeypatch.setenv("MDRAP_PROFILE", "LITE")
    prof = HardwareProfile.detect()
    assert prof.mode == ProfileMode.LITE

    monkeypatch.setenv("MDRAP_PROFILE", "ULTRA")
    prof2 = HardwareProfile.detect()
    assert prof2.mode == ProfileMode.ULTRA
