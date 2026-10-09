"""
Phase 2 Supervision & Failure Recovery Tests (Workstream C).

Verifies:
1. Normal worker lifecycle under RuntimeSupervisor.
2. Transient crashes trigger bounded restart with backoff.
3. FatalWorkerError bypasses restart attempts and immediately escalates.
4. Repeated crashes exceeding restart budget escalate to fatal.
5. Clean cooperative shutdown stops all worker threads without leaving zombies.
"""

import threading
import time
import pytest

from mdrap.supervisor import (
    FatalWorkerError,
    RuntimeSupervisor,
    WorkerSpec,
    WorkerState,
)


def test_supervisor_normal_worker_lifecycle():
    """Verify clean start, status tracking, and graceful stop for healthy worker."""
    run_counter = 0

    def healthy_worker(stop_event: threading.Event):
        nonlocal run_counter
        while not stop_event.is_set():
            run_counter += 1
            time.sleep(0.01)

    supervisor = RuntimeSupervisor()
    supervisor.register_worker(
        WorkerSpec(name="healthy", target=healthy_worker, is_critical=True)
    )

    supervisor.start()
    time.sleep(0.05)

    status = supervisor.get_status()
    assert status["running"] is True
    assert status["healthy"] is True
    assert status["workers"]["healthy"]["state"] == WorkerState.RUNNING.value
    assert status["workers"]["healthy"]["alive"] is True
    assert run_counter > 0

    supervisor.stop(timeout_s=1.0)
    time.sleep(0.05)

    status_stopped = supervisor.get_status()
    assert status_stopped["running"] is False
    assert status_stopped["workers"]["healthy"]["alive"] is False


def test_supervisor_transient_crash_restart():
    """Verify that a worker that crashes transiently is restarted by supervisor."""
    crashes = 0
    started_count = 0

    def flaky_worker(stop_event: threading.Event):
        nonlocal crashes, started_count
        started_count += 1
        if crashes < 2:
            crashes += 1
            raise RuntimeError(f"Transient crash #{crashes}")
        # After 2 crashes, stay alive
        while not stop_event.is_set():
            time.sleep(0.01)

    supervisor = RuntimeSupervisor()
    supervisor.register_worker(
        WorkerSpec(
            name="flaky",
            target=flaky_worker,
            is_critical=True,
            max_retries=5,
            retry_window_s=5.0,
            restart_delay_base_s=0.05,
        )
    )

    supervisor.start()
    # Wait for supervisor to restart the flaky worker twice
    time.sleep(0.4)

    status = supervisor.get_status()
    assert supervisor.is_healthy() is True
    assert status["workers"]["flaky"]["total_restarts"] == 2
    assert status["workers"]["flaky"]["state"] == WorkerState.RUNNING.value

    supervisor.stop(timeout_s=1.0)


def test_supervisor_fatal_worker_error_escalation():
    """Verify FatalWorkerError immediately escalates without retrying."""
    fatal_calls = []

    def fatal_callback(worker_name: str, reason: str):
        fatal_calls.append((worker_name, reason))

    def fatal_worker(stop_event: threading.Event):
        raise FatalWorkerError("Corrupt database file detected")

    supervisor = RuntimeSupervisor(on_fatal_failure=fatal_callback)
    supervisor.register_worker(
        WorkerSpec(
            name="fatal_service",
            target=fatal_worker,
            is_critical=True,
            max_retries=5,
        )
    )

    supervisor.start()
    time.sleep(0.1)

    assert supervisor.is_healthy() is False
    status = supervisor.get_status()
    assert status["workers"]["fatal_service"]["state"] == WorkerState.FAILED_FATAL.value
    assert status["workers"]["fatal_service"]["total_restarts"] == 0
    assert len(fatal_calls) == 1
    assert fatal_calls[0][0] == "fatal_service"
    assert "Corrupt database" in fatal_calls[0][1]

    supervisor.stop(timeout_s=1.0)


def test_supervisor_exhausted_retries_escalation():
    """Verify worker exceeding max retries in window is marked FAILED_FATAL."""
    fatal_calls = []

    def fatal_callback(worker_name: str, reason: str):
        fatal_calls.append((worker_name, reason))

    def crashing_worker(stop_event: threading.Event):
        raise ValueError("Continuous crash")

    supervisor = RuntimeSupervisor(on_fatal_failure=fatal_callback)
    supervisor.register_worker(
        WorkerSpec(
            name="crash_loop",
            target=crashing_worker,
            is_critical=True,
            max_retries=2,
            retry_window_s=5.0,
            restart_delay_base_s=0.02,
        )
    )

    supervisor.start()
    # Wait for crashes + backoff retries to exhaust budget (max 2)
    time.sleep(0.3)

    assert supervisor.is_healthy() is False
    status = supervisor.get_status()
    assert status["workers"]["crash_loop"]["state"] == WorkerState.FAILED_FATAL.value
    assert len(fatal_calls) == 1

    supervisor.stop(timeout_s=1.0)
