#!/usr/bin/env bash
# MDRAP Automated Demo Run & Evidence Capture Script
# Executes the 8 demo scenes using exact commands with seed=42

set -euo pipefail

echo "=== MDRAP Live Demo Recording Pipeline ==="
DEMO_DB="data/demo_recording.db"
rm -f "$DEMO_DB"

export MDRAP_DEMO=1

echo "--- [1/8] Scene 1: Hook (Crossed Quote Detection) ---"
python cli.py bbo BTC/USD

echo "--- [2/8] Scene 2: Platform Diagnostics (Doctor) ---"
python cli.py doctor

echo "--- [3/8] Scene 3: Live Pipeline Simulation ---"
python cli.py live BTC/USD -l 5 --sim

echo "--- [4/8] Scene 4: Catch Bad Data & Quarantine Inspection ---"
python cli.py run -e 1000 -s 42 --db "$DEMO_DB"
python cli.py query quarantine 5 --db "$DEMO_DB"

echo "--- [5/8] Scene 5: Consolidated Level-2 Depth Ladder ---"
python cli.py depth BTC/USD

echo "--- [6/8] Scene 6: Speed Benchmark (Native C Hot-Path) ---"
python cli.py core -e 1000000

echo "--- [7/8] Scene 7: Cryptographic Proof & Tamper Verification ---"
python cli.py audit -l 5 --db "$DEMO_DB"
python cli.py audit --verify --db "$DEMO_DB"

# Simulate attacker row modification
python tools/demo/tamper.py "$DEMO_DB"
python cli.py audit --verify --db "$DEMO_DB" || true

echo "--- [8/8] Scene 8: Completed Demo Verification ---"
echo "All 8 demo scenes executed successfully against seed=42."
