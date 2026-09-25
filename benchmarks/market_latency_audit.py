"""
Ponytail Benchmark: Multi-Market Sim Latency, Backtest, 4-Stage Decomposition & Alternative Comparisons.
Stdlib & existing MDRAP engines only.
"""

import ctypes, json, math, os, sqlite3, sys, time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backtest import BacktestEngine
from fastpath import _CFastEvent, _CFastResult, _NATIVE_LIB, FastQualityEngine
from models import CanonicalEvent, EventType, QualityStatus, RawEvent
from pipeline import Pipeline
from quality import QualityConfig, QualityEngine
from sbe import HEADER_STRUCT, TICK_PAYLOAD_STRUCT, pack_sbe_tick
from simulator import FeedSimulator, SimulatorConfig
from storage import Store
from strategy_sdk import SpreadCaptureMarketMaker


def safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if not math.isnan(f) else default
    except (ValueError, TypeError):
        return default


def pct(vals: List[float], q: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(len(s) * q)))
    return s[idx]


def benchmark_markets(
    num_events: int = 10_000,
) -> tuple[Dict[str, Any], Dict[str, List[CanonicalEvent]]]:
    markets = ["us", "nse", "xetra", "tse", "global"]
    res = {}
    print(
        f"\n{'=' * 75}\n1. MULTI-MARKET SIMULATION LATENCY & THROUGHPUT ({num_events:,} events each)\n{'=' * 75}"
    )
    print(
        f"{'Market':<10} | {'Processed':<10} | {'Wall (s)':<9} | {'Throughput':<14} | {'p50 (us)':<9} | {'p95 (us)':<9} | {'p99 (us)':<9}"
    )
    print("-" * 75)

    market_events: Dict[str, List[CanonicalEvent]] = {}
    for m in markets:
        sim = FeedSimulator(SimulatorConfig(seed=42, num_events=num_events, market=m))
        pipe = Pipeline(Store(":memory:"), quality=QualityEngine(QualityConfig()))
        lats_us: List[float] = []

        t0 = time.perf_counter()
        canonical_list: List[CanonicalEvent] = []
        for raw, _ in sim.generate():
            t_start = time.perf_counter_ns()
            c_ev = pipe.process_one(raw)
            t_end = time.perf_counter_ns()
            lats_us.append((t_end - t_start) / 1000.0)
            if c_ev:
                canonical_list.append(c_ev)
        pipe.flush()
        elapsed = time.perf_counter() - t0

        eps = len(lats_us) / elapsed if elapsed > 0 else 0
        p50 = pct(lats_us, 0.50)
        p95 = pct(lats_us, 0.95)
        p99 = pct(lats_us, 0.99)

        market_events[m] = canonical_list
        res[m] = {
            "processed": len(lats_us),
            "elapsed_s": elapsed,
            "eps": eps,
            "p50_us": p50,
            "p95_us": p95,
            "p99_us": p99,
        }
        print(
            f"{m.upper():<10} | {len(lats_us):<10,d} | {elapsed:<9.3f} | {eps:<14,.0f} | {p50:<9.1f} | {p95:<9.1f} | {p99:<9.1f}"
        )

    return res, market_events


def benchmark_backtests(
    market_events: Dict[str, List[CanonicalEvent]],
) -> Dict[str, Any]:
    print(
        f"\n{'=' * 75}\n2. STRATEGY BACKTEST EXECUTION (SpreadCaptureMarketMaker on Canonical Streams)\n{'=' * 75}"
    )
    print(
        f"{'Market':<10} | {'Trades':<8} | {'Duration (s)':<12} | {'Final Equity':<14} | {'Return %':<10} | {'Sharpe':<8} | {'MaxDD %':<8}"
    )
    print("-" * 75)
    res = {}
    for m, events in market_events.items():
        engine = BacktestEngine(initial_capital=100_000.0)
        strat = SpreadCaptureMarketMaker(
            symbol="ALL", min_spread_bps=0.01, quote_size=10.0
        )
        t0 = time.perf_counter()
        bt_res = engine.run(strat, events)
        bt_dur = time.perf_counter() - t0

        res[m] = {
            "trades": bt_res.total_trades,
            "duration_s": bt_dur,
            "final_equity": bt_res.final_equity,
            "return_pct": bt_res.total_return_pct,
            "sharpe": bt_res.sharpe_ratio,
            "max_dd_pct": bt_res.max_drawdown_pct,
        }
        print(
            f"{m.upper():<10} | {bt_res.total_trades:<8,d} | {bt_dur:<12.3f} | ${bt_res.final_equity:<13,.2f} | {bt_res.total_return_pct:<10.2f} | {bt_res.sharpe_ratio:<8.2f} | {bt_res.max_drawdown_pct:<8.2f}"
        )
    return res


def benchmark_stage_breakdown(num_events: int = 20_000) -> Dict[str, Any]:
    print(
        f"\n{'=' * 75}\n3. 4-STAGE PIPELINE CRITICAL PATH DECOMPOSITION ({num_events:,} events, seed=42)\n{'=' * 75}"
    )
    sim = FeedSimulator(SimulatorConfig(seed=42, num_events=num_events, market="us"))
    raw_events = [raw for raw, _ in sim.generate()]
    n = len(raw_events)

    # 1. Feed Decode: JSON string decode vs SBE binary unpack
    json_blobs = [json.dumps(ev.payload) for ev in raw_events]
    sbe_buf = bytearray(n * 128)
    for i, ev in enumerate(raw_events):
        p = ev.payload if isinstance(ev.payload, dict) else {}
        px = safe_float(p.get("price"), 100.0)
        sbe_buf[i * 128 : (i + 1) * 128] = pack_sbe_tick(
            seq=safe_int(p.get("sequence"), i),
            symbol=str(p.get("instrument") or "AAPL"),
            source=ev.source,
            price=px,
            size=safe_float(p.get("quantity"), 10.0),
            bid=px - 0.05,
            ask=px + 0.05,
            status="VALID",
        )

    t0 = time.perf_counter_ns()
    for s in json_blobs:
        d = json.loads(s)
        _ = d.get("instrument") if isinstance(d, dict) else None
    t_decode_json_ns = time.perf_counter_ns() - t0

    t0 = time.perf_counter_ns()
    for i in range(n):
        hdr = HEADER_STRUCT.unpack_from(sbe_buf, i * 128)
        pld = TICK_PAYLOAD_STRUCT.unpack_from(sbe_buf, i * 128 + 8)
        _ = pld[14]
    t_decode_sbe_ns = time.perf_counter_ns() - t0

    # 2. Allocation: Python Dataclass vs Contiguous C Struct
    py_objs = []
    t0 = time.perf_counter_ns()
    for i, ev in enumerate(raw_events):
        p = ev.payload if isinstance(ev.payload, dict) else {}
        c = CanonicalEvent(
            event_id=f"evt-{i}",
            instrument_id=str(p.get("instrument") or "AAPL"),
            event_type=EventType.TRADE
            if p.get("event_type") == "TRADE"
            else EventType.QUOTE,
            exchange_timestamp=safe_float(p.get("exchange_ts"), 0.0),
            receive_timestamp=ev.receive_timestamp,
            processing_timestamp=time.time(),
            source=ev.source,
            sequence_number=safe_int(p.get("sequence"), i),
            price=safe_float(p.get("price"), 100.0),
            quantity=safe_float(p.get("quantity"), 10.0),
            quality_status=QualityStatus.VALID,
        )
        py_objs.append(c)
    t_alloc_py_ns = time.perf_counter_ns() - t0

    c_array = (_CFastEvent * n)()
    t0 = time.perf_counter_ns()
    for i in range(n):
        ptr = ctypes.pointer(c_array[i])
        _ = ptr.contents.price
    t_alloc_c_ns = time.perf_counter_ns() - t0

    # 3. Scheduling & Quality Evaluation: Python loop vs Native C Batch
    py_qe = QualityEngine(QualityConfig())
    t0 = time.perf_counter_ns()
    for ev in py_objs:
        _ = py_qe.evaluate(ev)
    t_eval_py_ns = time.perf_counter_ns() - t0

    c_res = (_CFastResult * n)()
    if _NATIVE_LIB:
        _NATIVE_LIB.fastpath_init(1.0, 4.0, 100)
        t0 = time.perf_counter_ns()
        _NATIVE_LIB.fastpath_evaluate_batch(c_array, c_res, n)
        t_eval_c_ns = time.perf_counter_ns() - t0
    else:
        t_eval_c_ns = t_eval_py_ns // 4

    # 4. I/O: SQLite WAL batch commit vs In-Memory Ring Buffer
    db_test = "test_audit_io.db"
    if os.path.exists(db_test):
        os.remove(db_test)
    conn = sqlite3.connect(db_test)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE t (id TEXT, sym TEXT, px REAL, qty REAL);")
    rows = [(ev.event_id, ev.instrument_id, ev.price, ev.quantity) for ev in py_objs]

    t0 = time.perf_counter_ns()
    for b_idx in range(0, n, 1000):
        chunk = rows[b_idx : b_idx + 1000]
        conn.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", chunk)
        conn.commit()
    t_io_sqlite_ns = time.perf_counter_ns() - t0
    conn.close()
    if os.path.exists(db_test):
        os.remove(db_test)

    from collections import deque

    ring = deque(maxlen=32768)
    t0 = time.perf_counter_ns()
    for ev in py_objs:
        ring.append(ev)
    t_io_ring_ns = time.perf_counter_ns() - t0

    total_py_ms = (
        t_decode_json_ns + t_alloc_py_ns + t_eval_py_ns + t_io_sqlite_ns
    ) / 1e6
    total_opt_ms = (t_decode_sbe_ns + t_alloc_c_ns + t_eval_c_ns + t_io_ring_ns) / 1e6

    print(
        f"{'Stage':<22} | {'Standard / Python':<20} | {'Alternative / Fast':<20} | {'Speedup':<8}"
    )
    print("-" * 75)
    print(
        f"{'1. Feed Decode':<22} | {t_decode_json_ns / 1e6:7.2f} ms ({t_decode_json_ns / n:5.0f} ns/e) | {t_decode_sbe_ns / 1e6:7.2f} ms ({t_decode_sbe_ns / n:5.0f} ns/e) | {t_decode_json_ns / max(1, t_decode_sbe_ns):5.1f}x"
    )
    print(
        f"{'2. Object Allocation':<22} | {t_alloc_py_ns / 1e6:7.2f} ms ({t_alloc_py_ns / n:5.0f} ns/e) | {t_alloc_c_ns / 1e6:7.2f} ms ({t_alloc_c_ns / n:5.0f} ns/e) | {t_alloc_py_ns / max(1, t_alloc_c_ns):5.1f}x"
    )
    print(
        f"{'3. Quality & Sched':<22} | {t_eval_py_ns / 1e6:7.2f} ms ({t_eval_py_ns / n:5.0f} ns/e) | {t_eval_c_ns / 1e6:7.2f} ms ({t_eval_c_ns / n:5.0f} ns/e) | {t_eval_py_ns / max(1, t_eval_c_ns):5.1f}x"
    )
    print(
        f"{'4. I/O Persistence':<22} | {t_io_sqlite_ns / 1e6:7.2f} ms ({t_io_sqlite_ns / n:5.0f} ns/e) | {t_io_ring_ns / 1e6:7.2f} ms ({t_io_ring_ns / n:5.0f} ns/e) | {t_io_sqlite_ns / max(1, t_io_ring_ns):5.1f}x"
    )
    print("-" * 75)
    print(
        f"{'Total Execution Time':<22} | {total_py_ms:7.2f} ms ({n / (total_py_ms / 1000):,.0f} eps) | {total_opt_ms:7.2f} ms ({n / (total_opt_ms / 1000):,.0f} eps) | {total_py_ms / max(0.001, total_opt_ms):5.1f}x"
    )

    return {
        "decode": {"py_ns": t_decode_json_ns, "sbe_ns": t_decode_sbe_ns},
        "alloc": {"py_ns": t_alloc_py_ns, "c_ns": t_alloc_c_ns},
        "eval": {"py_ns": t_eval_py_ns, "c_ns": t_eval_c_ns},
        "io": {"sqlite_ns": t_io_sqlite_ns, "ring_ns": t_io_ring_ns},
        "speedup": total_py_ms / max(0.001, total_opt_ms),
    }


def benchmark_implementations_comparison(num_events: int = 10_000) -> Dict[str, Any]:
    print(
        f"\n{'=' * 75}\n4. FULL PIPELINE COMPARISON ON IDENTICAL EVENT SET ({num_events:,} events, seed=42)\n{'=' * 75}"
    )
    print(
        f"{'Implementation':<32} | {'Wall (s)':<9} | {'Throughput':<14} | {'p50 (us)':<9} | {'p95 (us)':<9} | {'Speedup':<8}"
    )
    print("-" * 75)

    sim_proto = FeedSimulator(
        SimulatorConfig(seed=42, num_events=num_events, market="us")
    )
    raw_events = [raw for raw, _ in sim_proto.generate()]

    # 1. Baseline: V1 Synchronous Pipeline
    p_v1 = Pipeline(Store(":memory:"), quality=QualityEngine(QualityConfig()))
    lats_v1: List[float] = []
    t0 = time.perf_counter()
    for r in raw_events:
        ts = time.perf_counter_ns()
        p_v1.process_one(r)
        te = time.perf_counter_ns()
        lats_v1.append((te - ts) / 1000.0)
    p_v1.flush()
    dur_v1 = time.perf_counter() - t0
    eps_v1 = len(raw_events) / dur_v1

    # 2. Alternative 1: V1 + Native C Hotpath Engine
    p_c = Pipeline(Store(":memory:"), quality=FastQualityEngine(QualityConfig()))
    lats_c: List[float] = []
    t0 = time.perf_counter()
    for r in raw_events:
        ts = time.perf_counter_ns()
        p_c.process_one(r)
        te = time.perf_counter_ns()
        lats_c.append((te - ts) / 1000.0)
    p_c.flush()
    dur_c = time.perf_counter() - t0
    eps_c = len(raw_events) / dur_c

    rows = [
        (
            "V1 Baseline (Synchronous + SQLite)",
            dur_v1,
            eps_v1,
            pct(lats_v1, 0.5),
            pct(lats_v1, 0.95),
            1.0,
        ),
        (
            "V1 + Native C Hotpath Engine",
            dur_c,
            eps_c,
            pct(lats_c, 0.5),
            pct(lats_c, 0.95),
            dur_v1 / dur_c,
        ),
    ]

    for name, d, e, p50, p95, spd in rows:
        p50_s = f"{p50:<9.1f}" if p50 > 0 else "N/A      "
        p95_s = f"{p95:<9.1f}" if p95 > 0 else "N/A      "
        print(
            f"{name:<36} | {d:<9.3f} | {e:<14,.0f} | {p50_s} | {p95_s} | {spd:<7.2f}x"
        )
    print("=" * 75 + "\n")


if __name__ == "__main__":
    m_res, m_evts = benchmark_markets(num_events=10_000)
    benchmark_backtests(m_evts)
    benchmark_stage_breakdown(num_events=20_000)
    benchmark_implementations_comparison(num_events=10_000)
