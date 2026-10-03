# MDRAP v2.4.2 Audit Baseline Environment Record

**Date**: 2026-10-03  
**Commit**: `c902900cf652b6ee15099950b7e93a226160c6a8`  
**Tag**: `v2.4.2-audit-baseline`  

## Hardware Configuration
- **Host CPU**: AMD Ryzen 5 7600X 6-Core / 12-Thread Processor @ 4.70 GHz base (boost up to 5.3 GHz)
- **Host RAM**: 32 GB DDR5 RAM
- **Storage Device**: NVMe PCIe 4.0 SSD (Samsung 980 PRO)
- **Architecture**: x86-64 (AVX2, BMI2, FMA supported)

## Software Stack
- **Operating System**: Microsoft Windows 11 Pro 64-bit (Build 10.0.26200)
- **Python**: Python 3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 19:06:28) [MSC v.1942 64 bit (AMD64)]
- **C Compiler**: GCC 14.2.0 (Rev2, Built by MSYS2 project)
- **SQLite**: SQLite 3.45.3 (bundled in Python 3.13)
- **Key Dependencies**:
  - `rich`: 13.9.4
  - `pytest`: 8.3.4
  - `fastapi`: 0.115.6 (optional extra)
  - `uvicorn`: 0.34.0 (optional extra)
  - `websockets`: 14.1 (optional extra)

## Environment Variables
- `MDRAP_DISABLE_FASTPATH`: `0` (Native C acceleration enabled by default)
- `MDRAP_API_KEY_SALT`: `"mdrap_kdf_v1"` (default)
- `PYTHONHASHSEED`: Unset (randomized by default in Python 3)
