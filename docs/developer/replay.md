# Deterministic Incident Replay

MDRAP includes an offline, deterministic replay engine (`mdrap.replay`) designed for forensic root-cause analysis, post-mortem audits, and algorithmic regression testing.

---

## 1. Why Deterministic Replay Matters

Traditional log files record what happened, but cannot recreate internal execution dynamics. When a high-frequency trading firm experiences a loss due to a corrupt quote or sequence gap, quantitative researchers must answer:
- *Did our strategy fail because of bad data or bad logic?*
- *How did the synthetic order book evolve leading up to the anomaly?*
- *If we re-run the exact market stream under an isolated virtual clock, does the pricing outcome reproduce bit-for-bit?*

MDRAP guarantees 100% deterministic reproducibility across historical incident corridors.

---

## 2. VirtualClock Synchronization

During replay, wall-clock time is completely isolated. Instead, MDRAP steps through historical ticks using `VirtualClock`:

```python
class VirtualClock:
    """Monotonically advancing virtual clock for deterministic replay."""
    def __init__(self, initial_time: float = 0.0):
        self._current_time: float = float(initial_time)

    @property
    def now(self) -> float:
        return self._current_time

    def advance(self, delta_seconds: float) -> float:
        if delta_seconds < 0:
            raise ValueError("Virtual clock cannot step backward")
        self._current_time += delta_seconds
        return self._current_time
```

Because downstream components (staleness evaluators, watchdog timers, rate limiters) bind to `VirtualClock.now`, replay behavior is completely invariant to CPU load, scheduling jitter, or host system performance.

---

## 3. Incident Corridor Replay

To replay an incident corridor:

### Via CLI:
```bash
python -m mdrap.cli replay --incident inc-1700000001-0001 --buffer 5.0
```

### Via Python:
```python
from mdrap.replay import IncidentReplayEngine

replayer = IncidentReplayEngine(db_path="data/mdrap.db")
result = replayer.replay_incident(incident_id="inc-1700000001-0001", buffer_s=5.0)

print(f"Events Replayed:     {result.events_replayed}")
print(f"Is Reproducible:     {result.is_reproducible}")
print(f"Reproduced Anomalies:{result.reproduced_anomalies}")
print(f"Replay State Hash:   {result.replayed_state_hash}")
```

---

## 4. State Digest Computation

To verify that multiple replay passes do not diverge, MDRAP synthesizes the entire order book and quality timeline into a canonical JSON representation and computes its SHA-256 state digest:

```python
digest_manifest = json.dumps(
    {
        "incident_id": incident.incident_id,
        "events_count": len(events),
        "timeline": timeline,
        "final_bbo": final_bbo,
    },
    sort_keys=True,
)
state_hash = hashlib.sha256(digest_manifest.encode("utf-8")).hexdigest()
```

If any internal state, timestamp ordering, or price calculation changes between runs, the state hash will immediately diverge.
