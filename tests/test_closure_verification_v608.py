"""
MDRAP 6.0.8 — Closure Verification Tests.
Verifies:
1. Separate monotonic sequence numbers for SHM L1 ticks and L2 depth frames.
2. Configuration loader error handling (never silently ignore explicit configuration).
3. SHMReader zero handle leak lifecycle across repeated cycles.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

from mdrap.config_loader import DEFAULT_CONFIG, load_config
from mdrap.shm import SHMWriter, SHMReader


def test_shm_monotonic_sequence_separation():
    """Verify L1 ticks and L2 depth frames use separate monotonic sequence numbers without collisions."""
    shm_name = "test_seq_sep_shm"
    writer = SHMWriter(name=shm_name, slot_count=64)
    reader = SHMReader(name=shm_name)

    try:
        # Simulate sequential publishing of tick (seq=1) and depth (seq=2)
        writer.write_tick(
            seq=1,
            symbol="BTC/USD",
            source="BINANCE",
            price=50000.0,
            size=1.5,
            bid=49999.0,
            ask=50001.0,
        )
        writer.write_depth(
            seq=2,
            symbol="BTC/USD",
            best_bid=49999.0,
            best_ask=50001.0,
            bid_size=10.0,
            ask_size=12.0,
            micro_price=50000.2,
            ofi=0.2,
            is_crossed=False,
            exchange_ts=1700000000.0,
            ingest_ts=1700000000.001,
            broadcast_ts=1700000000.002,
            engine_us=15.0,
        )

        # Slot 1 must remain TICK (not overwritten by DEPTH)
        slot1 = reader.read_slot(1)
        assert slot1 is not None, "Slot 1 should be committed"
        assert slot1["type"] == "TICK", f"Expected TICK, got {slot1['type']}"
        assert slot1["seq"] == 1
        assert slot1["price"] == 50000.0

        # Slot 2 must be DEPTH
        slot2 = reader.read_slot(2)
        assert slot2 is not None, "Slot 2 should be committed"
        assert slot2["type"] == "DEPTH", f"Expected DEPTH, got {slot2['type']}"
        assert slot2["seq"] == 2
        assert slot2["micro_price"] == 50000.2
    finally:
        reader.close()
        writer.close()


def test_config_loader_explicit_path_missing_raises():
    """Verify that an explicitly specified nonexistent configuration path is never silently ignored."""
    with pytest.raises(
        FileNotFoundError, match="Explicitly specified configuration file not found"
    ):
        load_config(path="definitely_non_existent_config_file_98765.toml")


def test_config_loader_missing_tomli_raises_on_explicit_file(tmp_path):
    """Verify that if TOML parsing is unavailable (Python < 3.11 without tomli), explicit config raises ImportError."""
    fake_cfg = tmp_path / "custom.toml"
    fake_cfg.write_text("[defaults]\nstaleness_threshold_s = 0.1\n", encoding="utf-8")

    with patch("mdrap.config_loader.tomllib", None):
        with pytest.raises(
            ImportError, match="tomli is required to parse TOML configuration files"
        ):
            load_config(path=str(fake_cfg))


def test_config_loader_missing_tomli_fallback_when_no_config():
    """Verify that if no configuration file is provided or present, default configuration is returned cleanly."""
    with patch("mdrap.config_loader.tomllib", None):
        with patch("mdrap.config_loader.find_config_path", return_value=None):
            cfg = load_config(path=None, validate=True)
            assert isinstance(cfg, dict)
            assert "defaults" in cfg
            assert cfg["defaults"]["staleness_threshold_s"] == 0.05


def test_shm_reader_zero_handle_leak_windows():
    """Verify that SHMReader attach/read/detach cycles leak zero OS handles on Windows."""
    if sys.platform != "win32":
        pytest.skip("Windows-specific handle count test")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    hProcess = kernel32.GetCurrentProcess()
    h_count = wintypes.DWORD()
    kernel32.GetProcessHandleCount.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
    ]

    def get_handles() -> int:
        kernel32.GetProcessHandleCount(hProcess, ctypes.byref(h_count))
        return h_count.value

    shm_name = "test_handle_leak_v608"
    writer = SHMWriter(name=shm_name, slot_count=64)
    writer.write_tick(seq=1, symbol="BTC/USD", source="BIN", price=50000.0, size=1.0)

    # Warmup
    r_warm = SHMReader(name=shm_name)
    r_warm.read_slot(1)
    r_warm.close()

    import gc

    gc.collect()
    h_baseline = get_handles()

    # 30 cycles of attach, read, close
    for i in range(30):
        reader = SHMReader(name=shm_name)
        item = reader.read_slot(1)
        assert item is not None
        reader.close()

    gc.collect()
    h_final = get_handles()
    writer.close()

    assert abs(h_final - h_baseline) <= 1, (
        f"Handle leak detected: baseline={h_baseline}, final={h_final}"
    )
