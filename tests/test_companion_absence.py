"""Tests for Companion Package Absence and Graceful Fallback (P-01, P-02, P-03).

Verifies that on clean checkouts where optional domain companion packages
('packages/') are absent, the system degrades gracefully with clear install
instructions and exit code 3, without raising unhandled tracebacks.
"""

from __future__ import annotations

import os
import subprocess
import sys
import pytest

from mdrap.exceptions import CompanionPackageMissing, EXIT_COMPANION_MISSING


def test_companion_exceptions_and_stubs(monkeypatch):
    """Verify that companion stub classes raise CompanionPackageMissing with install hints."""
    monkeypatch.setenv("MDRAP_DISABLE_COMPANIONS", "1")
    for mod in (
        "mdrap.tca",
        "mdrap_analytics",
        "mdrap.strategy_sdk",
        "mdrap_strategies",
        "mdrap.vessel",
        "mdrap_vessel",
    ):
        sys.modules.pop(mod, None)

    import mdrap.tca as tca_mod
    import mdrap.strategy_sdk as strat_mod
    import mdrap.vessel as vessel_mod

    # TCAEngine
    with pytest.raises((CompanionPackageMissing, NotImplementedError)) as exc:
        _ = tca_mod.TCAEngine()
    assert "mdrap_analytics" in str(exc.value)

    # Strategy
    with pytest.raises((CompanionPackageMissing, NotImplementedError)) as exc:
        _ = strat_mod.WhaleMomentumStrategy("AAPL")
    assert "mdrap_strategies" in str(exc.value)

    # Vessel
    with pytest.raises((CompanionPackageMissing, NotImplementedError)) as exc:
        _ = vessel_mod.VesselTracker()
    assert "mdrap_vessel" in str(exc.value)


@pytest.mark.parametrize(
    "cmd_args,expected_text,expected_pkg",
    [
        (["tca", "AAPL"], "Companion Package Required", "mdrap-analytics"),
        (
            ["strategy", "run", "--strategy", "whale_momentum"],
            "Companion Package Required",
            "mdrap-strategies",
        ),
        (["vessel", "list"], "Companion Package Required", "mdrap-contrib-vessel"),
        (["report", "rts28"], "Companion Package Required", "mdrap-analytics"),
    ],
)
def test_cli_companion_absence_clean_exit(cmd_args, expected_text, expected_pkg):
    """P-02: Each companion-backed CLI command exits with code 3 and helpful install hint without traceback."""
    env = {**os.environ, "MDRAP_DISABLE_COMPANIONS": "1"}
    res = subprocess.run(
        [sys.executable, "cli.py"] + cmd_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    # Must exit with EXIT_COMPANION_MISSING (code 3)
    assert res.returncode == EXIT_COMPANION_MISSING
    combined = res.stdout + res.stderr
    assert expected_text in combined or expected_pkg in combined
    # Must never dump unhandled traceback
    assert "Traceback (most recent call last)" not in res.stderr
    assert "Traceback (most recent call last)" not in res.stdout


@pytest.mark.parametrize(
    "cmd_args",
    [
        (["tca", "AAPL", "--json"]),
        (["strategy", "run", "--strategy", "whale_momentum", "--json"]),
        (["vessel", "list", "--json"]),
        (["report", "rts28", "--json"]),
    ],
)
def test_cli_companion_absence_json_output(cmd_args):
    """P-02: Companion-backed commands with --json output clean structured JSON error payload."""
    import json

    env = {**os.environ, "MDRAP_DISABLE_COMPANIONS": "1"}
    res = subprocess.run(
        [sys.executable, "cli.py"] + cmd_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert res.returncode == EXIT_COMPANION_MISSING
    data = json.loads(res.stdout)
    assert data.get("status") == "COMPANION_PACKAGE_REQUIRED"
