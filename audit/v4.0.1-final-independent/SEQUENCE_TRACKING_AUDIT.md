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

### Finding SQ-01: Divergent Buffer Capacity in Native C vs Pure Python
- **Severity:** P0 (Core Semantic Parity Violation)
- **Confidence:** Confirmed and Reproduced
- **Evidence:** [`raw_evidence/parity_stale_capacity.log`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v4.0.1-final-independent/raw_evidence/parity_stale_capacity.log)
  ```text
  [*] Testing C vs Python stale seen behavior...
  [+] Python evaluation for 5th stale sequence resend: is_dup=True, status=QualityStatus.INVALID, reasons=['DUPLICATE', 'OUT_OF_ORDER']
  [+] Native C evaluation for 5th stale sequence resend: is_dup=False, status=QualityStatus.SUSPICIOUS, reasons=['OUT_OF_ORDER']
  [BUG CONFIRMED] Semantic Parity Divergence: Python detected duplicate=True, Native C detected duplicate=False!
  ```
- **Root Cause Analysis:**
  In [`src/mdrap/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/fastpath.c#L205):
  ```c
  typedef struct {
      ...
      uint32_t regress_count;
      uint32_t stale_head;
      int64_t  stale_seen[4];  /* Bounded circular buffer: CAPACITY 4 */
  } FastSlot;
  _Static_assert(sizeof(FastSlot) == 128, "FastSlot layout");
  ```
  `FastSlot` was constrained to 128 bytes (two 64-byte CPU cache lines), which limited `stale_seen` to 4 entries (`4 * 8 = 32 bytes`).
  In [`src/mdrap/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/mdrap/quality.py#L533):
  ```python
  sl.stale_seen.append(seq)
  if len(sl.stale_seen) > 64:  # CAPACITY 64
      sl.stale_seen.pop(0)
  ```
  Python used a list capped at **64** entries. Consequently, under an adversarial stream of 5 distinct stale packets, Native C evicted slot 0 on the 5th packet, failing to flag its subsequent retransmission as a duplicate, while Python flagged it as `INVALID (DUPLICATE)`.

- **Remediation Implemented:**
  Aligned `src/mdrap/quality.py` to match the exact 4-slot ring buffer capacity of native C:
  ```python
  sl.stale_seen.append(seq)
  if len(sl.stale_seen) > 4:
      sl.stale_seen.pop(0)
  ```
- **Verification:** Re-ran `test_p0_parity_stale_capacity.py` and property tests. Both C and Python now produce identical classifications across all test sequences.

---

## 3. Boundary & Integer Extremes Verification

Adversarial testing against sequence boundary conditions in [`tests/test_final_p0c_sequence_matrix.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_final_p0c_sequence_matrix.py) confirmed:
- **Sequence 0 and 1:** Accepted without underflow or sign inversion.
- **Uint64 Boundaries:** Sequences up to $2^{63}-1$ and wraparounds handled safely without integer truncation.
- **Negative Sequences:** Rejected at validation stage with `REASON_SCHEMA_VIOLATION`.
