# Quantitative, Trading Risk & Strategy — Recommendations

## Priority 0 (Release Blockers)
1. **Restore or Implement `backends.py` in `mdrap-strategies` (F-QNT-01)**:
   - Implement the missing backend classes (`ExecutionBackend`, etc.) or clean up imports so `mdrap_strategies` imports cleanly.
2. **Eliminate Silent 1.0 Parity in `src/mdrap/fx.py` (F-QNT-04)**:
   - Raise `ValueError(f"Unmapped currency: {currency}")` instead of returning 1.0.

## Priority 1 (Microstructure & Risk)
3. **Fix `run_walkforward` in `src/mdrap/backtest.py` (F-QNT-02)**:
   - Pass training data to strategy fitting routine or document that this is sliding testing only.
4. **Fix `VolatilityTracker` Sliding Window in `src/mdrap/analytics.py` (F-QNT-05)**:
   - Implement true sliding window or rename to `ExpandingVolatilityTracker`.
5. **Bridge Pre-Trade Risk to Core Engine (F-QNT-03)**:
   - Expose active order validation interface in core.\n