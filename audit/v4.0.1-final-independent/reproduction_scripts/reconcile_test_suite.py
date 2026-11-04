"""Test Suite Reconciliation script to produce TEST_SUITE_RECONCILIATION.csv."""

import csv
import subprocess
import sys
from pathlib import Path

# Run pytest to collect all tests
cmd = [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"]
out = subprocess.check_output(cmd, text=True, errors="replace")

lines = [l.strip() for l in out.splitlines() if "::" in l]

categories = {
    "Unit tests": [],
    "Integration tests": [],
    "Recovery tests": [],
    "Projection tests": [],
    "Native parity tests": [],
    "Chaos tests": [],
    "Security tests": [],
    "E2E tests": [],
}

gate_mapping = []

for node in lines:
    node_lower = node.lower()
    
    # Categorize
    if "final/test_projection" in node_lower or "final_p0a" in node_lower or "final_p0b" in node_lower or "projection" in node_lower:
        cat = "Projection tests"
    elif "final/test_multiple_snapshot" in node_lower or "final_p0d" in node_lower or "snapshot" in node_lower or "recovery" in node_lower:
        cat = "Recovery tests"
    elif "final/test_sequence" in node_lower or "final_p0c" in node_lower or "fastpath" in node_lower or "parity" in node_lower:
        cat = "Native parity tests"
    elif "chaos" in node_lower or "fuzz" in node_lower:
        cat = "Chaos tests"
    elif "security" in node_lower or "auth" in node_lower:
        cat = "Security tests"
    elif "e2e" in node_lower or "pipeline" in node_lower or "full_architecture" in node_lower:
        cat = "E2E tests"
    elif "integration" in node_lower or "next_pieces" in node_lower:
        cat = "Integration tests"
    else:
        cat = "Unit tests"
        
    categories[cat].append(node)
    
    # Determine which release gate executes it
    is_gate_6 = "next_pieces" in node_lower or "pipeline_integration" in node_lower
    is_gate_7 = "test_security.py" in node_lower or "test_security_hardening_review.py" in node_lower
    is_gate_8 = "test_fuzz.py" in node_lower or "test_chaos.py" in node_lower
    is_gate_9 = "test_full_architecture_pipeline.py" in node_lower
    
    is_deselected_g5 = ("integration" in node_lower or "next_pieces" in node_lower or 
                        "chaos" in node_lower or "fuzz" in node_lower or 
                        "full_architecture" in node_lower)
                        
    if is_gate_6:
        exec_gate = "Gate 6 (Integration & Guarantees)"
    elif is_gate_7:
        exec_gate = "Gate 7 (Security Hardening)"
    elif is_gate_8:
        exec_gate = "Gate 8 (Fuzzing & Chaos)"
    elif is_gate_9:
        exec_gate = "Gate 9 (Full Architecture Pipeline)"
    elif is_deselected_g5:
        exec_gate = "Deselected in G5 (Run in dedicated gate or targeted suite)"
    else:
        exec_gate = "Gate 5 (Fast Isolated Unit Tests)"
        
    gate_mapping.append({
        "node_id": node,
        "category": cat,
        "executing_gate": exec_gate,
        "deselected_in_gate_5": is_deselected_g5
    })

print(f"Total collected tests: {len(lines)}")
for cat, tests in categories.items():
    print(f"  {cat}: {len(tests)}")

# Write CSV
csv_path = Path("audit/v4.0.1-final-independent/TEST_SUITE_RECONCILIATION.csv")
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["node_id", "category", "executing_gate", "deselected_in_gate_5"])
    writer.writeheader()
    writer.writerows(gate_mapping)

print(f"[+] Wrote reconciliation CSV to {csv_path}")
