# Security & Threat Modeling — Recommendations

## Priority 0 (Release Blockers)
1. **Fix `/metrics` IP Spoofing (F-SEC-01)**:
   - Remove loopback authentication exemptions. Require explicit monitoring API key or inspect rightmost untrusted IP.
2. **Correct RFC 6962 Domain Separation in `mdrap-audit` (F-SEC-03)**:
   - Call `self.hash_internal_node(left, right)` in `build()` and prepend `b"\x01"` in `InclusionProof.verify()`.
3. **Fail-Closed WebSocket Revocation Check (F-SEC-02)**:
   - Replace `if tok and not ent:` with `if not ent:` in `broadcast_event()`.

## Priority 1 (High Priority Fixes)
4. **Add Browser WebSocket Subprotocol Authentication (F-SEC-02)**:
   - Support `Sec-WebSocket-Protocol: mdrap-token.<token>` or ticket exchange endpoint.
5. **Mitigate Readiness Storage Lock Contention (F-SEC-04)**:
   - Probe database via `read_conn` or cached heartbeat rather than acquiring write lock.
6. **Harden SHM Permissions Creation (F-SEC-05)**:
   - Wrap creation with `umask(0o077)` to guarantee atomic 0600 mode.\n