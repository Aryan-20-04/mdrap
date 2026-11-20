# Deterministic Replay: Forensic Root-Cause Analysis for Market Data Anomalies

**Audience**: Risk Engineers, Compliance Leads, Quantitative Researchers  
**Topic**: Bit-for-Bit Deterministic Post-Mortems under VirtualClock  

---

## 1. The Challenge of Market Data Post-Mortems

In traditional software systems, reproducing an incident requires setting a breakpoint and stepping through execution. In quantitative finance, however, live market conditions cannot be frozen:
- Quotes arrive asynchronously across multiple venues with microsecond variance.
- System timers, watchdog threads, and network jitter introduce non-deterministic timing.
- Traditional text logs record summarized strings rather than exact wire bytes.

When an unexpected drawdown occurs during a volatile trading session, attempting to reproduce the failure against raw text logs almost always yields divergent results.

---

## 2. The MDRAP Replay Solution: VirtualClock & Corridor Isolation

MDRAP solves the reproducibility crisis through two architectural components:

### A. Automatic Incident Corridor Capture
When an anomaly is flagged (e.g., sequence jump, spread inversion, extreme clock drift), MDRAP's `IncidentManager` opens a forensic time corridor spanning:
$$[\text{IncidentStart} - \Delta t_{\text{buffer}}, \quad \text{IncidentEnd} + \Delta t_{\text{buffer}}]$$
Typically configured for 5 to 15 seconds around the anomaly, this corridor captures all baseline and anomalous events in durable SQLite WAL storage.

### B. Isolated VirtualClock
During replay, wall-clock time is completely severed. The `IncidentReplayEngine` steps through historical events using `VirtualClock`:
- Time advances strictly according to historical `exchange_timestamp` vectors.
- Latency evaluators, watchdog timers, and BBO order-book synthesizers bind to virtual time.
- CPU scheduling jitter and operating system background tasks cannot alter replay ordering or timing calculations.

---

## 3. The Determinism State Digest

To prove that a replayed corridor reproduces the original live event sequence bit-for-bit, MDRAP computes a **Replay State Digest**:
1. Synthesizes order book evolutions, best bids, best asks, crossed states, and triggered quality reasons into a canonical ordered manifest.
2. Hashes the manifest using SHA-256:
   $$\text{StateDigest} = \text{SHA256}(\text{CanonicalReplayManifest})$$
3. Verifies that running 100 consecutive replay passes produces the exact same SHA-256 digest on every iteration.

---

## 4. Operational Benefits for Trading Teams

- **Definitive Blame Attribution**: Prove conclusively whether an execution loss was caused by faulty strategy logic or corrupted venue market data.
- **Regression Testing**: Re-run new algorithmic strategy versions against recorded historical incident corridors to confirm that updated risk filters successfully quarantine bad data.
- **Regulatory Transparency**: Deliver complete, bit-for-bit reproducible post-mortem reports to compliance committees and external regulators.
