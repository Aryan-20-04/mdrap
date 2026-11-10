# Market Data & Live Feed Integrity — Recommendations

## Priority 0 (Release Blockers)
1. **Fix Databento Stream Fragmentation (CRIT-01)**:
   - Introduce an internal byte buffer (`self._residual_buffer = bytearray()`) in `DatabentoFeedManager`.
   - Prepend residual bytes to incoming socket chunks before calling `decode_dbn_record()`.
   - Retain unconsumed trailing bytes when `consumed == 0`.
2. **Implement TLS and CRAM Handshake for Databento (CRIT-02)**:
   - Wrap socket with `ssl.create_default_context().wrap_socket()`.
   - Parse greeting challenge and compute CRAM HMAC response.
3. **Declare `websockets` in `pyproject.toml` (HIGH-01)**:
   - Add `"websockets>=12.0,<17.0"` to `pyproject.toml` optional dependencies and dev/test requirements.

## Priority 1 (High Priority Fixes)
4. **Disallow Silent Mock Fallback in `polygon_feed.py` (MED-02)**:
   - Raise `ImportError` if `mock_mode=False` and `websockets` is missing.
5. **Add HTTP 429 Rate-Limiting & Backoff to `live.py` (HIGH-03)**:
   - Implement per-venue token bucket rate limits and inspect `Retry-After` headers.
6. **Demarcate or Remove Fabricated L2 Depth (HIGH-02)**:
   - Set `bids: []` and `asks: []` unless real top-of-book quotes are present.

## Priority 2 (Hygiene & Robustness)
7. **Fix Kraken Ticker Slicing (LOW-01)**:
   - Use `resolve_venue_symbols(symbol)["kraken"]` rather than naive string slicing.
8. **Add Optional Live Network Integration Tests (MED-03)**:
   - Add `@pytest.mark.live_network` tests for public endpoints.\n