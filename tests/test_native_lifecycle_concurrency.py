"""
Adversarial test suite verifying absence of Use-After-Free race condition in native fastpath lifecycle (VULN-01).
"""

import threading
import time
import pytest
from mdrap.fastpath import (
    _NATIVE_LIB,
    _CFastEvent,
    _CFastResult,
    HAS_FASTPATH,
    FE_MASK_VALID,
    FE_HAS_PRICE,
    FE_HAS_QTY,
)


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
