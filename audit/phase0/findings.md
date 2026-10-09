# MDRAP Phase 0 — Adversarial Correctness & Security Audit Findings

**Document Identifier**: `MDRAP-AUDIT-P0-FINDINGS-001`  
**Phase**: Phase 0 (Baseline Audit Only — Zero Remediation)  
**Audit Standard**: Rigorous adversarial review across Ingress, Quality, IPC, Persistence, Security, and API.  
**Classification Criteria**:
- **Critical**: Remote compromise, silent data corruption, or catastrophic financial/trading loss.
- **High**: Severe data loss, security bypass, symbol collision, or broken durability guarantees.
- **Medium**: Discrepancy between documentation/code, denial-of-service, or identifier collision.
- **Low / Informational**: Minor inefficiency, non-critical telemetry leakage, or edge-case deprecation.

---

## Summary of Findings

| ID | Title | Severity | Confidence | Affected Subsystem |
|---|---|---|---|---|
| `FINDING-SEC-001` | Static API Key Salt & Fallback to Raw Token Hash as Authenticator | **Critical** | Confirmed | `src/security.py` |
| `FINDING-IPC-001` | 16-Character Symbol & Source Identifier Truncation in SHM Slot V3 | **High** | Confirmed | `src/fastpath.c`, `src/shm.py` |
| `FINDING-QUAL-001` | Boolean Primitives and NaN Values Ingress Validation Bypass | **High** | Confirmed | `src/gateway.py`, `src/models.py` |
| `FINDING-QUAL-002` | Out-of-Order Reordering Buffer Traps Events if `drain_expired()` Omitted | **High** | Confirmed | `src/quality.py` |
| `FINDING-DUR-001` | In-Flight Batch Loss in Async Storage Writer Queue on Abrupt Termination | **High** | Confirmed | `src/mdrap/pipeline.py` |
| `FINDING-API-001` | Sensitive Infrastructure Path Disclosure on Unauthenticated Endpoints | **Medium** | Confirmed | `src/api.py` |
| `FINDING-API-002` | Sequential TCP Client Broadcast Stalls Async Event Loop Under Slow Consumers | **Medium** | Confirmed | `src/gateway_tcp.py` |
| `FINDING-SEQ-001` | Monotonic Event Identifier Collision Across Process Restarts | **Medium** | Confirmed | `src/gateway.py` |
| `FINDING-QUAL-003` | Rule Specification Discrepancy on Locked Markets (`bid == ask`) | **Low** | Confirmed | `src/rules.def`, `src/quality.py` |

---

## Detailed Audit Findings

### FINDING-SEC-001: Static API Key Salt & Fallback to Raw Token Hash as Authenticator
- **Severity**: **Critical**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/security.py`](src/security.py#L34-L40), `hash_api_key()`, and [`src/security.py`](src/security.py#L515-L535), `SecurityManager.get_by_token_or_hash()`
- **Exact Code Evidence**:
  ```python
  API_KEY_SALT: str = os.environ.get("MDRAP_API_KEY_SALT", "mdrap_kdf_v1")

  def hash_api_key(token: str, salt: str = API_KEY_SALT) -> str:
      return hmac.new(salt.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
  ```
  In `get_by_token_or_hash(token)`:
  ```python
  # Fallback: if caller provided token_hash directly, lookup succeeds:
  if token in self._api_keys:
      return self._api_keys[token]
  ```
- **Triggering Conditions**: Any user who gains read access to the SQLite database `api_keys` table or audit logs where `token_hash` is recorded can pass that hash as the `X-API-Key` HTTP header.
- **Expected vs Observed Behavior**:
  - *Expected*: Only possession of the plaintext secret API key authenticates a client. The hash stored in the database must be a one-way derivation that cannot be used directly as credentials.
  - *Observed*: The authentication lookup accepts the stored hash directly as an active credential via dictionary fallback.
- **Operational / Financial Impact**: Total authorization bypass. Anyone observing logs or database backups inherits administrative privileges without brute-forcing the key.
- **Recommended Phase 1 Remediation**:
  1. Remove hash-as-token fallback in `get_by_token_or_hash()`. Authentication must only compute `hash_api_key(provided_token)` and check for presence in `_api_keys`.
  2. Require high-entropy per-installation salt with explicit error on default `mdrap_kdf_v1`.
- **Regression Test Required**: `tests/test_security_hash_bypass.py` asserting that passing `ent.token_hash` as `X-API-Key` returns `401 Unauthorized`.

---

### FINDING-IPC-001: 16-Character Symbol & Source Identifier Truncation in SHM Slot V3
- **Severity**: **High**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/fastpath.c`](src/fastpath.c#L180-L215), `FastSlotV3` struct layout; [`src/shm.py`](src/shm.py#L110-L140), `SHMWriter.write_tick()`
- **Exact Code Evidence**:
  In `fastpath.c`:
  ```c
  typedef struct {
      uint64_t sequence;
      char symbol[16];   /* Fixed 16-byte buffer */
      char source[16];   /* Fixed 16-byte buffer */
      double price;
      ...
  } FastSlotV3;
  ```
  In `shm.py`:
  ```python
  sym_bytes = symbol.encode("ascii")[:15]  # Truncates to 15 characters + null
  ```
- **Triggering Conditions**: Ingestion of equity options with OCC symbology (e.g., `AAPL250117C00200000` = 21 chars), crypto perpetuals (e.g., `BTC-27DEC24-100000-C` = 20 chars), or ISIN/FIGI identifiers.
- **Expected vs Observed Behavior**:
  - *Expected*: Symbol and source strings must either accommodate full institutional lengths (minimum 32 bytes) or raise an explicit validation error on overflow.
  - *Observed*: Strings longer than 15 characters are silently truncated, causing distinct instruments to share the exact same symbol slot in shared memory.
- **Operational / Financial Impact**: High-frequency trading models receive price updates for one strike price stamped with the symbol of another strike price, executing unintended orders.
- **Recommended Phase 1 Remediation**: Expand `symbol` and `source` in `FastSlotV3` to 32 bytes (cacheline aligned, 128-byte slot size) or enforce strict length limits in `gateway.py` with `Reason.SCHEMA_VIOLATION`.
- **Regression Test Required**: `tests/test_shm_long_symbol.py` writing a 24-character symbol to SHM and asserting non-truncation upon reading.

---

### FINDING-QUAL-001: Boolean Primitives and NaN Values Ingress Validation Bypass
- **Severity**: **High**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/gateway.py`](src/gateway.py#L168-L184), `normalize()`; [`src/models.py`](src/models.py#L635-L657)
- **Exact Code Evidence**:
  ```python
  if event_type_raw == "TRADE":
      price, qty = p["price"], p["quantity"]
      if not isinstance(price, (int, float)) or price <= 0:
          raise SchemaError(f"invalid price: {price!r}")
  ```
- **Triggering Conditions**: Incoming JSON message containing boolean literals `{"price": true, "quantity": true}` or IEEE-754 `NaN`.
- **Expected vs Observed Behavior**:
  - In Python, `isinstance(True, (int, float))` evaluates to `True`, and `True <= 0` evaluates to `False`. The boolean `True` is coerced to `1.0`.
  - For `float("nan")`, `math.isnan(x)` is true, but `x <= 0` evaluates to `False` in IEEE-754 comparisons. The NaN value bypasses the `< 0` check.
- **Operational / Financial Impact**: Downstream TCA and BBO pricing calculations compute `NaN` or incorrect unit prices ($1.00), corrupting risk analytics.
- **Recommended Phase 1 Remediation**:
  Replace check with:
  ```python
  if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
      raise SchemaError(...)
  ```
- **Regression Test Required**: `tests/test_gateway_adversarial_types.py` verifying that booleans, `NaN`, and `Inf` raise `SchemaError`.

---

### FINDING-QUAL-002: Out-of-Order Reordering Buffer Traps Events if `drain_expired()` Omitted
- **Severity**: **High**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/quality.py`](src/quality.py#L415-L440), `QualityEngine.evaluate()`
- **Exact Code Evidence**:
  ```python
  if jump > 1 and jump <= cfg.reorder_max_jump and len(sl.pending) < cfg.reorder_buffer_capacity:
      # Buffer event for reordering:
      sl.pending[ev.sequence_number] = (now, ev, bad, hbid, hask, hpx)
      return None  # Event held in memory; None returned to caller
  ```
- **Triggering Conditions**: Network out-of-order packet delivery where sequence numbers arrive with gaps.
- **Expected vs Observed Behavior**:
  - *Expected*: If an event is buffered, it must eventually be emitted or quarantined, with explicit caller contracts enforcing queue draining.
  - *Observed*: If the caller invokes `engine.evaluate(event)` in a loop without periodically calling `engine.drain_expired()`, buffered events are never released, resulting in silent tick loss.
- **Operational / Financial Impact**: Silent message loss in consumer codebases that instantiate `QualityEngine` directly.
- **Recommended Phase 1 Remediation**: Add internal time-triggered drain checks inside `evaluate()` or raise an explicit warning if pending events exceed timeout thresholds without caller draining.
- **Regression Test Required**: `tests/test_quality_pending_drain_leak.py`.

---

### FINDING-DUR-001: In-Flight Batch Loss in Async Storage Writer Queue on Abrupt Termination
- **Severity**: **High**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/mdrap/pipeline.py`](src/mdrap/pipeline.py#L260-L268), `Pipeline.__init__()` and `flush()`
- **Exact Code Evidence**:
  ```python
  self._write_queue: queue.Queue = queue.Queue(maxsize=128)
  ...
  self._write_queue.put((canon, quar, lin, health_rows), timeout=1.0)
  ```
- **Triggering Conditions**: Unplanned container kill (`SIGKILL`), power loss, or OS panic.
- **Expected vs Observed Behavior**:
  - *Expected*: A market event acknowledged to an upstream producer is guaranteed durable on disk (WAL).
  - *Observed*: Batches placed on the in-memory `_write_queue` have not yet hit SQLite WAL or fsync. If the process is killed abruptly, up to 128 batches (up to 256,000 events) are lost without trace.
- **Operational / Financial Impact**: Regulatory violation (MiFID II / CAT reporting audit gap).
- **Recommended Phase 1 Remediation**: Mandate `IngestLog` write-ahead logging synchronously *before* placing batches on the asynchronous SQLite writer queue.
- **Regression Test Required**: `tests/test_durability_unclean_shutdown.py`.

---

### FINDING-API-001: Sensitive Infrastructure Path Disclosure on Unauthenticated Endpoints
- **Severity**: **Medium**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/api.py`](src/api.py#L370-L420), `/v1/health` and `/health`
- **Exact Code Evidence**:
  ```python
  @router.get("/health", response_model=HealthResponse, tags=["Health"])
  def get_health(request: Request):
      return HealthResponse(
          status="HEALTHY",
          version=__version__,
          uptime_seconds=time.time() - st.start_time,
          database_path=str(st.db_path),   # Local filesystem path
          shm_path=st.shm_name,            # Internal IPC handle
          ...
      )
  ```
- **Triggering Conditions**: HTTP `GET /v1/health` or `GET /health` without headers.
- **Impact**: Information disclosure to unauthenticated network actors regarding host directory structures and user profile paths.
- **Recommended Phase 1 Remediation**: Restrict detailed database and SHM paths to `ADMIN` authenticated requests; public health endpoint must return only `{"status": "ok"}`.

---

### FINDING-API-002: Sequential TCP Client Broadcast Stalls Async Event Loop Under Slow Consumers
- **Severity**: **Medium**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/gateway_tcp.py`](src/gateway_tcp.py#L125-L147), `TCPGatewayServer.broadcast()`
- **Exact Code Evidence**:
  ```python
  for writer in list(self.clients):
      try:
          writer.write(data)
          await asyncio.wait_for(writer.drain(), timeout=0.05)
      except Exception:
          dead.append(writer)
  ```
- **Triggering Conditions**: 20 connected TCP clients where 5 clients have exhausted TCP window buffers or experiencing network latency.
- **Impact**: 5 slow clients × 50ms timeout = 250ms blocking latency per broadcast tick. The entire event loop freezes, dropping ticks for fast consumers.
- **Recommended Phase 1 Remediation**: Use decoupled per-client `asyncio.Queue` workers; the broadcast routine puts to non-blocking queues and evicts immediately on queue full.

---

### FINDING-SEQ-001: Monotonic Event Identifier Collision Across Process Restarts
- **Severity**: **Medium**
- **Confidence**: **Confirmed**
- **Affected File & Function**: [`src/gateway.py`](src/gateway.py#L25-L50)
- **Exact Code Evidence**:
  ```python
  _event_counter = itertools.count(1)
  ...
  event_id = f"evt-{next(_event_counter)}"
  ```
- **Triggering Conditions**: Process restart during the trading day.
- **Impact**: Both runs generate `evt-1`, `evt-2`, causing primary key collisions in storage or silent overwrites in downstream caches.
- **Recommended Phase 1 Remediation**: Incorporate publisher boot epoch or nanosecond timestamp into the ID prefix: `evt-{epoch_ms}-{count}`.

---

### FINDING-QUAL-003: Rule Specification Discrepancy on Locked Markets (`bid == ask`)
- **Severity**: **Low**
- **Confidence**: **Confirmed**
- **Affected Files**: [`src/rules.def`](src/rules.def#L23) vs [`src/quality.py`](src/quality.py#L462)
- **Code Evidence**:
  - `rules.def`: `RULE_DEF(CROSSED_QUOTE, 6, "Bid price greater than or equal to ask price")`
  - `quality.py`: `if ev.bid_price > ev.ask_price:`
- **Impact**: Locked market quotes (`bid == ask`) are treated as valid quotes in Python, but flagged as crossed quotes in native C and documentation.
- **Recommended Phase 1 Remediation**: Standardize rule definition and implementation across both tiers: distinguish `LOCKED_MARKET` from `CROSSED_QUOTE` with distinct bit allocations.
