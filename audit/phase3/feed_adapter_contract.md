# MDRAP Phase 3 — Feed Adapter Contract

**Document Identifier**: `MDRAP-ADAPTER-P3-001`  
**Date**: October 9, 2026  

---

## 1. Required Adapter Methods

Every feed adapter must implement the following interface:

```python
class FeedAdapter(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def subscribe(self, symbol: str) -> None: ...
    def unsubscribe(self, symbol: str) -> None: ...
    def poll(self, timeout_s: float = 0.0) -> RawEvent | None: ...
    def normalize(self, raw: RawEvent) -> CanonicalEvent: ...
    def health(self) -> dict[str, Any]: ...
```

---

## 2. Ingress Sequencing & Provenance Rules

1. **Source Sequence Domain**: The external exchange sequence number is extracted and audited independently for each `(venue, feed_id, session_id)` tuple.
2. **Gap Detection**: If `seq > expected_sequence`, a sequence gap is logged and `stats.gaps_detected` is incremented. The adapter emits a gap alert without discarding subsequent valid frames.
3. **Duplicate / Out-of-Order**: If `seq <= expected_sequence`, the frame is flagged, `stats.duplicates_detected` is incremented, and downstream dedup logic tags the event.
