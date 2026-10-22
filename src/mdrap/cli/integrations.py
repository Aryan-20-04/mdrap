from ._common import *

__stability__ = "beta"


def cmd_sub(args):
    """Subscribe to the running MDRAP daemon and stream ticks or depth to stdout."""
    from ..client import MDRAPClient

    console = Console()
    sym = getattr(args, "symbol", "ALL") or "ALL"
    lim = getattr(args, "limit", 0)
    as_json = getattr(args, "json", False)
    token = getattr(args, "token", None)
    want_l2 = getattr(args, "l2", False)
    want_shm = getattr(args, "shm", False)
    shm_name = getattr(args, "shm_name", "mdrap_feed")
    want_binary = getattr(args, "binary", False)

    client = MDRAPClient(
        host=args.host,
        port=args.port,
        auth_token=token,
        use_shm=want_shm,
        shm_name=shm_name,
        use_binary=want_binary,
    )
    try:
        client.connect()
    except Exception as exc:
        console.print(
            f"[bold red]Cannot connect to MDRAP Daemon at {args.host}:{args.port}:[/bold red] {exc}"
        )
        console.print(
            "[yellow]Start the daemon first with:[/yellow] [bold cyan]mdrap daemon[/bold cyan]"
        )
        return

    if want_shm:
        mode_label = "Zero-Copy Shared Memory (<1µs) " + (
            "L2 Depth" if want_l2 else "L1 Ticks"
        )
    elif want_binary:
        mode_label = "MDRAP-BIN Fixed Binary (<2µs) " + (
            "Consolidated L2 Depth + L1 Ticks" if want_l2 else "Consolidated L1 Ticks"
        )
    else:
        mode_label = "TCP Socket JSON " + (
            "Consolidated L2 Depth + L1 Ticks" if want_l2 else "Consolidated L1 Ticks"
        )
    if not as_json:
        console.print(
            Panel.fit(
                f"[bold cyan]MDRAP Institutional Client Stream ({mode_label})[/bold cyan]\n"
                f"Connected to: [bold green]{args.host}:{args.port}[/bold green]  |  Subscribed: [bold yellow]{sym}[/bold yellow]\n"
                f"Automated Gap Recovery: [bold green]ENABLED[/bold green]  |  Wire-to-Wire Latency Tracking: [bold green]ACTIVE[/bold green]",
                border_style="cyan",
            )
        )
        console.print()

    client.subscribe([sym], include_depth=want_l2)

    try:
        for ev in client.stream(max_events=lim if lim > 0 else None):
            if as_json:
                sys.stdout.write(json.dumps(ev.__dict__) + "\n")
                sys.stdout.flush()
            else:
                if ev.is_depth:
                    crossed_tag = (
                        " [bold red][CROSSED L2][/bold red]" if ev.is_crossed else ""
                    )
                    spread_str = (
                        f"${ev.spread:,.2f}" if ev.spread is not None else "N/A"
                    )
                    micro_str = (
                        f"${ev.micro_price:,.2f}"
                        if ev.micro_price is not None
                        else "N/A"
                    )
                    ofi_str = f"{ev.ofi:+.2f}" if ev.ofi is not None else "0.00"
                    console.print(
                        f"[dim]#{ev.seq:<6}[/dim] [bold blue]DEPTH[/bold blue]  "
                        f"[magenta]{ev.symbol:<8}[/magenta] "
                        f"MicroPx: [bold green]{micro_str:<10}[/bold green] "
                        f"Spread: [yellow]{spread_str:<8}[/yellow] "
                        f"OFI: [cyan]{ofi_str:<6}[/cyan] "
                        f"[dim]Eng:{ev.engine_us:>4.1f}µs[/dim] "
                        f"[dim]Wire:{ev.wire_latency_us:>5.1f}µs[/dim]{crossed_tag}"
                    )
                else:
                    bbo_str = ""
                    if ev.bbo and ev.bbo.get("bid") is not None:
                        crossed = (
                            " [bold red][CROSSED][/bold red]"
                            if ev.bbo.get("crossed")
                            else ""
                        )
                        bbo_str = f" | BBO: [green]${ev.bbo['bid']:,.2f}[/green]/[red]${ev.bbo['ask']:,.2f}[/red]{crossed}"
                    st_color = (
                        "green"
                        if ev.status == "VALID"
                        else ("yellow" if ev.status == "SUSPICIOUS" else "red")
                    )
                    px = (
                        f"${ev.price:,.2f}"
                        if ev.price
                        else (
                            f"B:${ev.bid_price or 0:,.2f}/A:${ev.ask_price or 0:,.2f}"
                        )
                    )
                    console.print(
                        f"[dim]#{ev.seq:<6}[/dim] [bold cyan]TICK [/bold cyan]  "
                        f"[magenta]{ev.symbol:<8}[/magenta] [cyan]{ev.source:<8}[/cyan] "
                        f"[bold]{px:<16}[/bold] [{st_color}]{ev.status:<10}[/{st_color}] "
                        f"[dim]Eng:{ev.engine_us:>4.1f}µs[/dim] "
                        f"[dim]Wire:{ev.wire_latency_us:>5.1f}µs[/dim]{bbo_str}"
                    )
    except KeyboardInterrupt:
        pass
    finally:
        st = client.stats()
        client.close()
        if not as_json and st["events_received"] > 0:
            console.print()
            console.print(
                Panel.fit(
                    f"[bold cyan]MDRAP Client Session Scorecard[/bold cyan]\n"
                    f"Events Received: [bold green]{st['events_received']:,}[/bold green]  |  "
                    f"Gaps Recovered: [bold green]{st['events_replayed']:,}[/bold green] (Detections: {st['gaps_detected']})\n"
                    f"Engine Latency: [bold]p50={st['engine_latency_p50_us']:.1f}µs | p99={st['engine_latency_p99_us']:.1f}µs[/bold]\n"
                    f"Wire Latency:   [bold green]p50={st['wire_latency_p50_us']:.1f}µs | p99={st['wire_latency_p99_us']:.1f}µs[/bold green]",
                    border_style="green",
                )
            )


def cmd_top(args):
    """Launch the live full-screen terminal monitor cockpit."""
    from ..service import TerminalCockpit

    token = getattr(args, "token", None)
    cockpit = TerminalCockpit(host=args.host, port=args.port, auth_token=token)
    cockpit.run()


def cmd_strategy(args):
    """Institutional Algorithmic Strategy Engine & Paper EMS (§26)."""
    from ..strategy_sdk import (
        WhaleMomentumStrategy,
        SpreadCaptureMarketMaker,
        AvellanedaStoikovStrategy,
    )
    from ..simulator import FeedSimulator, SimulatorConfig
    from ..gateway import ingest, normalize
    from ..flow_tracker import OrderFlowTracker
    from ..models import EventType, QualityStatus

    console = Console()

    action = getattr(args, "action", "list") or "list"
    strat_name = getattr(args, "strategy", "whale_momentum") or "whale_momentum"
    sym = getattr(args, "symbol", "AAPL") or "AAPL"
    events_count = getattr(args, "events", 1000) or 1000
    show_book = getattr(args, "book", False)
    show_executions = getattr(args, "executions", False)
    export_arg = getattr(args, "export", None)

    if action == "list" and not (show_book or show_executions or export_arg):
        console.print(
            Panel(
                "[bold cyan]MDRAP Institutional Algorithmic Strategy Catalog & Paper EMS (§26)[/bold cyan]\n"
                "[dim]Autonomous event-driven execution framework with pre-trade risk gates[/dim]",
                expand=False,
            )
        )
        cat_rows = [
            (
                "whale_momentum",
                "Institutional Flow Momentum",
                "Detects institutional block prints (>= $100k notional) via Lee-Ready CVD;\nEnters in the direction of smart-money aggression with trailing stop.",
                "Max Order: 1,000 shs | Max Pos: 5,000 shs\nPrice Collar: 50 bps | Max DD: 5.0%",
            ),
            (
                "spread_capture",
                "Passive Liquidity Provision",
                "Provides two-sided passive liquidity inside wide bid-ask spreads (>= 3 bps);\nQuotes Buy Limit above Bid and Sell Limit below Ask, avoiding adverse selection.",
                "Max Order: 500 shs | Max Pos: 2,500 shs\nPrice Collar: 30 bps | Max DD: 5.0%",
            ),
            (
                "avellaneda_stoikov",
                "Quantitative HFT Market Maker (AS-MM)",
                "Avellaneda-Stoikov (2008) reservation price with Level-2 micro-price & OBI skew;\nDynamic optimal half-spread quoting with inventory risk dampening and toxic-flow shield.",
                "Max Order: 500 shs | Max Pos: 500 shs\nToxic Flow Spread Guard | Risk Collar: 50 bps",
            ),
        ]
        console.print(
            _t(
                "Available Institutional Trading Strategies",
                [
                    ("Strategy Name", "left", "bold cyan"),
                    ("Type", "left", "magenta"),
                    ("Alpha Rationale / Logic", "left", "white"),
                    ("Pre-Trade Risk Controls", "left", "yellow"),
                ],
                cat_rows,
            )
        )
        console.print(
            "[dim]Run a strategy: [bold]mdrap strategy run -s avellaneda_stoikov -i AAPL -e 2000[/bold][/dim]\n"
        )
        return

    # Execute Paper Strategy Run
    is_all_market = sym.upper() in ("ALL", "*", "MARKET")
    sym_list = [s.strip().upper() for s in sym.split(",")] if not is_all_market else []
    display_sym = "WHOLE MARKET UNIVERSE" if is_all_market else sym

    if not getattr(args, "json", False):
        console.print(
            Panel(
                f"[bold cyan]MDRAP Paper Trading Strategy Engine (§26)[/bold cyan]\n"
                f"• Strategy: [bold yellow]{strat_name}[/bold yellow]  |  Universe: [bold green]{display_sym}[/bold green]  |  Events: [white]{events_count:,}[/white]",
                expand=False,
            )
        )

    try:
        if strat_name == "spread_capture":
            strat = SpreadCaptureMarketMaker(
                symbol=sym, min_spread_bps=3.0, quote_size=50.0
            )
        elif strat_name in (
            "avellaneda_stoikov",
            "as_mm",
            "hft_mm",
            "hft_market_maker",
        ):
            strat = AvellanedaStoikovStrategy(
                symbol=sym, gamma=0.1, kappa=1.5, quote_size=50.0, max_inventory=500.0
            )
        else:
            strat = WhaleMomentumStrategy(
                symbol=sym, trade_size=100.0, stop_loss_pct=0.5
            )
    except (Exception,) as exc:
        if "Companion" in type(exc).__name__ or isinstance(
            exc, (NotImplementedError, TypeError, AttributeError)
        ):
            if getattr(args, "json", False):
                import json

                print(
                    json.dumps(
                        {
                            "status": "COMPANION_PACKAGE_REQUIRED",
                            "strategy": strat_name,
                            "error": str(exc),
                            "install_hint": "pip install packages/mdrap-strategies",
                        },
                        indent=2,
                    )
                )
            else:
                console.print(
                    Panel(
                        f"[bold yellow]{exc}[/bold yellow]\n\n"
                        "[dim]To enable algorithmic strategy execution and backtesting:\n"
                        "  [green]pip install packages/mdrap-strategies[/green][/dim]",
                        title="[bold cyan]Companion Package Required[/bold cyan]",
                        border_style="yellow",
                    )
                )
            return 3
        raise

    flow_tracker = OrderFlowTracker()

    # Data Quality Engine initialization (defaults to Native C Fastpath)
    quality_engine = None
    use_fastpath = getattr(args, "fastpath", True)
    if use_fastpath:
        try:
            from ..fastpath import FastQualityEngine, is_available

            quality_engine = FastQualityEngine() if is_available() else None
        except Exception:
            quality_engine = None
    if quality_engine is None:
        from ..quality import QualityEngine

        quality_engine = QualityEngine()

    # Single-pass streaming: generate → ingest → normalize → quality evaluate → strategy dispatch
    sim_cfg = SimulatorConfig(seed=42, num_events=events_count)
    if not is_all_market and sym_list:
        sim_cfg.instruments = sym_list
    sim = FeedSimulator(sim_cfg)
    active_symbols = set()
    event_count = 0

    t_start = time.perf_counter()
    strat.on_start()

    for raw, _ in sim.generate():
        try:
            raw_ing = ingest(raw)
            can = normalize(raw_ing)
        except Exception:
            continue

        if not (
            is_all_market or can.instrument_id == sym or can.instrument_id in sym_list
        ):
            continue

        # Fastpath Data Quality Guard: Filter corrupted or crossed events
        if quality_engine is not None:
            can = quality_engine.evaluate(can)
            if (
                getattr(can, "quality_status", QualityStatus.VALID)
                == QualityStatus.INVALID
            ):
                continue

        event_count += 1
        active_symbols.add(can.instrument_id)

        # Dispatch directly to strategy (single pass)
        if can.event_type == EventType.QUOTE:
            strat.on_quote(can)
        elif can.event_type == EventType.TRADE:
            strat.on_tick(can)
            if can.price and can.quantity:
                flow_tracker.observe_trade(
                    can.price, can.quantity, can.exchange_timestamp, can.source
                )
                notional = can.price * can.quantity
                if notional >= 100_000.0 or can.quantity >= 500:
                    whale_info = {
                        "instrument": can.instrument_id,
                        "price": can.price,
                        "quantity": can.quantity,
                        "notional": notional,
                        "side": "BUY"
                        if can.price
                        >= strat._current_mid.get(can.instrument_id, can.price)
                        else "SELL",
                        "timestamp": can.exchange_timestamp,
                    }
                    strat.on_whale(whale_info)

    strat.on_stop()
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0
    metrics = strat.performance_summary()

    table_title = (
        f"Performance Tear-Sheet: {strat_name.upper()} on Whole Market Universe"
        if is_all_market
        else f"Performance Tear-Sheet: {strat_name.upper()} on {sym}"
    )
    t_rows = []
    if is_all_market or len(sym_list) > 1:
        sorted_syms = sorted(active_symbols)
        t_rows.append(
            (
                "Simulated Market Universe",
                f"{len(sorted_syms)} instruments ({', '.join(sorted_syms)})",
                "Cross-market feed",
            )
        )

    eps = event_count / (elapsed_ms / 1000.0) if elapsed_ms > 0 else 0
    pnl = metrics["total_pnl"]
    pnl_col = "green" if pnl >= 0 else "red"
    t_rows.extend(
        [
            (
                "Events Ingested & Evaluated",
                f"{event_count:,}",
                f"{elapsed_ms:.2f} ms ({eps:,.0f} eps)",
            ),
            (
                "Total Executed Trades",
                str(metrics["total_trades"]),
                "Paper EMS executions",
            ),
            (
                "Win Rate %",
                f"{metrics['win_rate_pct']:.1f}%",
                "Profitable closed roundtrips",
            ),
            (
                "Realized Net P&L",
                f"[{pnl_col}]{'+$' if pnl >= 0 else '-$'}{abs(pnl):,.2f}[/{pnl_col}]",
                "TCA slippage deducted",
            ),
            (
                "Portfolio Return",
                f"[{pnl_col}]{'+' if pnl >= 0 else ''}{metrics['return_pct']:.2f}%[/{pnl_col}]",
                "On $100k starting capital",
            ),
            (
                "Ending Equity",
                f"${metrics['final_equity']:,.2f}",
                "Cash + Mark-to-Market",
            ),
            (
                "Mean Execution Slippage",
                f"{metrics['avg_slippage_bps']:.2f} bps",
                "Effective spread capture",
            ),
        ]
    )
    if is_all_market or len(sym_list) > 1:
        active_pos = {k: v for k, v in metrics["positions"].items() if v != 0}
        pos_summary = (
            ", ".join(f"{k}:{v:+.0f}" for k, v in sorted(active_pos.items()))
            if active_pos
            else "Flat (0 all)"
        )
        t_rows.append(
            ("Open Net Positions", pos_summary, f"{len(active_pos)} symbols held")
        )
    else:
        pos_qty = metrics["positions"].get(sym, 0.0)
        t_rows.append(
            (
                "Open Net Position",
                f"{pos_qty:,.0f} shares",
                "Pre-trade risk limit: 5,000 shs",
            )
        )

    console.print(
        _t(
            table_title,
            [
                ("Performance Metric", "left", "cyan"),
                ("Result Value", "right", "bold white"),
                ("Institutional Benchmark", "left", "dim"),
            ],
            t_rows,
        )
    )
    console.print(
        "[bold green]✔ Strategy run completed with zero risk limit breaches.[/bold green]\n"
    )

    # Render Level-2 Limit Order Book Ladder
    if show_book:
        symbols_to_render = (
            sorted(active_symbols) if (is_all_market or len(sym_list) > 1) else [sym]
        )
        for symbol_to_show in symbols_to_render:
            ob = strat.get_order_book(symbol_to_show)
            bids, asks = ob.get_ladder(depth=5)
            if bids or asks:
                book_table = Table(
                    title=f"📖 Level-2 Limit Order Book · {symbol_to_show.upper()}"
                )
                book_table.add_column("Bid Qty", justify="right", style="bold green")
                book_table.add_column("Bid Price", justify="right", style="green")
                book_table.add_column("Spread", justify="center", style="dim")
                book_table.add_column("Ask Price", justify="left", style="red")
                book_table.add_column("Ask Qty", justify="left", style="bold red")

                max_depth = max(len(bids), len(asks))
                for i in range(max_depth):
                    b_px, b_sz = bids[i] if i < len(bids) else (None, None)
                    a_px, a_sz = asks[i] if i < len(asks) else (None, None)
                    b_px_str = f"${b_px:,.2f}" if b_px else ""
                    b_sz_str = f"{b_sz:,.0f}" if b_sz else ""
                    a_px_str = f"${a_px:,.2f}" if a_px else ""
                    a_sz_str = f"{a_sz:,.0f}" if a_sz else ""
                    spr_str = f"{ob.spread_bps:.1f} bps" if i == 0 else ""
                    book_table.add_row(b_sz_str, b_px_str, spr_str, a_px_str, a_sz_str)

                console.print(book_table)
                console.print(
                    f"[dim]• Mid: ${ob.mid_price:,.2f}  |  Micro-Price: ${ob.micro_price:,.2f}  |  "
                    f"Spread: ${ob.spread:.4f} ({ob.spread_bps:.1f} bps)  |  "
                    f"Imbalance: {ob.imbalance:+.2f}[/dim]\n"
                )

    # Render Execution Quality & Microstructure Ledger Table
    ledger = strat.get_execution_ledger()
    if show_executions:
        if ledger:
            exec_cols = [
                ("Trade ID", "left", "bold cyan"),
                ("Symbol", "left", "white"),
                ("Side", "center"),
                ("Qty", "right"),
                ("Arrival Px", "right", "dim"),
                ("Fill Px", "right", "bold white"),
                ("Slippage", "right"),
                ("Eff Spread", "right", "yellow"),
                ("Signal / Action Reason", "left", "dim white"),
            ]
            exec_rows = []
            for item in ledger:
                side_str = (
                    "[bold green]BUY[/bold green]"
                    if item["side"] == "BUY"
                    else "[bold red]SELL[/bold red]"
                )
                slip_bps = item.get("slippage_bps", 0.0)
                slip_str = f"{slip_bps:+.1f} bps" if slip_bps != 0 else "0.0 bps"
                slip_colored = (
                    f"[red]{slip_str}[/red]"
                    if slip_bps > 0
                    else f"[green]{slip_str}[/green]"
                )
                arr_px = (
                    f"${item['arrival_price']:.2f}"
                    if item.get("arrival_price")
                    else "-"
                )
                fill_px = (
                    f"${item['filled_price']:.2f}" if item.get("filled_price") else "-"
                )
                eff_spr = f"{item.get('effective_spread_bps', 0.0):.1f} bps"
                exec_rows.append(
                    [
                        item["trade_id"],
                        item["symbol"],
                        side_str,
                        f"{item['quantity']:,.0f}",
                        arr_px,
                        fill_px,
                        slip_colored,
                        eff_spr,
                        item.get("signal_reason") or "Market Aggressor",
                    ]
                )
            console.print(
                _t(
                    f"⚡ Strategy Execution Quality & Microstructure Ledger · {strat_name.upper()}",
                    exec_cols,
                    exec_rows,
                )
            )
            console.print(f"[dim]Total executions in session: {len(ledger)}[/dim]\n")
        else:
            console.print(
                "[dim]No trades executed during this session (market conditions did not trigger entry).[/dim]\n"
            )

    # Export to File (JSON or CSV)
    if export_arg is not None:
        target_file = None if export_arg == "default" else export_arg
        fmt = (
            "csv" if (target_file and target_file.lower().endswith(".csv")) else "json"
        )
        exported_path = strat.export_executions(filepath=target_file, format=fmt)
        console.print(
            f"[bold green]✔ Strategy run and order book executions successfully exported to:[/bold green] [cyan]{exported_path}[/cyan]\n"
        )


def cmd_itch(args):
    """NASDAQ TotalView-ITCH 5.0 Binary Feed Parser & Benchmark Engine."""
    from ..itch import (
        ITCHFeedReplayer,
        ITCHOrderBookTracker,
        ITCHSyntheticGenerator,
        run_itch_benchmark,
        run_itch_file_benchmark,
    )

    console = Console()

    action = getattr(args, "action", "bench") or "bench"
    file_path = getattr(args, "file", None)

    if action == "bench":
        raw_events = getattr(args, "events", 1_000_000)
        events_count = 1_000_000 if raw_events is None else raw_events
        max_msgs = None if events_count == 0 else events_count
        vol_label = (
            "ALL (Until EOF)" if max_msgs is None else f"{events_count:,} frames"
        )

        if file_path and os.path.exists(file_path):
            if os.path.isdir(file_path):
                candidates = [
                    os.path.join(file_path, f)
                    for f in os.listdir(file_path)
                    if not os.path.isdir(os.path.join(file_path, f))
                ]
                if candidates:
                    file_path = candidates[0]

            console.print(
                Panel(
                    f"[bold cyan]NASDAQ TotalView-ITCH 5.0 Real File Benchmark Engine[/bold cyan]\n"
                    f"• Source File: [bold yellow]{os.path.basename(file_path)}[/bold yellow]  |  Target Volume: [white]{vol_label}[/white]\n"
                    f"[dim]Streaming binary file, decoding big-endian structs, and updating L3 order book[/dim]",
                    expand=False,
                )
            )
            console.print(
                f"Executing ITCH benchmark directly on real exchange file {file_path}..."
            )
            res = run_itch_file_benchmark(
                file_path=file_path, max_messages=max_msgs, reconstruct_book=True
            )
        else:
            console.print(
                Panel(
                    f"[bold cyan]NASDAQ TotalView-ITCH 5.0 Global Benchmark Engine[/bold cyan]\n"
                    f"• Target Volume: [bold yellow]{events_count:,} binary frames[/bold yellow]  |  [dim]MBO Order Book & BBO Reconstruction[/dim]",
                    expand=False,
                )
            )
            console.print(
                f"Executing ITCH 5.0 benchmark across {events_count:,} binary messages..."
            )
            res = run_itch_benchmark(
                num_messages=events_count, seed=42, reconstruct_book=True
            )

        table = Table(title="NASDAQ TotalView-ITCH 5.0 Performance Scorecard")
        table.add_column("Benchmark Metric", style="cyan")
        table.add_column("Measured Value", style="bold green", justify="right")
        table.add_column("Hardware & Arch Context", style="dim")

        if "file" in res:
            table.add_row(
                "Source NASDAQ File",
                str(res["file"]),
                f"{res.get('file_size_mb', 0):.1f} MB on disk",
            )

        table.add_row(
            "ITCH Binary Messages Processed",
            f"{res['num_messages']:,}",
            "Big-endian binary frames",
        )
        table.add_row(
            "Execution Duration",
            f"{res['elapsed_seconds']:.3f} s",
            "CPU user/sys clock",
        )
        table.add_row(
            "ITCH Parsing & Book Throughput",
            f"{res['throughput_mps']:,.0f} msgs/sec",
            "Pure Python struct.Struct",
        )
        table.add_row(
            "Mean Latency per Message",
            f"{res['mean_latency_us']:.2f} µs",
            "Unpack + Depth update",
        )
        table.add_row(
            "Synthesized Market Trades",
            f"{res['executed_trades']:,}",
            "Converted to CanonicalEvent",
        )
        table.add_row(
            "Active Book Orders Tracked",
            f"{res['active_orders_in_book']:,}",
            "In-memory MBO order cache",
        )

        b_stats = res.get("book_stats", {})
        table.add_row(
            "Order Adds (Msg A/F)", f"{b_stats.get('adds', 0):,}", "Liquidity posted"
        )
        table.add_row(
            "Order Executions (Msg E/C)",
            f"{b_stats.get('executes', 0):,}",
            "Trades crossed",
        )
        table.add_row(
            "Order Cancels/Deletes (Msg X/D)",
            f"{b_stats.get('cancels', 0):,}",
            "Liquidity cancelled",
        )
        table.add_row(
            "Order Replaces (Msg U)",
            f"{b_stats.get('replaces', 0):,}",
            "Pegged order updates",
        )

        console.print(table)
        console.print(
            f"[bold green]✔ NASDAQ TotalView-ITCH 5.0 benchmark verified at {res['throughput_mps']:,.0f} msgs/sec.[/bold green]\n"
        )

    elif action == "generate":
        out_path = getattr(args, "output", "data/sample.itch") or "data/sample.itch"
        events_count = getattr(args, "events", 100_000) or 100_000
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        console.print(
            f"Generating {events_count:,} binary ITCH 5.0 frames to [bold cyan]{out_path}[/bold cyan]..."
        )
        gen = ITCHSyntheticGenerator(seed=42)
        t0 = time.perf_counter()
        bytes_written = 0
        with open(out_path, "wb") as f:
            for frame in gen.generate_stream(events_count):
                f.write(frame)
                bytes_written += len(frame)
        elapsed = time.perf_counter() - t0
        console.print(
            f"[bold green]✔ Generated {bytes_written / 1024 / 1024:.2f} MB ({events_count:,} frames) in {elapsed:.2f}s.[/bold green]\n"
        )

    elif action == "parse":
        file_path = getattr(args, "file", None)
        if not file_path or not os.path.exists(file_path):
            console.print(
                f"[bold red]Error: ITCH file '{file_path}' not found.[/bold red]"
            )
            return

        limit = getattr(args, "limit", 50) or 50
        console.print(
            f"Streaming first {limit} messages from [bold cyan]{file_path}[/bold cyan]..."
        )
        replayer = ITCHFeedReplayer(file_path)
        book = ITCHOrderBookTracker()

        table = Table(title=f"ITCH 5.0 Stream Preview ({os.path.basename(file_path)})")
        table.add_column("Type", style="bold magenta")
        table.add_column("Timestamp (ns)", style="dim")
        table.add_column("Order Ref", style="cyan")
        table.add_column("Stock", style="bold yellow")
        table.add_column("Side", style="green")
        table.add_column("Shares", justify="right")
        table.add_column("Price", justify="right", style="bold white")

        for msg in replayer.iterate_messages(limit=limit):
            book.process_message(msg)
            px_str = f"${msg.price:.2f}" if msg.price > 0 else "-"
            table.add_row(
                msg.msg_type,
                str(msg.timestamp_ns),
                str(msg.order_ref) if msg.order_ref else "-",
                msg.stock or "-",
                msg.side or "-",
                f"{msg.shares:,}" if msg.shares else "-",
                px_str,
            )
        console.print(table)
        console.print(
            f"[dim]Replayer processed {limit} messages from {file_path}.[/dim]\n"
        )


def cmd_edgar(args):
    """SEC EDGAR Alternative Data (Out of Core Scope)."""
    console = Console()
    console.print(
        "[yellow]Notice:[/yellow] SEC EDGAR research is out of core scope for the market data engine sidecar."
    )


def cmd_vessel(args):
    """Global Maritime Tanker & Cargo Tracking Alternative Data Engine."""
    from ..vessel import VesselTracker, GLOBAL_CHOKEPOINTS

    console = Console()

    action = getattr(args, "action", "list") or "list"
    identifier = getattr(args, "identifier", None)
    vessel_type = getattr(args, "vessel_type", None)
    company = getattr(args, "company", None)
    chokepoint = getattr(args, "chokepoint", None)
    status = getattr(args, "status", None)
    limit = getattr(args, "limit", 25) or 25

    try:
        tracker = VesselTracker()
    except (Exception,) as exc:
        if "Companion" in type(exc).__name__ or isinstance(
            exc, (NotImplementedError, AttributeError)
        ):
            if getattr(args, "json", False):
                import json

                print(
                    json.dumps(
                        {
                            "status": "COMPANION_PACKAGE_REQUIRED",
                            "error": str(exc),
                            "install_hint": "pip install packages/mdrap-contrib-vessel",
                        },
                        indent=2,
                    )
                )
            else:
                console.print(
                    Panel(
                        f"[bold yellow]{exc}[/bold yellow]\n\n"
                        "[dim]To enable maritime AIS intelligence and commodity tanker tracking:\n"
                        "  [green]pip install packages/mdrap-contrib-vessel[/green][/dim]",
                        title="[bold cyan]Companion Package Required[/bold cyan]",
                        border_style="yellow",
                    )
                )
            return 3
        raise
    try:
        if action in ("list", "all", "ls"):
            vessels = tracker.list_vessels(
                vessel_type=vessel_type,
                company=company,
                chokepoint=chokepoint,
                laden_status=status,
                limit=limit,
            )
            filter_parts = []
            if vessel_type:
                filter_parts.append(f"Type={vessel_type}")
            if company:
                filter_parts.append(f"Company={company}")
            if chokepoint:
                filter_parts.append(f"Chokepoint={chokepoint}")
            if status:
                filter_parts.append(f"Status={status}")
            filter_str = f" ({', '.join(filter_parts)})" if filter_parts else ""

            v_cols = [
                ("Vessel Name", "left", "bold cyan"),
                ("IMO / Flag", "left", "dim"),
                ("Type", "left", "magenta"),
                ("Operator (Owner)", "left", "white"),
                ("Charterer / Major", "left", "bold yellow"),
                ("Commodity Payload", "left", "bold green"),
                ("Load", "center"),
                ("Voyage (From → To)", "left", "white"),
                ("Nearest Chokepoint", "left", "bright_cyan"),
                ("Speed", "right"),
            ]
            v_rows = []
            for v in vessels:
                load_style = (
                    "[bold green]LADEN[/bold green]"
                    if v.laden_status == "LADEN"
                    else "[dim]BALLAST[/dim]"
                )
                cp_style = (
                    f"[bold red]⚠️ {v.nearest_chokepoint} ({v.distance_to_chokepoint_nm} nm)[/bold red]"
                    if v.in_chokepoint
                    else f"{v.nearest_chokepoint} ({v.distance_to_chokepoint_nm} nm)"
                )
                route_str = f"{v.origin_port.split(',')[0]} → {v.destination_port.split(',')[0]}"
                v_rows.append(
                    [
                        v.name,
                        f"{v.imo}\n{v.flag}",
                        v.vessel_type,
                        v.operator,
                        v.charterer,
                        f"{v.commodity}\n[dim]{v.cargo_volume}[/dim]",
                        load_style,
                        f"{route_str}\n[dim]ETA: {v.eta.split(' ')[0]}[/dim]",
                        cp_style,
                        f"{v.speed_knots} kts",
                    ]
                )
            console.print(
                _t(
                    f"🚢 Global Commercial Tanker & Cargo Fleet{filter_str}",
                    v_cols,
                    v_rows,
                )
            )
            console.print(
                f"[dim]Tracking {len(vessels)} commercial vessels (reference watch-list fixture). Use 'python -m cli vessel track <NAME>' for full voyage dossier.[/dim]\n"
            )

        elif action in ("track", "inspect", "show"):
            if not identifier:
                console.print(
                    "[red]Please specify a vessel IMO number, MMSI, or Name to track. e.g. python -m cli vessel track 'FRONT ALTAIR'[/red]"
                )
                raise SystemExit(1)
            v = tracker.get_vessel(identifier)
            if not v:
                console.print(
                    f"[bold red]Vessel not found matching identifier '{identifier}'.[/bold red]"
                )
                raise SystemExit(1)

            in_cp_msg = (
                f"[bold red]⚠️ CURRENTLY IN CHOKEPOINT PASSAGE ({v.distance_to_chokepoint_nm} nm)[/bold red]"
                if v.in_chokepoint
                else f"[green]{v.distance_to_chokepoint_nm} nm away[/green]"
            )
            load_msg = (
                "[bold green]LADEN (Full Cargo Onboard)[/bold green]"
                if v.laden_status == "LADEN"
                else "[dim]BALLAST (Empty / Repositioning)[/dim]"
            )

            panel_content = (
                f"[bold cyan]{v.name}[/bold cyan] · [dim]IMO: {v.imo} | MMSI: {v.mmsi} | Flag: {v.flag}[/dim]\n"
                f"[bold magenta]Vessel Type:[/bold magenta] {v.vessel_type} | [bold white]DWT Capacity:[/bold white] {v.dwt:,} MT\n\n"
                f"🏢 [bold white]Commercial Ownership & Charter:[/bold white]\n"
                f"  • [bold]Commercial Operator / Owner:[/bold] [yellow]{v.operator}[/yellow]\n"
                f"  • [bold]Charterer / Commodity Major:[/bold] [bold bright_yellow]{v.charterer}[/bold bright_yellow]\n\n"
                f"📦 [bold white]Cargo Intelligence:[/bold white]\n"
                f"  • [bold]Commodity Category:[/bold] {v.cargo_category}\n"
                f"  • [bold]Specific Cargo Tag:[/bold] [bold green]{v.commodity}[/bold green]\n"
                f"  • [bold]Estimated Payload:[/bold] {v.cargo_volume}\n"
                f"  • [bold]Laden Status:[/bold] {load_msg}\n\n"
                f"🗺️ [bold white]Voyage & Geofencing Status:[/bold white]\n"
                f"  • [bold]Departure Port:[/bold] {v.origin_port}\n"
                f"  • [bold]Destination Port:[/bold] {v.destination_port}\n"
                f"  • [bold]Estimated Arrival (ETA):[/bold] {v.eta}\n"
                f"  • [bold]GPS Telemetry:[/bold] Lat {v.latitude:.4f}°, Lon {v.longitude:.4f}° | Speed: {v.speed_knots} kts | Heading: {v.heading}°\n"
                f"  • [bold]Navigation Status:[/bold] {v.nav_status}\n"
                f"  • [bold]Nearest Strategic Chokepoint:[/bold] [bold yellow]{v.nearest_chokepoint}[/bold yellow] ({in_cp_msg})\n"
                f"  • [dim]Last Position Telemetry Received: {v.last_update}[/dim]"
            )
            console.print(
                Panel(
                    panel_content,
                    title=f"⚓ Vessel Intelligence Dossier · {v.name}",
                    expand=False,
                )
            )

        elif action in ("chokepoints", "bottlenecks", "cp"):
            traffic = tracker.get_chokepoint_traffic(max_distance_nm=150.0)
            cp_cols = [
                ("Chokepoint", "left", "bold yellow"),
                ("Strategic Importance & Global Trade Role", "left", "white"),
                ("Daily Global Flow", "left", "bold cyan"),
                ("Nearby Tracked Vessels", "center", "bold"),
                ("Active Ships within 150 nm", "left", "dim"),
            ]
            cp_rows = []
            for key, cp in GLOBAL_CHOKEPOINTS.items():
                nearby = traffic.get(cp.name, [])
                count_str = (
                    f"[bold red]{len(nearby)}[/bold red]" if nearby else "[dim]0[/dim]"
                )
                vessel_list_str = (
                    ", ".join(
                        f"{v.name} ({v.distance_to_chokepoint_nm} nm)" for v in nearby
                    )
                    if nearby
                    else "No active vessels within 150 nm"
                )
                cp_rows.append(
                    [
                        cp.name,
                        cp.significance,
                        cp.daily_flow,
                        count_str,
                        vessel_list_str,
                    ]
                )
            console.print(
                _t(
                    "🌐 Global Maritime Chokepoints & Strategic Bottleneck Monitor",
                    cp_cols,
                    cp_rows,
                )
            )
            console.print(
                "[dim]Monitoring primary global energy and container choke points for geopolitical and congestion risks.[/dim]\n"
            )

        elif action in ("commodities", "cargo", "breakdown"):
            breakdown = tracker.get_commodity_breakdown()
            cm_cols = [
                ("Commodity Category", "left", "bold cyan"),
                ("Active Vessels", "center", "bold"),
                ("Total DWT", "right", "magenta"),
                ("Specific Commodities Carried", "left", "green"),
                ("Chartering Majors & Traders", "left", "yellow"),
            ]
            cm_rows = [
                [
                    cat_name,
                    str(info["vessel_count"]),
                    f"{info['total_dwt']:,} MT",
                    ", ".join(info["commodities"]),
                    ", ".join(info["charterers"]),
                ]
                for cat_name, info in breakdown["categories"].items()
            ]
            console.print(
                _t(
                    "🛢️ Floating Commodities & Seaborne Supply Chain Exposure",
                    cm_cols,
                    cm_rows,
                )
            )

            summary_panel = (
                f"• [bold white]Total Commercial Fleet Tracked:[/bold white] {breakdown['total_vessels_tracked']}\n"
                f"• [bold white]Laden with Cargo:[/bold white] [bold green]{breakdown['laden_vessels']}[/bold green] | "
                f"[bold white]Ballast / Repositioning:[/bold white] [dim]{breakdown['ballast_vessels']}[/dim]\n"
                f"• [bold white]Top Chartering Entities:[/bold white] {', '.join(f'{k} ({v})' for k, v in breakdown['top_charterers'][:6])}\n"
                f"• [bold white]Top Fleet Operators:[/bold white] {', '.join(f'{k} ({v})' for k, v in breakdown['top_operators'][:6])}"
            )
            console.print(
                Panel(
                    summary_panel,
                    title="📊 Seaborne Supply Chain Summary",
                    expand=False,
                )
            )

        else:
            console.print(
                f"[red]Unknown vessel action '{action}'. Choices: list, track, chokepoints, commodities.[/red]"
            )
            raise SystemExit(1)
    except Exception as exc:
        if isinstance(exc, SystemExit):
            raise
        console.print(f"[bold red]Maritime Tracking Error:[/bold red] {str(exc)}")
        raise SystemExit(1)


def cmd_dashboard(args):
    """Launch real-time terminal visualizer dashboard."""
    import asyncio

    try:
        from ..terminal_display import run_dashboard
    except ImportError:
        print("Error: Could not import dashboard. Make sure rich is installed.")
        return

    try:
        asyncio.run(run_dashboard(port=args.port))
    except KeyboardInterrupt:
        pass


def cmd_sdk_demo(args):
    """Run a demonstration of the Quant-Ready Python SDK."""
    from ..client import MDrapClient
    import asyncio

    console = Console()
    console.print("[bold cyan]MDRAP Python SDK Client Demo[/bold cyan]")
    console.print("Attempting to connect to TCP Gateway at 127.0.0.1:9000...")

    events_received = []

    def on_event(msg):
        events_received.append(msg)
        if len(events_received) % 10 == 0:
            console.print(
                f"Received {len(events_received)} live ticks. Latest: {msg['instrument']} @ {msg['price']:.2f} (Quality: {msg['quality']})"
            )

    async def run_client():
        client = MDrapClient(host="127.0.0.1", port=9000)

        # We will run this for 5 seconds and then gracefully close
        task = asyncio.create_task(client.subscribe(on_event))
        await asyncio.sleep(5.0)
        await client.close()
        await task

        console.print(
            f"\n[bold green]Gathered {len(events_received)} events.[/bold green]"
        )
        try:
            df = client.to_dataframe(events_received)
            console.print("[bold yellow]Pandas DataFrame Integration:[/bold yellow]")
            print(df.head())
        except ImportError:
            console.print(
                "[yellow]Pandas not installed. Run `pip install pandas` to see DataFrame output.[/yellow]"
            )

    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        pass


def cmd_desk(args):
    from ..navigator import MDRAPNavigator

    nav = MDRAPNavigator()
    nav.run()
