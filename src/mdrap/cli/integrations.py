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
    """Institutional Algorithmic Strategy Engine & Paper EMS (Pruned in Read-Only Infrastructure)."""
    console = Console()

    action = getattr(args, "action", "list") or "list"
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
        return 0

    if getattr(args, "json", False):
        import json

        print(
            json.dumps(
                {
                    "status": "COMPANION_PACKAGE_REQUIRED",
                    "package": "mdrap-strategies",
                    "install_hint": "pip install packages/mdrap-strategies",
                },
                indent=2,
            )
        )
        return 3

    console.print(
        Panel(
            "[bold cyan]MDRAP Paper Trading Strategy Engine (§26)[/bold cyan]\n"
            "[bold yellow]Notice: Algorithmic execution and trading strategies are out of scope (NO DEPLOYMENT governance strictly enforced; Companion Package Required).[/bold yellow]\n\n"
            "[dim]To enable algorithmic execution and trading strategies:\n"
            "  [green]pip install packages/mdrap-strategies[/green][/dim]",
            title="[bold cyan]Companion Package Required[/bold cyan]",
            border_style="yellow",
        )
    )
    return 3


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
    """Global Maritime Tanker & Cargo Tracking Alternative Data Engine (Pruned from Core)."""
    console = Console()
    if getattr(args, "json", False):
        import json

        print(
            json.dumps(
                {
                    "status": "COMPANION_PACKAGE_REQUIRED",
                    "install_hint": "pip install packages/mdrap-contrib-vessel",
                },
                indent=2,
            )
        )
        return 3
    console.print(
        Panel(
            "[bold yellow]Maritime AIS intelligence is out of core scope for the market data engine sidecar.[/bold yellow]\n\n"
            "[dim]To enable maritime AIS intelligence and commodity tanker tracking:\n"
            "  [green]pip install packages/mdrap-contrib-vessel[/green][/dim]",
            title="[bold cyan]Companion Package Required[/bold cyan]",
            border_style="yellow",
        )
    )
    return 3


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
    console = Console()
    console.print(
        "[yellow]Notice:[/yellow] Interactive modal desk has been retired. "
        "Use operational commands: [bold cyan]mdrap status[/bold cyan], [bold cyan]mdrap doctor[/bold cyan], or [bold cyan]mdrap replay[/bold cyan]."
    )
