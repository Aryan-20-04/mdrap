"""
Adversarial test suite verifying absence of Use-After-Free race condition in native fastpath lifecycle (VULN-01).
"""

import threading
import time
import pytest
import os
from mdrap.fastpath import (
    _NATIVE_LIB,
    _CFastEvent,
    _CFastResult,
    HAS_FASTPATH,
    FE_MASK_VALID,
    FE_HAS_PRICE,
    FE_HAS_QTY,
)
from mdrap.fastpath import FastQualityEngine, _C_EXT
from mdrap.models import CanonicalEvent, EventType


def _canonical_event(i=1):
    return CanonicalEvent(
        event_id=f"native-lifecycle-{i}",
        instrument_id="AAPL",
        event_type=EventType.TRADE,
        exchange_timestamp=1000.0 + i,
        receive_timestamp=1000.001 + i,
        source="FEEDX",
        sequence_number=i,
        price=100.0 + i / 100,
        quantity=10.0,
    )


def _require_native_engine():
    engine = FastQualityEngine()
    native_extension = bool(_C_EXT and engine._engine_ptr_c)
    if not engine.is_native or not native_extension:
        engine.close()
        if os.environ.get("MDRAP_REQUIRE_NATIVE") == "1":
            pytest.fail("MDRAP_REQUIRE_NATIVE=1 but the compiled C extension is unavailable")
        pytest.skip("Compiled C extension FastQualityEngine is unavailable")
    return engine


@pytest.mark.parametrize("operation", ["evaluate", "batch", "reset"])
def test_per_instance_close_waits_for_inflight_native_operations(operation, monkeypatch):
    engine = _require_native_engine()
    entered = threading.Event()
    release = threading.Event()
    errors = []
    original = {
        "evaluate": engine._evaluate_unlocked,
        "batch": engine._evaluate_batch_unlocked,
    }.get(operation)

    def delayed(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test failed to release in-flight operation")
        return original(*args, **kwargs)

    if original:
        monkeypatch.setattr(
            engine,
            "_evaluate_unlocked" if operation == "evaluate" else "_evaluate_batch_unlocked",
            delayed,
        )
    elif operation == "reset":
        class DelayedLock:
            def __enter__(self):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test failed to release reset")
            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(engine, "_eval_lock", DelayedLock())

    def run_operation():
        try:
            if operation == "evaluate":
                engine.evaluate(_canonical_event())
            elif operation == "batch":
                engine.evaluate_batch([_canonical_event(1), _canonical_event(2)])
            else:
                engine.reset()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_operation)
    worker.start()
    assert entered.wait(5)
    closer = threading.Thread(target=engine.close)
    closer.start()
    time.sleep(0.02)
    assert closer.is_alive(), "close returned while a native operation was in flight"
    with pytest.raises(RuntimeError, match="closed"):
        engine.evaluate_batch([])
    release.set()
    worker.join(5)
    closer.join(5)
    assert not worker.is_alive() and not closer.is_alive()
    assert not errors
    assert engine._engine_ptr is None and engine._engine_ptr_c is None
    engine.close()
    with pytest.raises(RuntimeError, match="closed"):
        engine.evaluate(_canonical_event(3))


def test_concurrent_evaluate_and_cleanup_race():
    """Verify that concurrent fastpath_cleanup does not cause Use-After-Free in fastpath_evaluate."""
    if not HAS_FASTPATH or not _NATIVE_LIB:
        pytest.skip("Native fastpath not available")

    lib = _NATIVE_LIB
    if not hasattr(lib, "fastpath_evaluate") or not hasattr(lib, "fastpath_cleanup"):
        pytest.skip("Lifecycle symbols not exported")

    stop_event = threading.Event()
    errors = []

    def evaluator_worker():
        ev = _CFastEvent()
        ev.source_id = 1
        ev.instrument_id = 1
        ev.event_type = 0
        ev.exchange_ts = time.time()
        ev.receive_ts = time.time()
        ev.price = 100.0
        ev.quantity = 10.0
        ev.present_mask = FE_MASK_VALID | FE_HAS_PRICE | FE_HAS_QTY
        res = _CFastResult()

        count = 0
        while not stop_event.is_set() and count < 10000:
            try:
                lib.fastpath_evaluate(threading.byref(ev) if hasattr(threading, "byref") else ev, res)
            except Exception as e:
                errors.append(e)
                break
            count += 1

    def lifecycle_worker():
        while not stop_event.is_set():
            try:
                lib.fastpath_cleanup()
                time.sleep(0.0001)
                lib.fastpath_init(0.05, 6.0, 50)
            except Exception as e:
                errors.append(e)
                break

    # Run 4 evaluator threads and 1 lifecycle thread
    eval_threads = [threading.Thread(target=evaluator_worker) for _ in range(4)]
    lifecycle_thread = threading.Thread(target=lifecycle_worker)

    for t in eval_threads:
        t.start()
    lifecycle_thread.start()

    time.sleep(0.5)
    stop_event.set()

    for t in eval_threads:
        t.join(timeout=2.0)
    lifecycle_thread.join(timeout=2.0)

    # Ensure clean state at end of test
    lib.fastpath_init(0.05, 6.0, 50)

    assert len(errors) == 0, f"Encountered concurrency errors during evaluate vs cleanup: {errors}"
