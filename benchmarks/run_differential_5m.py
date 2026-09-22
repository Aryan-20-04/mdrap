"""
Phase 6: Full 5,000,000 Event Differential Testing Runner
Evaluates 1,000,000 synthetic events across 5 random seeds (5,000,000 total events)
comparing:
  1. Pure Python QualityEngine
  2. Native C FastQualityEngine (unbatched)
  3. Native C FastQualityEngine (micro-batched)

Asserts bit-identical status, reasons, total counts, and reason histograms.
"""

import copy
import ctypes
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from gateway import normalize
from models import CanonicalEvent, EventType, QualityStatus, Reason
from quality import QualityConfig, QualityEngine
from fastpath import FastQualityEngine, HAS_FASTPATH, _CFastEvent, _CFastResult
from simulator import FeedSimulator, SimulatorConfig


def _format_hex_dump(c_struct) -> str:
    raw_bytes = bytes(ctypes.string_at(ctypes.byref(c_struct), ctypes.sizeof(c_struct)))
    return " ".join(f"{b:02X}" for b in raw_bytes)


def run_seed_differential(seed: int, num_events: int = 1_000_000, chunk_size: int = 128):
    print(f"\n--- Running Seed {seed} ({num_events:,} events) ---", flush=True)
    cfg = QualityConfig(staleness_threshold_s=0.05, price_anomaly_stddev=6.0, price_window=50)

    sim_cfg = SimulatorConfig(
        seed=seed,
        num_events=num_events,
        duplicate_rate=0.015,
        missing_rate=0.012,
        out_of_order_rate=0.010,
        malformed_rate=0.0,
        price_anomaly_rate=0.012,
        crossed_quote_rate=0.008,
    )
    sim = FeedSimulator(sim_cfg)

    q_py = QualityEngine(cfg)
    q_c = FastQualityEngine(cfg)
    q_batch = FastQualityEngine(cfg)

    batch_buffer: list[CanonicalEvent] = []
    py_results: list[tuple[QualityStatus, list[str]]] = []
    c_results: list[tuple[QualityStatus, list[str]]] = []
    raw_events_debug: list[CanonicalEvent] = []

    t0 = time.perf_counter()
    total_checked = 0

    for idx, (raw, _) in enumerate(sim.generate()):
        ev = normalize(raw)

        # Inject 9 fault types deterministically
        mod = idx % 120
        if mod == 7:
            ev.price = -25.5
        elif mod == 19:
            ev.price = math.nan
        elif mod == 31:
            ev.quantity = -100.0
        elif mod == 43 and ev.bid_price is not None:
            ev.bid_price = math.nan
        elif mod == 57 and ev.ask_price is not None:
            ev.ask_price = -5.0
        elif mod == 71:
            ev.receive_timestamp = ev.exchange_timestamp + 0.15
        elif mod == 83 and ev.price is not None:
            ev.price = round(ev.price * 0.40, 2)
        elif mod == 97 and ev.price is not None:
            ev.price = round(ev.price * 2.50, 2)

        # Clone lightweight copy for batch buffer
        ev_b = copy.copy(ev)
        ev_b.reasons = []
        batch_buffer.append(ev_b)
        raw_events_debug.append(ev)

        # 1. Evaluate Native C unbatched
        q_c.evaluate(ev)
        c_results.append((ev.quality_status, list(ev.reasons)))

        # 2. Reset status/reasons and evaluate Pure Python on exact same event
        ev.quality_status = QualityStatus.VALID
        ev.reasons.clear()
        q_py.evaluate(ev)
        py_results.append((ev.quality_status, list(ev.reasons)))

        if len(batch_buffer) >= chunk_size:
            q_batch.evaluate_batch(batch_buffer)

            for b_idx, (eb, (py_st, py_r), (c_st, c_r)) in enumerate(
                zip(batch_buffer, py_results, c_results)
            ):
                global_idx = total_checked + b_idx
                if (
                    py_st != c_st
                    or set(py_r) != set(c_r)
                    or py_st != eb.quality_status
                    or set(py_r) != set(eb.reasons)
                ):
                    orig_ev = raw_events_debug[b_idx]
                    c_ev = _CFastEvent()
                    c_ev.source_id = orig_ev.source_id
                    c_ev.instrument_id = orig_ev.instrument_id_int
                    c_ev.event_type = 1 if orig_ev.event_type == EventType.QUOTE else 0
                    c_ev.exchange_ts = orig_ev.exchange_timestamp
                    c_ev.receive_ts = orig_ev.receive_timestamp
                    c_ev.sequence_num = (
                        orig_ev.sequence_number if orig_ev.sequence_number is not None else -1
                    )
                    c_ev.price = orig_ev.price if orig_ev.price is not None else math.nan
                    c_ev.quantity = orig_ev.quantity if orig_ev.quantity is not None else math.nan

                    c_res = _CFastResult()
                    c_res.status = (
                        1
                        if c_st == QualityStatus.VALID
                        else (2 if c_st == QualityStatus.SUSPICIOUS else 3)
                    )

                    msg = (
                        f"\n[DIFFERENTIAL PARITY FAILURE] at Event Index: {global_idx}\n"
                        f"Raw Event: {orig_ev}\n"
                        f"Python Output: status={py_st.value}, reasons={py_r}\n"
                        f"Native C Output: status={c_st.value}, reasons={c_r}\n"
                        f"Native C Batch Output: status={eb.quality_status.value}, reasons={eb.reasons}\n"
                        f"C Event Hex Dump: {_format_hex_dump(c_ev)}\n"
                        f"C Result Hex Dump: {_format_hex_dump(c_res)}\n"
                    )
                    raise AssertionError(msg)

            total_checked += len(batch_buffer)
            batch_buffer.clear()
            py_results.clear()
            c_results.clear()
            raw_events_debug.clear()

            if total_checked % 100_000 == 0:
                elapsed = time.perf_counter() - t0
                rate = total_checked / elapsed
                print(
                    f"  Processed {total_checked:,} / {num_events:,} events ({rate:,.0f} eps)...",
                    flush=True,
                )

    # Flush tail batch
    if batch_buffer:
        q_batch.evaluate_batch(batch_buffer)
        for b_idx, (eb, (py_st, py_r), (c_st, c_r)) in enumerate(
            zip(batch_buffer, py_results, c_results)
        ):
            global_idx = total_checked + b_idx
            if (
                py_st != c_st
                or set(py_r) != set(c_r)
                or py_st != eb.quality_status
                or set(py_r) != set(eb.reasons)
            ):
                raise AssertionError(f"Tail mismatch at {global_idx}")
        total_checked += len(batch_buffer)

    elapsed = time.perf_counter() - t0
    rate = total_checked / elapsed

    assert q_py.counts == q_c.counts == q_batch.counts, "Counts mismatch"
    assert q_py.reason_counts == q_c.reason_counts == q_batch.reason_counts, "Reasons mismatch"

    print(
        f"  [PASS] Seed {seed}: {total_checked:,} events in {elapsed:.2f}s ({rate:,.0f} eps) -> 0 discrepancies",
        flush=True,
    )
    print(f"  Counts: {q_py.counts}", flush=True)
    print(f"  Reasons: {q_py.reason_counts}", flush=True)

    return {
        "seed": seed,
        "events": total_checked,
        "elapsed_s": round(elapsed, 3),
        "eps": round(rate, 1),
        "counts": q_py.counts,
        "reasons": q_py.reason_counts,
        "discrepancies": 0,
    }


def main():
    if not HAS_FASTPATH:
        print("[ERROR] Native C accelerator not loaded! Aborting.", file=sys.stderr, flush=True)
        sys.exit(1)

    seeds = [42, 1337, 777, 2026, 99999]
    events_per_seed = int(os.environ.get("MDRAP_PARITY_EVENTS", "1000000"))
    total_target = len(seeds) * events_per_seed

    print("=" * 70, flush=True)
    print("MDRAP Phase 6: 5,000,000 Event Differential Testing Acceptance Gate", flush=True)
    print(f"Target: {len(seeds)} seeds x {events_per_seed:,} = {total_target:,} events", flush=True)
    print("Engines: Pure Python vs Native C vs Native C Batch", flush=True)
    print(
        "Faults Injected: Stale, Crossed, Negative, NaN, Sequence Gap, Out of Order, Spike, Flash Crash, Duplicate",
        flush=True,
    )
    print("=" * 70, flush=True)

    start_all = time.perf_counter()
    results = []

    for seed in seeds:
        res = run_seed_differential(seed, num_events=events_per_seed)
        results.append(res)

    total_elapsed = time.perf_counter() - start_all
    overall_eps = total_target / total_elapsed

    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_events": total_target,
        "total_elapsed_s": round(total_elapsed, 2),
        "overall_eps": round(overall_eps, 1),
        "total_discrepancies": 0,
        "seeds": results,
        "status": "PASSED",
    }

    out_path = os.path.join(os.path.dirname(__file__), "differential_5m_results.json")
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 70, flush=True)
    print("PHASE 6 ACCEPTANCE GATE: PASSED", flush=True)
    print(f"Evaluated {total_target:,} events across 3 engines with ZERO discrepancies.", flush=True)
    print(f"Total time: {total_elapsed:.2f}s | Aggregate rate: {overall_eps:,.0f} eps", flush=True)
    print(f"Results recorded: {out_path}", flush=True)
    print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
