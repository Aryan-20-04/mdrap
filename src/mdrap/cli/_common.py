#!/usr/bin/env python3
"""
Market Data Reliability & Acceleration Platform — terminal CLI.

Quick start (use the installed 'mdrap' command):

    mdrap s                          status dashboard
    mdrap r --events 50000           run pipeline (short for 'run')
    mdrap r -v v2 --fastpath -d      run V2 + C hotpath + live dashboard
    mdrap a ohlcv AAPL               OHLCV candles (short for 'analytics')
    mdrap a spread all               bid-ask spread analysis
    mdrap a vol                      realized volatility
    mdrap q health                   source reliability (short for 'query')
    mdrap q latest AAPL              latest canonical event
    mdrap w status                   watchdog source states (short for 'watchdog')
    mdrap t                          run full test suite (short for 'test-all')

Run `mdrap <command> -h` for the full flag list.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import sys
import time
from dataclasses import fields
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from ..benchmark import run_benchmark, save_result  # noqa: E402
from ..pipeline import Pipeline  # noqa: E402
from ..simulator import FeedSimulator, SimulatorConfig  # noqa: E402
from ..storage import Store  # noqa: E402
from .._version import __version__  # noqa: E402
from ..term import (  # noqa: E402
    Console,
    Table,
    Panel,
    box,
    format_status,
    format_latency,
    format_rate,
    render_brand_header,
    render_step_start,
    render_step_success,
    render_summary_card,
    render_gemini_tips,
)

__stability__ = "beta"


def _config_from_args(args) -> SimulatorConfig:
    cfg = SimulatorConfig()
    for f in fields(SimulatorConfig):
        val = getattr(args, f.name, None)
        if val is not None:
            setattr(cfg, f.name, val)
    return cfg


def _load_or_fetch_depth_events(
    db_path: str, store, canonical_sym: str, console
) -> list:
    """Load depth events from local store or fetch live snapshot from venues."""
    events = []
    if os.path.exists(db_path):
        stored_rows = store.query_events(instrument_id=canonical_sym, limit=100)
        from ..models import CanonicalEvent, EventType, QualityStatus

        for r in stored_rows:
            if r.get("bid_price") or r.get("price"):
                events.append(
                    CanonicalEvent(
                        event_id=r["event_id"],
                        instrument_id=r["instrument_id"],
                        event_type=EventType(r["event_type"]),
                        exchange_timestamp=r["exchange_timestamp"],
                        receive_timestamp=r["receive_timestamp"],
                        processing_timestamp=r["processing_timestamp"],
                        source=r["source"],
                        sequence_number=r["sequence_number"],
                        price=r["price"],
                        quantity=r["quantity"],
                        bid_price=r["bid_price"] or r["price"],
                        bid_size=r["bid_size"] or 100.0,
                        ask_price=r["ask_price"] or r["price"],
                        ask_size=r["ask_size"] or 100.0,
                        quality_status=QualityStatus(r["quality_status"]),
                    )
                )
    if not events:
        from ..live import LiveConnector

        connector = LiveConnector()
        with console.status(
            "[bold cyan]Aggregating live multi-venue depth snapshots...[/bold cyan]"
        ):
            events = connector.fetch_snapshot(canonical_sym)
    return events


def _ensure_db_dir(path: str):
    if path != ":memory:":
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)


def _generate_baseline_candles(
    symbol: str, count: int = 20, interval_s: float = 5.0
) -> list[dict]:
    """Generate realistic baseline historical candles leading up to current time."""
    import random

    s_upper = symbol.upper()
    base_px = None
    vol_mult = 200.0

    if "BTC" in s_upper:
        base_px = 65420.0
        vol_mult = 1.5
    elif "ETH" in s_upper:
        base_px = 3450.0
        vol_mult = 8.0
    elif "SOL" in s_upper:
        base_px = 142.50
        vol_mult = 45.0
    elif "NVDA" in s_upper:
        base_px = 125.0
        vol_mult = 500.0
    elif "MSFT" in s_upper:
        base_px = 445.0
        vol_mult = 200.0
    elif "AAPL" in s_upper:
        base_px = 228.0
        vol_mult = 350.0

    if base_px is None:
        try:
            from ..live import LiveConnector, resolve_venue_symbols

            conn = LiveConnector(timeout=1.5)
            sym_meta = resolve_venue_symbols(symbol)
            if sym_meta["type"] == "EQUITY":
                eq_evs = conn.fetch_equity_events(symbol)
                if eq_evs and eq_evs[0].payload.get("price"):
                    base_px = float(eq_evs[0].payload["price"])
            else:
                snap = conn.fetch_snapshot(symbol)
                for ev in snap:
                    p = ev.payload.get("price") or ev.payload.get("bid")
                    if p and float(p) > 0:
                        base_px = float(p)
                        break
        except Exception:
            pass

    if base_px is None or base_px <= 0:
        base_px = 100.0

    vol_mult = max(10.0, min(1000.0, 50000.0 / max(base_px, 1.0)))

    now = time.time()
    candles = []
    curr = base_px
    rng = random.Random(hash(symbol) % 10000)

    for i in range(count, 0, -1):
        b_start = float(int((now - i * interval_s) // interval_s) * interval_s)
        pct_chg = rng.uniform(-0.0025, 0.003)
        op = round(curr, 2)
        cl = round(curr * (1.0 + pct_chg), 2)
        hi = round(max(op, cl) + abs(pct_chg * curr) * rng.uniform(0.2, 0.7), 2)
        lo = round(min(op, cl) - abs(pct_chg * curr) * rng.uniform(0.2, 0.7), 2)
        vol = round(vol_mult * rng.uniform(10.0, 50.0), 1)
        curr = cl
        candles.append(
            {
                "instrument_id": symbol,
                "bucket_start": b_start,
                "interval_s": interval_s,
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": vol,
                "event_count": int(rng.uniform(5, 25)),
                "_first_ts": b_start,
                "_last_ts": b_start + interval_s - 0.1,
            }
        )
    return candles


def _t(
    title: str, cols: list, rows: list, border: str = "cyan", show_lines: bool = False
) -> Table:
    t = Table(title=title, border_style=border, show_lines=show_lines)
    for col in cols:
        if isinstance(col, tuple):
            name = col[0]
            just = col[1] if len(col) > 1 and col[1] else "left"
            style = col[2] if len(col) > 2 and col[2] else None
            nw = col[3] if len(col) > 3 and col[3] else False
            t.add_column(name, justify=just, style=style, no_wrap=nw)
        else:
            t.add_column(str(col))
    for r in rows:
        t.add_row(*[str(x) if x is not None else "" for x in r])
    return t


def _parse_timeframe_interval(val: Any) -> float:
    if val is None:
        return 5.0
    if isinstance(val, (int, float)):
        return float(val) if float(val) > 0 else 5.0
    s = str(val).strip().lower()
    try:
        if s.endswith("s"):
            return max(0.1, float(s[:-1]))
        elif s.endswith("m"):
            return max(0.1, float(s[:-1]) * 60.0)
        elif s.endswith("h"):
            return max(0.1, float(s[:-1]) * 3600.0)
        elif s.endswith("d"):
            return max(0.1, float(s[:-1]) * 86400.0)
        return max(0.1, float(s))
    except (ValueError, TypeError):
        return 5.0


def _add_sim_flags(p: argparse.ArgumentParser, default_events: int):
    p.add_argument(
        "-e",
        "--events",
        dest="num_events",
        type=int,
        default=default_events,
        help="Number of simulated events",
    )

    p.add_argument(
        "-s", "--seed", type=int, default=None, help="Random seed for reproducibility"
    )
    p.add_argument("--duplicate-rate", dest="duplicate_rate", type=float, default=None)
    p.add_argument("--missing-rate", dest="missing_rate", type=float, default=None)
    p.add_argument(
        "--out-of-order-rate", dest="out_of_order_rate", type=float, default=None
    )
    p.add_argument("--malformed-rate", dest="malformed_rate", type=float, default=None)
    p.add_argument(
        "--price-anomaly-rate", dest="price_anomaly_rate", type=float, default=None
    )
    p.add_argument(
        "--crossed-quote-rate", dest="crossed_quote_rate", type=float, default=None
    )
    p.add_argument(
        "-m",
        "--market",
        dest="market",
        default="us",
        choices=["us", "nse", "xetra", "tse", "global"],
        help="Market profile (us, nse, xetra, tse, global)",
    )


_PACKAGE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
__all__ = [name for name in globals() if not name.startswith("__")] + [
    "__version__",
    "__stability__",
]
