# Independent Adversarial Reviewer — Exact Evidence

## 1. Soak Test Duration
- File: `docs/audit/v5/24H_SOAK_TEST_REPORT.md:16-19`
  ```markdown
  Audit Status: SUSTAINED MULTI-VENUE SOAK EXECUTED (24-HOUR CONTINUOUS RUN INCOMPLETE DUE TO AGENTIC SESSION TIMEOUT BOUNDARIES).
  An empirical sustained live soak run of 32.71 seconds was executed...
  A total of 1,032 genuine market events were ingested.
  ```

## 2. Fabricated Percentiles in Stage Breakdown
- File: `benchmarks/stage_breakdown.json:49-56`
  ```json
  "native_c_kernel": {
    "total_events": 1000000,
    "p50_ns": 22.896999944350682,
    "p95_ns": 22.896999944350682,
    "p99_ns": 22.896999944350682,
    "max_ns": 22.896999944350682
  }
  ```

## 3. Committed Binaries
- Files: `src/mdrap/_fastpath_native.dll` and `src/mdrap/mdrap-core.exe` tracked in Git.

## 4. Hardcoded Demo Token
- File: `src/mdrap/workload_simulator.py:49`
  ```python
  auth_token: str = "mdrap_demo_pro_key"
  ```\n