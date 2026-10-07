"""Pytest integration for Gate G2 Kill-9 crash/recovery harness."""

from tests.gate_g2_harness import run_gate_g2_harness


def test_gate_g2_kill9_recovery_smoke():
    """Run 25 randomized kill-9 crash/recovery runs during standard pytest runs."""
    res = run_gate_g2_harness(total_runs=25, max_workers=4)
    assert res["passed"] is True
    assert res["acknowledged_event_loss"] == 0
    assert res["conflict_errors"] == 0
