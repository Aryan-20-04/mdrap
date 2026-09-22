# MDRAP Quickstart Guide

Get up and running with the **Market Data Reliability & Acceleration Platform (MDRAP)** in less than 5 minutes.

---

## 1. Prerequisites

- **Option A (Container)**: [Docker](https://docs.docker.com/get-docker/) & [Docker Compose](https://docs.docker.com/compose/)
- **Option B (Bare Metal / Local Python)**: Python 3.10+ (Python 3.11–3.13 supported) and a C compiler (GCC, Clang, or MSVC) for native hot-path compilation.

---

## 2. Option A: Docker Quickstart (Recommended)

### Step 1: Clone and Configure Environment

```bash
git clone https://github.com/Aryan-20-04/mdrap.git
cd mdrap

# Create environment file from template
cp .env.example .env
```

Edit `.env` if you have direct market data vendor API keys (e.g. Polygon, Databento). If testing locally or with synthetic feeds, no API keys are required.

### Step 2: Start the MDRAP Container

```bash
docker compose up -d --build
```

The container boots:
- **FastAPI REST & WebSocket Service**: Listening on port `8000`
- **Interactive Documentation (Swagger UI)**: Available at `http://localhost:8000/docs`
- **TCP Low-Latency Stream Gateway**: Listening on port `9001`
- **Persistent SQLite Database**: Mounted to Docker volume `mdrap-data` at `/data/mdrap.db`

### Step 3: Check Health

```bash
curl http://localhost:8000/v1/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "2.2.0",
  "uptime_seconds": 4.21,
  "engine": "running",
  "active_feeds_count": 1,
  "db": {
    "path": "/data/mdrap.db",
    "wal_mode": true,
    "conflicts": 0
  },
  "shm": {
    "enabled": true,
    "name": "mdrap_feed"
  },
  "watchdog": {
    "healthy_sources": 0,
    "total_monitored": 0,
    "sources": {}
  }
}
```

---

## 3. Option B: Local Python Installation

### Step 1: Create Virtual Environment and Install

```bash
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
.venv\Scripts\activate

# Install MDRAP with all optional API and performance dependencies
pip install -e ".[all]"

# Optional: compile native C hot-path accelerator
python build_fastpath.py
```

### Step 2: Generate an Admin Key

```bash
python cli.py keys create --client-id Institutional_Admin --role ADMIN
```

Output:
```text
┌─────────────────── Client Authentication Key Created ────────────────────┐
│ API Key Generated Successfully!                                          │
│                                                                          │
│ Client ID: Institutional_Admin                                           │
│ Role: ADMIN                                                              │
│ API Token: mdrap_live_AgphJLX14LrgugU51tFGRQk8RpN-jOfv                   │
│ Key Prefix: mdrap_live_A...                                              │
│ Rate Limit: 20,000 eps                                                   │
│                                                                          │
│ WARNING: Copy and store this secret key securely now.                    │
│ It is hashed with SHA-256 in the database and cannot be displayed again. │
└──────────────────────────────────────────────────────────────────────────┘
```

### Step 3: Launch the Production Server

```bash
python cli.py serve --host 0.0.0.0 --port 8000
```

---

## 4. First Query via Python SDK

Create a script `example_query.py`:

```python
from client import MDRAPClient

# Connect to self-hosted instance
client = MDRAPClient(
    base_url="http://localhost:8000",
    api_key="mdrap_live_AgphJLX14LrgugU51tFGRQk8RpN-jOfv"
)

# 1. System Health
health = client.health()
print(f"Platform Engine Status: {health['status']} (v{health['version']})")

# 2. Cryptographic Audit Trail Verification
audit = client.verify_audit()
print(f"Audit Trail Integrity: {'VERIFIED' if audit['verified'] else 'FAILED'} ({audit['entries_checked']} records checked)")

# 3. Query Real-Time NBBO
bbo = client.get_bbo("BTC/USD")
print(f"BTC/USD NBBO: {bbo}")
```

Run the script:
```bash
python example_query.py
```

---

## 5. Next Steps

- [Production Deployment Guide](deployment.md) — Volumes, sizing, TLS reverse proxy.
- [Security & RBAC Architecture](security.md) — Cryptographic posture, tokens, hashing.
- [REST & WebSocket API Reference](api.md) — Endpoint specifications and schemas.
- [Data Licensing Compliance](data-licensing.md) — Connecting licensed market data feeds.
