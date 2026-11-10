# Market Data & Live Feed Integrity — Exact Evidence

## 1. Databento Stream Fragmentation
- File: `src/mdrap/databento_feed.py:526-533`
  ```python
  while offset < total_len and not self._stop_event.is_set():
      ev, consumed = decode_dbn_record(chunk, offset, self.resolver)
      if consumed <= 0:
          break
      offset += consumed
      self._stats["records_decoded"] += 1
      if ev:
          self._enqueue_event(ev)
  ```

## 2. Databento Plaintext Auth
- File: `src/mdrap/databento_feed.py:568-582`
  ```python
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  s.connect((host, port))
  _greeting = s.recv(1024).decode("utf-8", errors="replace")
  auth_payload = f"auth={self.api_key}|dataset={self.dataset}|schema={self.schema}\n"
  s.sendall(auth_payload.encode("utf-8"))
  ```

## 3. Missing `websockets` Dependency
- File: `pyproject.toml:39-44`
  ```toml
  dependencies = []
  ```
- File: `src/mdrap/ws_feed.py:598-602`
  ```python
  if not HAS_WEBSOCKETS:
      logger.error("[ws_feed] 'websockets' library not installed. WebSocket streaming disabled.")
      return
  ```

## 4. Fabricated L2 Depth in `live.py`
- File: `src/mdrap/live.py:855-863`
  ```python
  bids_l2 = [
      [round(bid_p - i * spread_offset, 2), float(100 * (i + 1))]
      for i in range(5)
      if round(bid_p - i * spread_offset, 2) > 0
  ]
  asks_l2 = [
      [round(ask_p + i * spread_offset, 2), float(100 * (i + 1))]
      for i in range(5)
  ]
  ```

## 5. Unbounded REST Polling
- File: `src/mdrap/live.py:1032, 1108`
  ```python
  poll_interval_s: float = 0.05
  ...
  time.sleep(poll_interval_s)
  ```\n