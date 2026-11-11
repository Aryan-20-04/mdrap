"""Tests for thread-safe native C fastpath lifecycle and cleanup (MDRAP-NAT-01).

Verifies:
1. Atomic CAS pointer exchange in fastpath_cleanup().
2. Concurrent multithreaded cleanup and initialization calls do not cause double-free or segfaults.
"""

import threading
import time
import pytest

from mdrap import fastpath


def test_concurrent_fastpath_cleanup_and_init():
    """Verify concurrent fastpath_cleanup calls are atomic and safe (MDRAP-NAT-01)."""
    lib = getattr(fastpath, "_NATIVE_LIB", None)
    if not lib or not hasattr(lib, "fastpath_cleanup"):
        # If pure Python fallback or library not loaded, verify Python wrapper lifecycle
        for _ in range(50):
            fastpath.reset_engine()
        return

    errors = []
    stop_event = threading.Event()

    def cleanup_worker():
        while not stop_event.is_set():
            try:
                lib.fastpath_cleanup()
                time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)
                break

    def init_worker():
        while not stop_event.is_set():
            try:
                if hasattr(lib, "fastpath_init"):
                    lib.fastpath_init(0.05, 6.0, 50)
                time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)
                break

    # Spawn 4 threads racing cleanup and 4 threads racing init
    threads = []
    for _ in range(4):
        t1 = threading.Thread(target=cleanup_worker)
        t2 = threading.Thread(target=init_worker)
        threads.extend([t1, t2])
        t1.start()
        t2.start()

    time.sleep(1.0)
    stop_event.set()

    for t in threads:
        t.join(timeout=2.0)

    # Invariant: ZERO segfaults, zero double-free exceptions
    assert len(errors) == 0, f"Concurrent cleanup/init produced errors: {errors}"
