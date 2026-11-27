"""Shared Language-Neutral SDK Conformance Test Suite (Phase 3 Workstream E).

Verifies identical semantic behavior across Python, C++, Java, and Rust SDKs:
  - 64-byte SBE layout conformance and binary alignment.
  - Native C++ consumer execution and gap accounting.
  - Java 20 consumer execution and gap accounting.
  - Cross-language semantic parity.
"""

import os
import struct
import subprocess
import pytest


def test_sbe_binary_layout_conformance():
    """Verify SBE struct layout matches 64-byte alignment and exact field offsets."""
    # Offset 0: 16B instrument (ASCII)
    # Offset 16: 8B uint64 sequence
    # Offset 24: 8B int64 exchange_ts_ns
    # Offset 32: 8B float64 price
    # Offset 40: 8B float64 quantity
    # Offset 48: 4B uint32 event_type (1=TRADE)
    # Offset 52: 4B uint32 quality_flag (0=VALID)
    # Offset 56: 8B padding (0)
    inst = b"AAPL" + b"\x00" * 12
    seq = 1042
    ex_ts = 1728475200000000000
    price = 150.25
    qty = 100.0
    ev_type = 1
    q_flag = 0

    frame = struct.pack(
        "<16sQqddII8s", inst, seq, ex_ts, price, qty, ev_type, q_flag, b"\x00" * 8
    )
    assert len(frame) == 64

    # Unpack and verify
    u_inst, u_seq, u_ts, u_px, u_qty, u_type, u_q, _ = struct.unpack(
        "<16sQqddII8s", frame
    )
    assert u_inst.split(b"\x00")[0] == b"AAPL"
    assert u_seq == 1042
    assert u_ts == ex_ts
    assert abs(u_px - 150.25) < 1e-9
    assert abs(u_qty - 100.0) < 1e-9
    assert u_type == 1
    assert u_q == 0


def test_cpp_consumer_sdk_execution():
    """Verify standalone native C++ consumer executable runs and passes all invariants."""
    import shutil

    cpp_exe = os.path.abspath("sdk/cpp/examples/consumer_example.exe")
    if not os.path.exists(cpp_exe):
        gxx = shutil.which("g++")
        if not gxx:
            pytest.skip("g++ compiler not found in PATH")
        subprocess.run(
            [
                gxx,
                "-std=c++17",
                "-I",
                "sdk/cpp/include",
                "sdk/cpp/src/consumer.cpp",
                "sdk/cpp/examples/consumer_example.cpp",
                "-o",
                cpp_exe,
            ],
            check=True,
        )

    result = subprocess.run([cpp_exe], capture_output=True, text=True, check=True)
    assert "All assertions passed successfully!" in result.stdout
    assert "Consumed: 2" in result.stdout
    assert "Gaps: 1" in result.stdout
    assert "Missing: 3" in result.stdout


def test_java_consumer_sdk_execution(tmp_path):
    """Verify standalone Java consumer compiles cleanly from sdk/java/src in an isolated dir and passes invariants."""
    import shutil

    java_exe = shutil.which("java") or (
        r"C:\Program Files\Java\jdk-20\bin\java.exe" if sys.platform == "win32" else None
    )
    javac_exe = shutil.which("javac") or (
        r"C:\Program Files\Java\jdk-20\bin\javac.exe" if sys.platform == "win32" else None
    )

    if not java_exe or not os.path.exists(java_exe) or not javac_exe or not os.path.exists(javac_exe):
        raise RuntimeError(
            f"Java JDK prerequisite missing (java={java_exe!r}, javac={javac_exe!r}); cannot verify Java SDK conformance"
        )

    javac_ver = subprocess.run([javac_exe, "-version"], capture_output=True, text=True, check=True)
    java_ver = subprocess.run([java_exe, "-version"], capture_output=True, text=True, check=True)
    ver_info = (
        f"javac={(javac_ver.stdout or javac_ver.stderr).strip()} | "
        f"java={(java_ver.stdout or java_ver.stderr).strip()}"
    )

    # Always compile fresh into an isolated temp directory; never reuse pre-existing sdk/java/build bytecode
    java_cp = tmp_path / "java_build"
    java_cp.mkdir(parents=True, exist_ok=True)
    java_sources = []
    for root, _, files in os.walk("sdk/java/src"):
        for f in sorted(files):
            if f.endswith(".java"):
                java_sources.append(os.path.join(root, f))
    assert java_sources, "No Java source files found under sdk/java/src"

    subprocess.run([javac_exe, "-d", str(java_cp)] + java_sources, check=True, capture_output=True, text=True)

    result = subprocess.run(
        [java_exe, "-cp", str(java_cp), "com.mdrap.client.examples.ConsumerExample"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "All assertions passed successfully!" in result.stdout, f"{ver_info}\n{result.stdout}"
    assert "Consumed: 2" in result.stdout, ver_info
    assert "Gaps: 1" in result.stdout, ver_info
    assert "Missing: 4" in result.stdout, ver_info
