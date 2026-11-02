# MDRAP v4.0.1 Engine Sequence Specification

**Specification Version:** 4.0.1-SEQ-SPEC  
**Status:** Approved / Enforced  
**Component:** `mdrap.engine.Engine`, `mdrap.quality.QualityEngine`, `src/fastpath.c`  

---

## 1. Single Authoritative Sequence Tracking Model

Market data streams arrive out-of-order, duplicated, delayed, or gapped due to network routing, UDP packet loss, or line arbitration (A/B line MoldUDP64 / ITCH). This specification defines the unified sequence tracking model enforced identically across Python Engine, pure Python `QualityEngine`, and native C `FastQualityEngine`.

---

## 2. Sequence State Machine & 64-Bit Sliding Window

For each stream key $(\text{source}, \text{instrument})$, state maintains:
1. `last_seq` (`uint64`): Highest sequential sequence number processed.
2. `seen_mask` (`uint64`): 64-bit bitmask tracking arrivals within $[ \text{last\_seq} - 63, \text{last\_seq} ]$.
3. `sequence_cand` (`uint64`): Candidate counter for sequence jumps.
4. `sequence_regress` (`uint64`): Regression counter for backwards jumps $> 1000$.

### 2.1 State Transitions

#### Case 1: Sequential Forward Arrival ($\text{seq} = \text{last\_seq} + 1$)
- **Action:** Advance `last_seq = seq`.
- **Bitmask:** Shift window left by 1, insert 1:
  $$\text{seen\_mask} = ((\text{seen\_mask} \ll 1) \mid 1) \ \& \ (2^{64} - 1)$$
- **Verdict:** `QualityStatus.VALID`.

#### Case 2: Moderate Forward Gap ($1 < \text{seq} - \text{last\_seq} \le 1000$)
- **Action:** Record missing sequence numbers into `GapManager`.
- **Bitmask:** Shift window left by jump $J$:
  $$\text{seen\_mask} = ((\text{seen\_mask} \ll J) \mid 1) \ \& \ (2^{64} - 1)$$
- **Verdict:** `QualityStatus.SUSPICIOUS`, Reason `SEQUENCE_GAP`.

#### Case 3: Bogus Forward Jump ($\text{seq} - \text{last\_seq} > 1000$)
- **Anti-Poisoning Protection:** A single corrupted packet (e.g. sequence $2^{62}$) must not permanently poison subsequent valid packets.
- **Action:** Increment candidate counter `sequence_cand`. Do not advance `last_seq`.
- **Verdict:** Flagged `QualityStatus.SUSPICIOUS`, Reason `SEQUENCE_GAP`.
- **Re-anchoring:** If 2 consecutive packets arrive in the new jump band, the sequence resets to the new baseline.

#### Case 4: Backward or In-Window Arrival ($\text{seq} \le \text{last\_seq}$)
- Let distance $D = \text{last\_seq} - \text{seq}$.
- **Subcase 4a ($D < 64$ and bit $D$ set in `seen_mask`):**
  - **Verdict:** `QualityStatus.INVALID`, Reasons: `DUPLICATE` (and `OUT_OF_ORDER` if $D > 0$).
  - **Action:** Reject; do not emit to downstream trading strategies.
- **Subcase 4b ($D < 64$ and bit $D$ NOT set in `seen_mask`):**
  - Late arrival filling an open gap:
    - If matched in `GapManager`: `QualityStatus.VALID`, Reason `GAP_FILLED`.
    - If unmanaged: `QualityStatus.SUSPICIOUS`, Reason `OUT_OF_ORDER`.
  - **Action:** Update bitmask: $\text{seen\_mask} = \text{seen\_mask} \mid (1 \ll D)$.

---

## 3. Dynamic Price Corridor Adaptation

To avoid permanent false positive `SUSPICIOUS` flags following genuine structural market shifts (e.g. corporate earnings, circuit-breaker reopenings, $+20\%$ gap open):
- Price window tracks recent trade observations (default: 50 ticks).
- Anomalous deviations exceeding $\pm 4.0 \sigma$ increment `_price_anom` counter.
- **Regime Shift Reseed:** Upon observing `DEFAULT_PRICE_RESEED_AFTER = 8` consecutive anomalous ticks:
  - Price window is purged and re-seeded around the new price level.
  - Subsequent ticks in the new regime evaluate as `QualityStatus.VALID`.
- Price window statistics (`mean`, `variance`, `anom_count`) are fully serialized into snapshots and transactional journals.
