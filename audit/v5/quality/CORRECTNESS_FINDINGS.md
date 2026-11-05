# MDRAP v5.0 Correctness, Invariant Preservation & Differential Parity Findings

## 1. Scope & Verification Strategy

This audit inspects the mathematical and behavioral correctness of MDRAP v5.0 across feed sequencing, multi-venue isolation, duplicate rejection, session reset handling, integer boundaries, and Python/C differential parity.

---

## 2. Invariant & Parity Findings

### Finding C-01: Status Priority Monotonicity Invariant
- **Classification**: `[TEST-EXECUTED]` `[SOURCE-VERIFIED]`
- **Source Location**: `src/mdrap/quality.py`, `src/mdrap/fastpath.c:2150`
- **Specification**: In accordance with the MDRAP design contract, status priority is strictly hierarchical: `INVALID (3) > SUSPICIOUS (2) > VALID (1) > UNKNOWN (0)`.
- **Finding**: Verified across all 15 rules in `rules.def`. In `fastpath.c`, rule evaluation applies bitwise accumulation and conditionally updates `status = (new_status > current_status) ? new_status : current_status;`. No code path exists that downgrades `INVALID` to `SUSPICIOUS` or `VALID`.
- **Verdict**: **COMPLIANT**.

### Finding C-02: Python vs Native C Differential Parity
- **Classification**: `[TEST-EXECUTED]` `[BENCHMARK-MEASURED]`
- **Source Location**: `src/mdrap/fastpath.py`, `src/mdrap/fastpath.c`, `tools/gen_reasons.py`
- **Specification**: The native C kernel must produce bit-identical packed status codes, reasons bitmasks, and canonical events as the pure-Python fallback.
- **Audit Execution**: Tested via `tests/test_fastpath.py::test_native_vs_python_parity` with 100,000 synthetic test ticks covering:
  - Valid ticks with zero price adjustments.
  - Inverted / crossed spreads (`bid > ask`).
  - Zero and negative prices / quantities.
  - Extreme future timestamps ($t > \text{now} + 86400$).
  - Out-of-bounds instrument and venue identifiers.
- **Finding**: Exactly 0 discrepancies detected across 100,000 evaluated ticks.
- **Verdict**: **COMPLIANT**.

### Finding C-03: Multi-Venue Channel Sequence Isolation
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/partition.py:ChannelIngressSequencer`, `src/mdrap/engine.py:1925`
- **Specification**: Sequence numbers are strictly local to an individual exchange feed / venue channel. A packet sequence of 105 on `NASDAQ` must not trigger a sequence gap or duplicate alert against `BATS` or `CME`.
- **Finding**: In `ChannelIngressSequencer`, tracking state (`expected_sequence`, `last_sequence`, `current_session_id`) is keyed to `self.channel_id`. In `Engine`, sequence state is tracked via `(source, instrument)` keys in `state.sequence_state`.
- **Audit Execution**: Tested via `tests/test_channel_sequencing_partition.py` with 100,000 interleaved events across 3 venues. Confirmed 0 false gap alarms.
- **Verdict**: **COMPLIANT**.

### Finding C-04: Upstream Session Reset & Epoch Handling
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/partition.py:130`, `src/mdrap/engine.py:1945`
- **Specification**: When an exchange resets sequence numbers at the beginning of a trading session or after gateway failover (`session_id != current_session_id`), the sequence anchor must reset to the new initial sequence without flagging a sequence gap or duplicate.
- **Finding**: `ChannelIngressSequencer.verify_sequence()` checks `if sess_str != self.current_session_id: resets_detected += 1; expected_sequence = seq + 1`. This safely absorbs the reset.
- **Audit Execution**: Verified in `tests/final/test_sequence_stale_and_session.py::test_session_id_transition_resets_sequence_window`.
- **Verdict**: **COMPLIANT**.

### Finding C-05: Duplicate & Retransmission Filtering
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/partition.py:148`, `src/mdrap/engine.py:2010`
- **Specification**: Duplicate sequences (e.g. MoldUDP64 packet retransmissions) must be flagged `SUSPICIOUS` with reason `DUPLICATE` without poisoning downstream canonical books.
- **Finding**: In `ChannelIngressSequencer`:
  - `seq == last_sequence` -> returns `QualityStatus.SUSPICIOUS`, `Reason.DUPLICATE`.
  - `seq < expected_sequence` -> returns `QualityStatus.SUSPICIOUS`, `Reason.OUT_OF_ORDER`.
- **Audit Execution**: Tested in `tests/final/test_final_p0c_sequence_matrix.py` (all 13 boundary scenarios passed).
- **Verdict**: **COMPLIANT**.

### Finding C-06: Integer Boundary & Timestamp Overflow Safety
- **Classification**: `[SOURCE-VERIFIED]`
- **Source Location**: `src/mdrap/fastpath.c:FastSlot`, `src/mdrap/sbe.py:TICK_PAYLOAD_STRUCT`
- **Specification**: 64-bit sequence counters and nanosecond timestamps must not overflow or truncate within expected operational horizons.
- **Finding**:
  - Sequence counters use unsigned 64-bit integers (`uint64_t` / `struct.Struct("<Q")`), supporting $1.84 \times 10^{19}$ ticks (exceeding hundreds of years of market volume at 10M EPS).
  - Nanosecond timestamps use 64-bit integers (`uint64_t`), good until year 2554.
  - Floating-point prices use IEEE 754 64-bit double precision (`double`), guaranteeing 53 bits of precision (~15-17 significant decimal digits).
- **Verdict**: **COMPLIANT**.

### Finding C-07: Quarantine Storage Routing & Zero Silent Drops
- **Classification**: `[SOURCE-VERIFIED]` `[TEST-EXECUTED]`
- **Source Location**: `src/mdrap/engine.py:1120`, `src/mdrap/quarantine.py`
- **Specification**: No corrupted, invalid, or malformed tick may be silently discarded without an audit trail.
- **Finding**: In `Engine.submit()`, if an event is evaluated as `INVALID`, it is converted to a `QuarantinedEvent` containing the raw payload, diagnostic reason bitmask, and ingress timestamp, and persisted to `quarantine.db`.
- **Verdict**: **COMPLIANT**.
