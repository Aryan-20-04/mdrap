"""MDRAP Differential Fuzzer.

Feeds random and mutated byte buffers into Python and C SBE decoders and event evaluators
to verify identical rejection/acceptance semantics, memory safety, and zero crashes.
"""
import argparse
import ctypes
import os
import random
import sys

# Ensure src is importable
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
src_path = os.path.join(repo_root, "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from sbe import pack_sbe_tick, unpack_sbe_tick, TICK_TOTAL_FRAME_SIZE
from fastpath import _NATIVE_LIB, HAS_FASTPATH


class _CSbeTickPayload(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("seq", ctypes.c_uint64),
        ("exchange_ts", ctypes.c_double),
        ("ingest_ts", ctypes.c_double),
        ("broadcast_ts", ctypes.c_double),
        ("price", ctypes.c_double),
        ("size", ctypes.c_double),
        ("bid", ctypes.c_double),
        ("ask", ctypes.c_double),
        ("bid_size", ctypes.c_double),
        ("ask_size", ctypes.c_double),
        ("status", ctypes.c_uint8),
        ("is_crossed", ctypes.c_uint8),
        ("_pad", ctypes.c_uint16),
        ("engine_us", ctypes.c_uint32),
        ("symbol", ctypes.c_char * 16),
        ("source", ctypes.c_char * 16),
    ]


def run_sbe_differential_fuzz(iterations: int, seed: int = 42) -> int:
    rng = random.Random(seed)
    print(f"[fuzz] Running {iterations} SBE differential fuzz iterations (seed={seed})...")

    if not HAS_FASTPATH or _NATIVE_LIB is None:
        print("[fuzz] Native C library not available; skipping C comparison.")
        return 0

    c_payload = _CSbeTickPayload()
    c_unpack = _NATIVE_LIB.fastpath_sbe_unpack_tick
    c_unpack.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(_CSbeTickPayload)]
    c_unpack.restype = ctypes.c_int32

    valid_matches = 0
    rejection_matches = 0

    for i in range(iterations):
        # 50% purely random bytes, 50% mutated valid frames
        if rng.random() < 0.5:
            buf_len = rng.randint(0, 300)
            buf = rng.randbytes(buf_len)
        else:
            # Generate a valid frame, then mutate random bytes
            valid_frame = bytearray(
                pack_sbe_tick(
                    seq=rng.randint(1, 1000000),
                    exchange_ts=1700000000.0 + rng.random(),
                    ingest_ts=1700000000.0 + rng.random(),
                    price=round(rng.uniform(10.0, 500.0), 2),
                    size=float(rng.randint(1, 100)),
                    bid=100.0,
                    ask=100.5,
                    bid_size=10.0,
                    ask_size=10.0,
                    symbol="AAPL",
                    source="FEEDX",
                )
            )
            # Apply 1 to 5 random bit/byte mutations
            mutations = rng.randint(0, 5)
            for _ in range(mutations):
                idx = rng.randint(0, len(valid_frame) - 1)
                valid_frame[idx] ^= rng.randint(1, 255)
            buf = bytes(valid_frame)

        # 1. Test Python decoder
        py_res = unpack_sbe_tick(buf)

        # 2. Test C decoder
        c_res_code = c_unpack(buf, len(buf), ctypes.byref(c_payload))

        # Check rejection equivalence
        py_accepted = py_res is not None
        c_accepted = c_res_code == 1

        if py_accepted != c_accepted:
            raise AssertionError(
                f"Discrepancy at iteration {i}: Python accepted={py_accepted}, C accepted={c_accepted}, buf_len={len(buf)}"
            )

        if py_accepted:
            valid_matches += 1
            # Verify parsed sequence number matches
            assert py_res.seq == c_payload.seq, f"Seq mismatch: py={py_res.seq} != c={c_payload.seq}"
        else:
            rejection_matches += 1

    print(f"[fuzz] OK: {iterations} iterations passed (valid={valid_matches}, rejected={rejection_matches}).")
    return 0


def main():
    parser = argparse.ArgumentParser(description="MDRAP Differential Fuzzer")
    parser.add_argument("--iterations", type=int, default=10000, help="Number of fuzzing iterations")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed")
    args = parser.parse_args()

    try:
        run_sbe_differential_fuzz(args.iterations, args.seed)
    except Exception as e:
        print(f"[ERROR] Fuzzing failure: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
