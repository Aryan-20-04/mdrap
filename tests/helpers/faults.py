"""MDRAP platform-independent fault injection helpers for universal testing."""

from __future__ import annotations

import contextlib
import os
import sys
from unittest import mock


@contextlib.contextmanager
def fail_commit_fsync(exc: Exception | None = None):
    """Context manager that injects an I/O fault specifically at the commit fsync of append_batch.

    Targeting is invariant across POSIX and Windows because directory sync
    and rotation syncs are bypassed; only when the immediate caller is 'append_batch'
    does this hook trigger the fault.
    """
    if exc is None:
        exc = OSError(5, "EIO injected at batch commit boundary")

    real_fsync = os.fsync

    def hook(fd):
        frame = sys._getframe(1)
        while frame and frame.f_code.co_name in (
            "hook",
            "_execute_mock_call",
            "_mock_call",
            "__call__",
            "fail_commit_fsync",
        ):
            frame = frame.f_back
        if frame and frame.f_code.co_name == "append_batch":
            raise exc
        return real_fsync(fd)

    with mock.patch("os.fsync", side_effect=hook):
        yield
