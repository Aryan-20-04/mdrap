# MDRAP Failure Injection & Chaos Engineering Matrix

Institutional market data infrastructure must fail predictably and guarantee zero silent data loss. This document records the automated chaos testing matrix, expected failure behaviors, and platform recovery mechanisms.

---

## Failure Injection Scenarios

| Failure Scenario | Injected Condition | Expected System Behavior | Recovery Mechanism | Tested In |
|---|---|---|---|---|
| **Feed Disconnect** | Feed stops emitting ticks mid-stream | Watchdog transitions feed to `DEGRADED` then `SILENT` within configured threshold | Automatic failover to next-highest reliability peer feed | `tests/test_chaos.py::test_feed_kill_drill` |
| **Database Unavailable** | SQLite lock contention or disk I/O timeout | Engine catches write exception without dropping events | Spills atomic batch to fsync'd dead-letter JSONL fallback | `tests/test_chaos.py::test_storage_outage_drill` |
| **Network Latency / Jitter** | Sudden 5.0s flight delay on ticks | Latency exceeds 50ms threshold, tagged `STALE` | Feed reliability score penalized; routed to quarantine | `tests/test_chaos.py::test_network_jitter_drill` |
| **Packet Storm / Duplicate Burst** | 300+ duplicate quotes transmitted | Deduplication filter detects duplicates; status set to `INVALID` | Isolated to quarantine; canonical book untouched | `tests/test_chaos.py::test_burst_drill` |
| **Sequence Gap / Packet Loss** | Sequence jumps by > 1 (e.g. 50 packets dropped) | `SEQUENCE_GAP` flagged; reliability tracker penalized | Reorder buffer waits up to 2ms; gap logged if unfilled | `tests/test_quality_hardening_matrix.py` |
| **Clock Jump / Future Skew** | Exchange timestamp leads local clock by > 1.0s | `TS_IMPLAUSIBLE` tagged; marked `SUSPICIOUS` | Quarantined; does not poison cross-feed recency window | `tests/test_quality_hardening_matrix.py` |
| **Corrupt Payload / Malformed Frame** | Non-finite values (`NaN`/`Inf`) or corrupt JSON | Gateway rejects malformed frame; marked `SCHEMA_VIOLATION` | Isolated with full original payload preserved | `tests/test_model_fuzz.py` |
| **SHM Ring Buffer Overrun** | Slow consumer lags behind high-speed producer | Overrun stats record skipped laps; watermark bit set | Telemetry logged; consumer can snapshot-resync | `tests/test_shm_watermark.py` |

---

## Verifying Chaos Tests

To run the automated chaos drills locally:

```bash
pytest tests/test_chaos.py -v
```
