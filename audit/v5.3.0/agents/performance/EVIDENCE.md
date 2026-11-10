# Performance & Latency Engineering — Exact Evidence

## 1. Parity Divergence on HALT / STATUS
- File: `src/mdrap/quality.py:360-366`
  ```python
  is_status = event.event_type in (EventType.HALT, EventType.STATUS, "HALT", "STATUS")
  if not is_trade and not is_quote and not is_status:
      self._mark(event, QualityStatus.INVALID, Reason.SCHEMA_VIOLATION)
      return event
  ```
- File: `src/mdrap/fastpath.py:1232-1240`
  ```python
  c_ev_type = (
      1 if (event.event_type == EventType.QUOTE or event.event_type == "QUOTE")
      else (0 if (event.event_type == EventType.TRADE or event.event_type == "TRADE") else -1)
  )
  ```
- File: `src/mdrap/fastpath.c:677-680`
  ```c
  if (ev->event_type != 0 && ev->event_type != 1) {
      mark(res, STATUS_INVALID, REASON_SCHEMA_VIOLATION);
      return;
  }
  ```

## 2. Benchmark Raw Discrepancy
- File: `audit/v5.3/raw/benchmarks_raw.json:4-24`
  ```json
  "boundary_c_quality_c_fastpath_eps": 416331.2,
  "boundary_c_quality_python_fallback_eps": 149885.7,
  "quality_c_fastpath_speedup": 2.7776579607861304
  ```
- File: `README.md:157-158`
  ```markdown
  | **Native C Fastpath FFI** | ... | **510,820** |
  | **Pure Python Fallback Parity** | ... | **170,080** |
  ```\n