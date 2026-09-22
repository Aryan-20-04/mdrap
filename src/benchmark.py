"""
Benchmarking Framework.

Runs the pipeline against the deterministic simulator for a fixed
configuration, measures throughput/latency, and scores quality-engine
detection against the simulator's known ground truth. Every result is
written with the full reproducibility record the spec asks for
(section 21): config, code version, environment, dataset identity,
timestamp -- nothing is reported as a "result" without an actual
timed, versioned run behind it.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from dataclasses import asdict

from fastpath import FastQualityEngine
from models import QualityStatus, Reason
from pipeline import Pipeline
from simulator import FeedSimulator, SimulatorConfig
from storage import Store

# Which injected fault label should map to which quality outcome, so
# detection can be scored automatically. "missing" is handled
# separately since a missing event never gets emitted (see report()).
EXPECTED = {
    "duplicate": (QualityStatus.INVALID, Reason.DUPLICATE),
    "out_of_order": (QualityStatus.SUSPICIOUS, Reason.OUT_OF_ORDER),
    "malformed": (QualityStatus.INVALID, Reason.SCHEMA_VIOLATION),
    "price_anomaly": (QualityStatus.SUSPICIOUS, Reason.PRICE_ANOMALY),
    "crossed_quote": (QualityStatus.INVALID, Reason.CROSSED_QUOTE),
}


def _env_info() -> dict:
    info = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    info["mem_total_kb"] = int(line.split()[1])
                    break
    except OSError:
        pass
    return info


def run_benchmark(
    sim_config: SimulatorConfig,
    db_path: str = ":memory:",
    warmup_events: int = 0,
    label: str = "run",
    version: str = "v1",
    fastpath: bool = True,
) -> dict:
    sim = FeedSimulator(sim_config)
    store = Store(db_path)
    if fastpath:
        quality = FastQualityEngine()
    else:
        from quality import QualityEngine

        quality = QualityEngine()
    pipeline = Pipeline(store, quality=quality)

    _ground_truth = {}  # raw_id -> label (for labeled fault types other than "missing")
    detected = {k: 0 for k in EXPECTED}
    injected_seen = {k: 0 for k in EXPECTED}
    false_positive_counts = {r.value: 0 for r in Reason}
    total_valid_no_fault = 0

    gen = sim.generate()

    # Optional warm-up: process and discard, not counted in timed metrics.
    for _ in range(warmup_events):
        try:
            raw, _label = next(gen)
        except StopIteration:
            break
        pipeline.process_one(raw)
    pipeline.flush()  # flush warmup events so they don't leak into timed run
    pipeline.metrics = pipeline.metrics.__class__()  # reset timer/counters post warm-up
    if hasattr(pipeline, "reset_ground_truth"):
        pipeline.reset_ground_truth()

    for raw, label_ in gen:
        result = pipeline.process_one(raw)
        if label_ is not None:
            injected_seen[label_] += 1
            expected_status, expected_reason = EXPECTED[label_]
            if (
                result is not None
                and result.quality_status == expected_status
                and expected_reason.value in result.reasons
            ):
                detected[label_] += 1
        else:
            total_valid_no_fault += 1
            if result is not None and result.quality_status != QualityStatus.VALID:
                for r in result.reasons:
                    false_positive_counts[r] = false_positive_counts.get(r, 0) + 1
    pipeline.finish()
    _t1 = time.time()

    # "missing" can't be scored per-event (nothing was emitted); use the
    # sequence-gap reason count as an approximate proxy and say so plainly.
    gap_detected = pipeline.quality.reason_counts.get(Reason.SEQUENCE_GAP.value, 0)

    quality_report = {}
    for k in EXPECTED:
        inj = injected_seen[k]
        quality_report[k] = {
            "injected": inj,
            "detected": detected[k],
            "detection_rate": round(detected[k] / inj, 4) if inj else None,
        }
    quality_report["missing_sequence_gap_proxy"] = {
        "injected_missing_events": sim.injected["missing"],
        "sequence_gap_flags_raised": gap_detected,
        "note": "approximate: consecutive misses can collapse into one gap flag",
    }
    false_positive_rate = (
        sum(false_positive_counts.values()) / total_valid_no_fault
        if total_valid_no_fault
        else None
    )

    result = {
        "label": label,
        "pipeline_version": "v1",
        "accelerator": "native_c" if fastpath else "cpython",
        "timestamp": time.time(),
        "code_version": pipeline.code_version,
        "environment": _env_info(),
        "config": asdict(sim_config),
        "warmup_events": warmup_events,
        "dataset_seed": sim_config.seed,
        "performance": pipeline.metrics.summary(),
        "quality": {
            "detection_by_fault_type": quality_report,
            "false_positive_rate_on_clean_events": (
                round(false_positive_rate, 6)
                if false_positive_rate is not None
                else None
            ),
            "false_positive_breakdown": false_positive_counts,
            "clean_events_evaluated": total_valid_no_fault,
        },
        "storage_counts": store.counts(),
        "source_reliability": pipeline.reliability.scores(),
    }
    store.close()
    return result


def save_result(result: dict, out_dir: str = "benchmarks") -> str:
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{result['label']}_{int(result['timestamp'])}.json"
    path = os.path.join(out_dir, fname)
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path
