# MDRAP Phase 2 — Configuration Contract & Precedence Specification

**Document Identifier**: `MDRAP-CONF-P2-001`  
**Status**: APPROVED & IMPLEMENTED  
**Author**: Principal Systems Engineer  
**Component**: `src/mdrap/config_loader.py`  
**Test Suite**: `tests/test_phase2_config_validation.py`  

---

## 1. Resolution Precedence Ladder

MDRAP enforces an unambiguous, monotonic precedence order across configuration layers. A higher layer strictly overrides all lower layers:

```
+----------------------------------------+
| 1. Explicit CLI Arguments              |  (Highest precedence)
+----------------------------------------+
                  | overrides
                  v
+----------------------------------------+
| 2. Environment Variables               |  (MDRAP_* variables)
+----------------------------------------+
                  | overrides
                  v
+----------------------------------------+
| 3. File Configuration (`mdrap.toml`)   |  (venue/symbol overrides -> defaults)
+----------------------------------------+
                  | overrides
                  v
+----------------------------------------+
| 4. Built-in Safe Defaults              |  (Lowest precedence / baseline)
+----------------------------------------+
```

### Provable Precedence Helper
Implemented in `resolve_precedence(cli_val, env_var_name, config_val, default_val)`:
1. Returns `cli_val` if non-None.
2. Checks `os.environ[env_var_name]`; returns value if set and non-empty.
3. Returns `config_val` if non-None.
4. Returns `default_val`.

---

## 2. Fail-Closed Validation Invariants

MDRAP adheres to institutional fail-closed security:
1. **Unknown Sections**: Any top-level table not in `{"defaults", "venue", "system", "storage", "network", "security"}` immediately aborts startup with `ConfigValidationError`.
2. **Unknown Keys**: Misspelled or unrecognized keys (e.g. `price_windw` instead of `price_window`) are strictly rejected. MDRAP will never silently ignore misspelled settings.
3. **Numeric Range Enforcement**:
   - `staleness_threshold_s`: Strictly positive finite float (`> 0.0`).
   - `price_anomaly_stddev`: Strictly positive finite float (`> 0.0`).
   - `price_window`: Positive integer (`>= 2`).
   - `dedup_cache_size`: Positive integer (`> 0`).
   - `circuit_filter_pct`: Fraction between `0.0` and `1.0`.
   - `network.port`: Valid TCP port integer (`1` to `65535`).
   - `storage.max_segment_bytes`: Positive integer (`> 0`).
