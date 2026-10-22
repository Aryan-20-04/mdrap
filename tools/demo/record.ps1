# MDRAP Automated Demo Run & Evidence Capture Script (PowerShell)
# Executes the 8 demo scenes using exact commands with seed=42

$ErrorActionPreference = "Stop"

Write-Host "=== MDRAP Live Demo Recording Pipeline (PowerShell) ==="
$DemoDb = "data/demo_recording.db"
if (Test-Path $DemoDb) { Remove-Item $DemoDb -Force }

$env:MDRAP_DEMO = "1"

Write-Host "`n--- [1/8] Scene 1: Hook (Crossed Quote Detection) ---"
python cli.py bbo BTC/USD

Write-Host "`n--- [2/8] Scene 2: Platform Diagnostics (Doctor) ---"
python cli.py doctor

Write-Host "`n--- [3/8] Scene 3: Live Pipeline Simulation ---"
python cli.py live BTC/USD -l 5 --sim

Write-Host "`n--- [4/8] Scene 4: Catch Bad Data & Quarantine Inspection ---"
python cli.py run -e 1000 -s 42 --db $DemoDb
python cli.py query quarantine 5 --db $DemoDb

Write-Host "`n--- [5/8] Scene 5: Consolidated Level-2 Depth Ladder ---"
python cli.py depth BTC/USD

Write-Host "`n--- [6/8] Scene 6: Speed Benchmark (Native C Hot-Path) ---"
python cli.py core -e 1000000

Write-Host "`n--- [7/8] Scene 7: Cryptographic Proof & Tamper Verification ---"
python cli.py audit -l 5 --db $DemoDb
python cli.py audit --verify --db $DemoDb

# Simulate attacker row modification
python tools/demo/tamper.py $DemoDb
python cli.py audit --verify --db $DemoDb

Write-Host "`n--- [8/8] Scene 8: Completed Demo Verification ---"
Write-Host "All 8 demo scenes executed successfully against seed=42."
