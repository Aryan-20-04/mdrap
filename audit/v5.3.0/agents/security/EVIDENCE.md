# Security & Threat Modeling — Exact Evidence

## 1. Metrics Spoofing Bypass
- File: `src/mdrap/api.py:84`
  ```python
  candidate = forwarded.split(",", 1)[0].strip()  # Leftmost entry!
  ```
- File: `src/mdrap/api.py:1673-1678`
  ```python
  is_loopback = client_host in loopback_hosts
  if metrics_auth_required and not is_loopback:
      # Auth required only if NOT loopback
  ```

## 2. Incomplete WebSocket Revocation
- File: `src/mdrap/api.py:376`
  ```python
  if tok and not ent:
      dead_sockets.append(ws)
      continue
  ```

## 3. Broken RFC 6962 Domain Separation
- File: `packages/mdrap-audit/mdrap_audit/__init__.py:88-91`
  ```python
  combined = (left + right).encode("utf-8")  # Missing 0x01 prefix!
  parent = sha256_hex(combined)
  ```
- File: `src/mdrap/security.py:211-213` (Correct Core Implementation)
  ```python
  return hashlib.sha256(b"\x01" + bytes.fromhex(left_hex) + bytes.fromhex(right_hex)).hexdigest()
  ```\n