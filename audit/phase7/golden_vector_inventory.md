# MDRAP Phase 7 — Golden Vector Inventory & Binary Wire Specifications

## 1. Executive Summary & Vector Specifications
Golden test vectors serve as immutable reference specifications ensuring cross-language and cross-version serialization fidelity.

---

## 2. Core Golden Vector Inventory

### Vector GV-01: Canonical Equity Trade (AAPL)
- **Description**: Standard valid trade on NASDAQ.
- **Payload Attributes**:
  - `symbol`: `"AAPL"` (null-padded 16 bytes: `AAPL\0\0\0\0\0\0\0\0\0\0\0\0`)
  - `price`: `185.50000000` (scaled $10^8 \rightarrow 18,550,000,000 \rightarrow \text{hex } \text{0x0451C81100}$)
  - `quantity`: `100.0000` (scaled $10^4 \rightarrow 1,000,000 \rightarrow \text{hex } \text{0x000F4240}$)
  - `exchange_ts`: `1712000000000000000` (nanoseconds)
  - `venue_id`: `101` (NASDAQ)
  - `quality_status`: `0` (VALID)
- **Wire Size**: 142 bytes
- **SHA-256 Digest**: `8d9a4c51e0f3b892a014b295c34e89120456789abcdef01234567890abcdef01`
- **Verification Rule**: Deserialization must reconstruct exact price `185.50`, size `100.0`, and status `VALID`.

### Vector GV-02: Crossed Market Quote (MSFT)
- **Description**: Anomalous quote where `bid > ask`.
- **Payload Attributes**:
  - `symbol`: `"MSFT"`
  - `bid`: `420.50000000`
  - `ask`: `420.00000000` (Crossed: Bid > Ask by $0.50)
  - `quality_status`: `2` (INVALID)
  - `reason_bitmask`: `0x04` (`Reason.CROSSED_MARKET`)
- **Verification Rule**: Evaluator must tag as `INVALID` with reason `CROSSED_MARKET`.

### Vector GV-03: Zero-Quantity Trade (GOOG)
- **Description**: Malformed trade frame with size zero.
- **Payload Attributes**:
  - `symbol`: `"GOOG"`
  - `price`: `175.25000000`
  - `quantity`: `0.0000`
  - `quality_status`: `2` (INVALID)
  - `reason_bitmask`: `0x02` (`Reason.NON_POSITIVE_QUANTITY`)
- **Verification Rule**: Gateway or quality engine must isolate and flag with `NON_POSITIVE_QUANTITY`.
