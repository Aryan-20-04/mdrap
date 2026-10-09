# MDRAP Phase 7 — API, SDK, and Schema Compatibility Assurance Strategy

## 1. Executive Summary & Purpose
In institutional trading ecosystems, upgrading server infrastructure must never break downstream client algorithms, risk analytics, or proprietary trading models running on older SDK versions.

This strategy defines the **Compatibility Assurance Framework**, governing Simple Binary Encoding (SBE) schema evolution, public API surface stability, golden test vector verification, and cross-language client compatibility.

---

## 2. Compatibility Guarantees & Schema Evolution Rules

1. **Fixed Binary Offsets**: SBE wire format header fields (message size, template ID, schema version, sequence, timestamps, symbol, scaled price, scaled quantity) occupy immutable byte offsets across all versions.
2. **Trailing Optional Fields Only**: Any new field added in schema versions $v2+$ must be carved from the trailing 66-byte `extension_padding` space.
3. **Graceful Unknown Template Handling**: Client SDKs encountering unrecognized `template_id` integers skip the frame using `message_size` without raising fatal deserialization errors.
4. **SemVer Enforcement**: Public Python interfaces adhere to SemVer 2.0.0. Deprecated interfaces emit warnings for a minimum of 2 minor releases prior to removal.

---

## 3. Golden Vector Verification
MDRAP maintains an immutable library of **Golden Test Vectors**: byte sequences produced by canonical reference encoders with independently verified expected values.

During continuous integration:
- Golden vectors are deserialized by the current parser and asserted for bit-for-bit equivalence.
- Newly serialized events are compared against golden vector layouts to ensure no compiler or architecture-specific padding deviations occur.
