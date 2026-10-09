# MDRAP Phase 7 — Differential Testing Strategy & Cross-Implementation Verification

## 1. Executive Summary & Objective
Where multiple implementations of the same logic exist within MDRAP (specifically, the native C high-throughput kernel in [`src/fastpath.c`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/fastpath.c) and the pure Python reference engine in [`src/quality.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/src/quality.py)), subtle discrepancies between floating-point rounding, bitmask operations, or edge-case handling can create critical cross-platform divergence.

**Differential Testing** subjects both implementations to identical inputs and asserts bit-for-bit parity across all outputs.

---

## 2. Core Differential Test Pairs

### Pair 1: Native C Fastpath Kernel vs. Pure Python Quality Engine
- **Input Corpus**: 5,000 synthetic market quotes and trades containing valid data, crossed quotes, zero sizes, extreme spikes, and negative prices.
- **Oracle Comparison**:
  $$\text{Evaluate}_{\text{Python}}(event) \overset{?}{=} \text{Evaluate}_{\text{NativeC}}(event)$$
- **Assertion**:
  - `status`: Identical enum (`VALID`, `SUSPICIOUS`, or `INVALID`).
  - `reasons`: Identical bitmask integer and string representation.
  - `latency_difference`: Native C kernel achieves $> 5\text{x}$ throughput improvement without diverging on a single bit.

### Pair 2: Binary SBE Struct Serialization vs. Dataclass Canonical Model
- **Input Corpus**: Randomly generated canonical trade and quote records.
- **Oracle Comparison**:
  $$\text{Model} \xrightarrow{\text{to\_sbe\_bytes()}} \text{Bytes} \xrightarrow{\text{from\_sbe\_bytes()}} \text{NewModel}$$
  $$\text{Model} \equiv \text{NewModel}$$
- **Assertion**: Scaled fixed-point conversion ($10^8$ price, $10^4$ size) produces zero precision loss compared to IEEE-754 double precision within supported intervals.

### Pair 3: Range Partitioning vs. CRC32 Hash Partitioning
- **Input Corpus**: 1,000 ticker symbols across Russell 1000 and crypto symbols.
- **Evaluation**: Both partitioning algorithms guarantee determinism, non-overlapping assignments, and strict sequence isolation per partition.

---

## 3. Automated Execution in Phase 7
Implemented in [`tests/test_phase7_verification.py`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/tests/test_phase7_verification.py):
- `test_differential_python_versus_native_rules`: Evaluates 2,500 test events and verifies 100% agreement on validation status and reason bitmasks.
- `test_differential_sbe_wire_fidelity`: Asserts numeric fidelity between fixed-point wire serialization and canonical models.
