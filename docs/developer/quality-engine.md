# The Deterministic Quality Engine

The MDRAP Quality Engine (`mdrap.quality`) enforces deterministic market microstructure invariants across every incoming tick.

---

## 1. Strict Priority Invariant

Quality statuses follow a strict non-downgrade hierarchy:

$$\text{INVALID} > \text{SUSPICIOUS} > \text{VALID}$$

- Once an event is marked `INVALID`, no subsequent rule or reconciler can downgrade it to `SUSPICIOUS` or `VALID`.
- Once an event is marked `SUSPICIOUS`, it can only be escalated to `INVALID`, never downgraded to `VALID`.
- A real market anomaly (e.g., sudden extreme volatility) is marked `SUSPICIOUS`, not `INVALID`. Only corrupt, impossible, or structurally violated packets are classified as `INVALID`.

---

## 2. The 15 Deterministic Quality Rules

| # | Rule Name | Category | Trigger Condition | Status Assigned |
| :- | :--- | :--- | :--- | :--- |
| 1 | **NonPositivePrice** | Structural | Bid or ask price $\le 0.0$ | `INVALID` |
| 2 | **NonPositiveSize** | Structural | Bid or ask quantity $\le 0.0$ | `INVALID` |
| 3 | **InvertedSpread** | Microstructure | $\text{Bid Price} \ge \text{Ask Price}$ (Crossed/locked book) | `SUSPICIOUS` / `INVALID` |
| 4 | **ExcessiveSpread** | Microstructure | $(\text{Ask} - \text{Bid}) / \text{Mid} > \text{Threshold}$ ($>500\text{ bps}$) | `SUSPICIOUS` |
| 5 | **PriceJump** | Volatility | Absolute price move $> 10\%$ relative to moving median | `SUSPICIOUS` |
| 6 | **ClockSkew** | Network | $|\text{ReceiveTS} - \text{ExchangeTS}| > \text{MaxDrift}$ ($>50\text{ms}$) | `SUSPICIOUS` |
| 7 | **FutureTimestamp** | Network | $\text{ExchangeTS} > \text{ReceiveTS} + 1.0\text{s}$ | `INVALID` |
| 8 | **StaleTick** | Temporal | Delta since last tick $> \text{StaleThreshold}$ ($>3.0\text{s}$) | `SUSPICIOUS` |
| 9 | **SequenceGap** | Protocol | Exchange packet sequence jumped by $\ge 2$ | `SUSPICIOUS` |
| 10 | **SequenceRegression**| Protocol | Exchange packet sequence decreased ($S_t < S_{t-1}$) | `INVALID` |
| 11 | **DuplicateEvent** | Protocol | Identical sequence number and payload hash repeated | `INVALID` |
| 12 | **ZeroDepthLevels** | Order Book | L2 order book pushed with 0 bid or ask levels | `INVALID` |
| 13 | **AsymmetricDepth** | Order Book | Discrepancy between reported and actual level count | `SUSPICIOUS` |
| 14 | **SBEOutOfBounds** | Binary Encoding| SBE integer overflow or NaN/Infinity floating point | `INVALID` |
| 15 | **CorruptWireCRC** | Transport | Checksum or framing delimiter corrupted | `INVALID` |

---

## 3. Bitmask Representation

For ultra-high throughput in native C hot paths, each rule corresponds to a bit position in a 32-bit integer bitmask:

```c
#define RULE_NON_POSITIVE_PRICE   (1 << 0)
#define RULE_NON_POSITIVE_SIZE    (1 << 1)
#define RULE_INVERTED_SPREAD      (1 << 2)
#define RULE_EXCESSIVE_SPREAD     (1 << 3)
#define RULE_PRICE_JUMP           (1 << 4)
#define RULE_CLOCK_SKEW           (1 << 5)
#define RULE_FUTURE_TIMESTAMP     (1 << 6)
#define RULE_STALE_TICK           (1 << 7)
#define RULE_SEQUENCE_GAP         (1 << 8)
#define RULE_SEQUENCE_REGRESSION  (1 << 9)
#define RULE_DUPLICATE_EVENT      (1 << 10)
#define RULE_ZERO_DEPTH_LEVELS    (1 << 11)
#define RULE_ASYMMETRIC_DEPTH     (1 << 12)
#define RULE_SBE_OUT_OF_BOUNDS    (1 << 13)
#define RULE_CORRUPT_WIRE_CRC     (1 << 14)
```

The C kernel evaluates all 15 rules in sub-microsecond time using branch-free bitwise operations.

---

## 4. Pure-Python Fallback Parity

When native compilation is unavailable (`MDRAP_DISABLE_FASTPATH=1`), `mdrap.quality.QualityEngine` runs a pure-Python implementation. The test suite (`tests/test_fastpath_parity.py`) verifies that for 100,000 synthetic test ticks, the pure-Python output matches the native C kernel output bit-for-bit with 0 divergences.

---

## 5. Anomaly Quarantine

When an event triggers an `INVALID` or `SUSPICIOUS` outcome:
1. The event is isolated from the canonical downstream shared-memory ring buffer.
2. Downstream trading algorithms receive an out-of-band circuit-breaker notification.
3. The event is forwarded to the `IncidentManager` to begin recording an incident corridor.
