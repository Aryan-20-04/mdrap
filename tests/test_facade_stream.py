"""Unit tests for MDRAP Developer Ergonomics Facade (stream, connect, doctor).

Verifies:
- 3-line stream() generator yields valid, normalized CanonicalEvent ticks.
- connect() context manager cleans up resources on exit.
- doctor() diagnostics report contains expected hardware and environment checks.
"""

from __future__ import annotations

import pytest

import mdrap
from mdrap.models import CanonicalEvent, QualityStatus
from mdrap.profile import ProfileMode


def test_stream_simulated_ticks():
    """Verify mdrap.stream() yields requested number of validated ticks."""
    ticks = list(mdrap.stream("sim:AAPL", count=15))
    assert len(ticks) == 15
    for t in ticks:
        assert isinstance(t, CanonicalEvent)
        assert t.instrument_id == "AAPL"
        assert t.price > 0.0
        assert t.quantity > 0.0
        assert t.quality_status == QualityStatus.VALID
        assert t.exchange_timestamp > 0.0


def test_stream_crypto_symbol_price_scale():
    """Verify crypto symbol generates scaled realistic baseline prices."""
    ticks = list(mdrap.stream("sim:BTC/USDT", count=5))
    assert len(ticks) == 5
    for t in ticks:
        assert t.instrument_id == "BTC/USDT"
        assert t.price > 50000.0  # BTC realistic base around $65,000


def test_connect_context_manager():
    """Verify mdrap.connect() context manager lifecycle and early stop."""
    collected = []
    with mdrap.connect("sim:ETH/USD") as session:
        for tick in session:
            collected.append(tick)
            if len(collected) >= 7:
                break
    assert len(collected) == 7
    assert session._stopped is True


def test_doctor_diagnostic_report():
    """Verify mdrap.doctor() returns structured telemetry without crashing."""
    diag = mdrap.doctor(verbose=False)
    assert isinstance(diag, dict)
    assert "platform" in diag
    assert "cpu_cores" in diag
    assert "total_ram_gb" in diag
    assert "profile_mode" in diag
    assert "native_c_accelerator" in diag
    assert "companion_packages" in diag
    assert isinstance(diag["companion_packages"], dict)
    assert "mdrap-options" in diag["companion_packages"]
