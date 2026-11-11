# MDRAP Phase 3 — Design Partner Pilot Setup & Onboarding Guide

**Document Version**: 1.0.0  
**Target Audience**: Customer Infrastructure Lead / Trading Systems Engineer  
**Author**: Lead Infrastructure & Deployment Engineer (Agent 5)  
**Status**: APPROVED  

---

## 1. Pilot Overview & Mutual Commitments

The MDRAP Design Partner Pilot is a **structured 30-day evaluation** designed to measure real-world feed quality improvements, detect silent exchange sequence gaps, and test deterministic incident replay on customer-selected market data streams.

### What MDRAP Delivers:
- Fully functional, pre-configured read-only software package.
- 1-on-1 technical onboarding session (approx. 45 minutes).
- Automated weekly reliability and incident summary reports.
- Zero-cost evaluation license during the 30-day period.

### What the Customer Provides:
- An isolated Linux or Windows test instance (virtual machine, container, or staging server).
- Read-only network access to public exchange WebSocket endpoints (or mirrored internal feed lines).
- Designee engineer for a 15-minute weekly operational check-in.
- Candid feedback on incident detection and workflow utility.

---

## 2. Fast-Track Pilot Installation (30 Minutes)

### Step 1: Download & Extract Pre-Packaged Release
```bash
# Clone or extract verified pilot release candidate
git clone https://github.com/organization/mdrap.git
cd mdrap
git checkout business/phase3-design-partner-acquisition
```

### Step 2: Automated Environment Bootstrap
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 3: Run Interactive Baseline Verification
```bash
# Verify system integrity with zero dependencies
python tools/run_customer_demo.py
```
This automated runner executes the 12-step verification protocol, connects to live exchange feeds, injects a controlled synthetic gap, creates an incident, restarts the application, verifies deterministic replay, and validates the RFC 6962 Merkle proof package.

---

## 3. Selecting Pilot Feeds & Symbols

We recommend monitoring **2 to 4 high-liquidity instruments across 2 exchanges** to establish immediate cross-venue comparison:
- **Exchange 1**: Coinbase Spot (e.g. `BTC-USD`, `ETH-USD`)
- **Exchange 2**: Kraken Spot (e.g. `BTC-USD`, `ETH-USD`)
- *(Optional)* Binance or OKX (if geographically permissible).

Edit `config/pilot_config.json`:
```json
{
  "venues": ["COINBASE", "KRAKEN"],
  "symbols": ["BTC-USD", "ETH-USD"],
  "storage": {
    "db_path": "data/pilot_run.db",
    "retention_days": 30
  },
  "quality": {
    "max_future_skew_s": 5.0,
    "staleness_threshold_s": 5.0
  }
}
```

---

## 4. Operational Commands During the Pilot

### Check Real-Time Health & Freshness
```bash
python cli.py health --db data/pilot_run.db
```

### List Recorded Incidents & Anomaly Spikes
```bash
python cli.py incident list --db data/pilot_run.db
```

### Replay a Specific Market Incident
```bash
python cli.py replay --incident <INCIDENT_ID> --corridor 30 --db data/pilot_run.db
```

### Export Merkle Proof Package for Independent Audit
```bash
python cli.py export-evidence --incident <INCIDENT_ID> --output ./incident_evidence.json
python tools/verify_evidence.py ./incident_evidence.json
```

---

## 5. End of Pilot Evaluation & Success Review

At Day 30, the joint engineering team reviews:
1. Total sequence gaps and clock skews caught by MDRAP that went unnoticed by primary feed handlers.
2. Number of hours saved during postmortems.
3. Quant developer feedback on data quality scoring.
4. Mutual decision on transitioning to an annual subscription.
