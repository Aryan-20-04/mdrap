# Installation Guide

MDRAP provides dual execution tiers:
1. **Pure Python Fallback Engine**: 100% standard library (plus `rich` for CLI formatting). Works identically across Linux, Windows, macOS, and BSD with zero compilation requirements.
2. **Native C Acceleration Kernel** (Optional): Provides microsecond-latency SBE decoding, seqlock shared memory ring buffers, and SIMD-accelerated quality evaluation.

---

## 1. System Requirements

- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, RHEL 8+), Windows 10/11, macOS 12+
- **Python Version**: Python 3.10, 3.11, 3.12, or 3.13
- **Disk Space**: 500 MB minimum for binary & libraries; 5+ GB recommended for WAL storage
- **RAM**: 512 MB minimum (2 GB recommended for production workloads)

---

## 2. Option A: Pre-Built Wheel Installation

MDRAP publishes pre-built universal wheels containing Python byte-code and bundled Windows native DLLs:

```bash
pip install mdrap_core-6.0.0-py3-none-any.whl
```

Verify installation:
```bash
python -c "import mdrap; print(f'MDRAP Version: {mdrap.__version__}')"
```

---

## 3. Option B: Source Installation with Optional C Compilation

To install from source with native C acceleration enabled on Linux or Windows:

### Linux / POSIX Prerequisites
```bash
sudo apt-get update && sudo apt-get install -y gcc libc6-dev python3-dev
```

### Build and Install
```bash
git clone https://github.com/mdrap-assurance/mdrap.git
cd mdrap
python -m pip install --upgrade pip build
python -m build --wheel
python -m pip install dist/*.whl
```

If a C compiler is present, the setup script compiles `src/fastpath.c` into a native shared library (`_fastpath_native.so` on Linux, `_fastpath_native.dll` on Windows). If no compiler is available, MDRAP automatically defaults to the pure-Python fallback kernel.

---

## 4. Option C: Containerized Deployment (Docker)

To deploy MDRAP without modifying the host system:

```bash
# Build container image
docker build -t mdrap:6.0.0 -f deploy/Dockerfile .

# Run container with mounted data directory
docker run -d \
  --name mdrap-sidecar \
  --restart unless-stopped \
  -v $(pwd)/data:/data \
  -p 127.0.0.1:8000:8000 \
  mdrap:6.0.0
```

---

## 5. Acceleration Tier Verification

MDRAP guarantees 100% semantic parity between its native C hot path and pure-Python fallback. To verify which engine is active:

```bash
python -c "from mdrap.quality import HAS_FASTPATH; print('Native C Active' if HAS_FASTPATH else 'Pure Python Fallback Active')"
```

To force pure-Python fallback execution for testing:
```bash
# Linux / macOS
export MDRAP_DISABLE_FASTPATH=1

# Windows PowerShell
$env:MDRAP_DISABLE_FASTPATH="1"
```

---

## 6. Running Post-Installation Smoke Tests

Verify that all core components operate correctly:

```bash
python -m mdrap.cli check
```
