import subprocess
import sys
from models import Reason
from fastpath import (
    HAS_FASTPATH,
    FastQualityEngine,
    _CFastEvent,
    _CFastResult,
    _NATIVE_LIB,
)
import ctypes


def test_gen_reasons_check_cli():
    cmd = [sys.executable, "tools/gen_reasons.py", "--check"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, (
        f"gen_reasons.py --check failed:\n{res.stdout}\n{res.stderr}"
    )


def test_native_c_never_sets_user_bits():
    """Assert that native C quality evaluation never sets bits >= 32."""
    if not HAS_FASTPATH or _NATIVE_LIB is None:
        return

    engine = FastQualityEngine()

    test_events = [
        _CFastEvent(
            0,
            0,
            0,
            0,
            1000.0,
            1000.001,
            1,
            150.0,
            10.0,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        ),
        _CFastEvent(
            0,
            0,
            1,
            0,
            1000.0,
            1000.001,
            2,
            float("nan"),
            float("nan"),
            151.0,
            150.0,
            10.0,
            10.0,
        ),
        _CFastEvent(
            0,
            0,
            0,
            0,
            1000.0,
            1000.001,
            3,
            -10.0,
            5.0,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        ),
        _CFastEvent(
            0,
            0,
            0,
            0,
            500.0,
            1000.0,
            4,
            150.0,
            10.0,
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        ),
    ]

    user_mask = 0xFFFFFFFF00000000

    for ev in test_events:
        res = _CFastResult()
        _NATIVE_LIB.fastpath_engine_evaluate(
            engine._engine_ptr, ctypes.byref(ev), ctypes.byref(res)
        )
        assert (res.reason_mask & user_mask) == 0, (
            f"Native C set user bits (>= 32): {hex(res.reason_mask)}"
        )
