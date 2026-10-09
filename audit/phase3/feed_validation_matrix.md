# MDRAP Phase 3 — Feed Validation Matrix

**Document Identifier**: `MDRAP-FEEDVAL-P3-001`  
**Date**: October 9, 2026  

---

## 1. Input Validation Rules per Field

| Field | Validation Rule | Violation Handling |
|---|---|---|
| `venue` | Non-empty ASCII string | Rejection at initialization (`IngressError`) |
| `feed_id` | Non-empty ASCII string | Rejection at initialization (`IngressError`) |
| `sequence` | Monotonic uint64 | Gap / Duplicate detection telemetry update |
| `price` | Finite double > 0.0 | Tagged `Reason.SCHEMA_VIOLATION` -> Quarantine |
| `quantity` | Finite double >= 0.0 | Tagged `Reason.SCHEMA_VIOLATION` -> Quarantine |
| `bid` / `ask` | Finite double, `bid <= ask` | Tagged `Reason.CROSSED_QUOTE` -> Quarantine |
| `exchange_ts` | Nanoseconds within 24h of system clock | Fallback to `receive_timestamp` if missing |
| `frame_length`| `<= max_frame_bytes` (default 64KB) | Rejection with `FramingError` |
