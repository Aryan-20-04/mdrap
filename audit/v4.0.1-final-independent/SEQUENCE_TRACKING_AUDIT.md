# MDRAP v4.0.1 — Sequence Tracking and Session-Reset Audit

**Audit Date:** 2026-11-04  
**Target Subsystems:** `src/mdrap/fastpath.c`, `src/mdrap/quality.py`  
**Test Scripts:** `audit/v4.0.1-final-independent/reproduction_scripts/test_p0_parity_stale_capacity.py`  
**Raw Test Evidence:** `audit/v4.0.1-final-independent/raw_evidence/parity_stale_capacity.log`

---

## 1. Sequence Engine Architecture

MDRAP tracks feed sequence numbers per `(source, instrument)` channel using a dual-window model:
1. **Immediate In-Window Reordering Window (64 positions):**
   - Maintained via a 64-bit integer bitmask (`uint64_t seen`).
   - Packets arriving at sequence $S < S_{last}$ with $\Delta = S_{last} - S < 64$ are tested against bit $\Delta$.
   - If bit $\Delta$ is 0, the packet is an out-of-order, previously unseen transmission: marked `SUSPICIOUS (OUT_OF_ORDER)` and bit $\Delta$ is set to 1.
   - If bit $\Delta$ is 1, the packet is an exact duplicate: marked `INVALID (DUPLICATE)`.
2. **Out-of-Window Stale Circular Buffer:**
   - For packets with $\Delta \ge 64$, packets fall outside the 64-bit word mask.
   - A circular history ring (`stale_seen`) records recent out-of-window sequence identifiers to detect duplicate retransmissions of stale packets.
3. **Session Reset Threshold ($> 1,000$ Backward Jump):**
   - When a sequence regresses by more than `seq_jump_limit` (default 1,000), a session transition counter (`regress_count`) increments.
   - If two consecutive large backward sequences arrive, the channel confirms an exchange daily session reboot or reconnection, re-anchoring `last_seq` without marking the entire stream permanently dead.

---

## 2. Independent Audit & Discovered Discrepancy

### Finding SQ-01: Divergent Buffer Capacity in Native C vs Pure Python & History Depth Expansion
- **Severity:** P0 (Core Semantic Parity Violation)
- **Confidence:** Confirmed, Formally Re-engineered, and Verified
- **Initial Observation:**
  In prior versions, Native C allocated `stale_seen[4]` (4 entries) in `FastSlot` to fit inside two 64-byte cache lines (128 bytes), while Python used a 64-entry list. This caused semantic divergence: a 5th stale sequence retransmission was missed by C but caught by Python.
- **High-Frequency Market Data Adequacy Review:**
  While temporarily reducing Python to 4 entries restored parity, an audit of real institutional exchange feeds (NASDAQ ITCH, MoldUDP64, CME MDP 3.0) revealed that 4 entries is inadequate during network microbursts or bulk packet retransmissions. A MoldUDP64 re-request packet burst can easily contain 10–25 out-of-window messages. A 4-slot ring would evict sequence numbers within microseconds, allowing duplicate retransmissions to be misclassified as new out-of-order packets.
- **Architectural Solution & Cache-Line Alignment:**
  Both Native C and Pure Python were upgraded to a robust **32-entry circular history buffer** with zero parity divergence and strict cache-line alignment:
  1. **Native C (`src/mdrap/fastpath.c`):**
     - Expanded `stale_seen` to `int64_t stale_seen[32];` (256 bytes).
     - Added 32 bytes of padding to align `FastSlot` to exactly 384 bytes:
       $$\text{sizeof(FastSlot)} = 64 \text{ bytes (ptrs/math)} + 32 \text{ bytes (metadata)} + 256 \text{ bytes (32-entry ring)} + 32 \text{ bytes (pad)} = 384 \text{ bytes} = 6 \times 64\text{-byte cachelines}$$
     - Replaced runtime modulo division (`% 4` or `% 32`) with a single-cycle bitwise mask:
       `sl->stale_seen[sl->stale_head & 31] = seq;`
     - Compile-time statically asserted:
       `_Static_assert(sizeof(FastSlot) == 384, "FastSlot layout 6 cachelines");`
  2. **Pure Python (`src/mdrap/quality.py`):**
     - Set `DEFAULT_STALE_SEEN_CAPACITY = 32`.
     - Eviction policy: `if len(sl.stale_seen) > DEFAULT_STALE_SEEN_CAPACITY: sl.stale_seen.pop(0)`.
  3. **Golden Reference Oracle (`tests/golden_oracle.py`):**
     - Aligned to 32 entries with early return on duplicate detection.
- **Verification:**
  - Automated adversarial tests in [`tests/final/test_sequence_stale_and_session.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/final/test_sequence_stale_and_session.py) (`test_stale_history_capacity_32_deep_burst`) proved that a burst of 30 distinct stale packets is completely retained, and subsequent retransmissions are 100% flagged as `INVALID (DUPLICATE)`.
  - Differential fuzz tests across 500-event adversarial streams (`tests/test_differential_fuzz.py`) pass with 100% agreement across GoldenOracle, QualityEngine, and FastQualityEngine.

---

## 3. Boundary & Integer Extremes Verification

Adversarial testing against sequence boundary conditions in [`tests/test_final_p0c_sequence_matrix.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_final_p0c_sequence_matrix.py) confirmed:
- **Sequence 0 and 1:** Accepted without underflow or sign inversion.
- **Uint64 Boundaries:** Sequences up to $2^{63}-1$ and wraparounds handled safely without integer truncation.
- **Negative Sequences:** Rejected at validation stage with `REASON_SCHEMA_VIOLATION`.
