"""
Trading, Research & Quant Infrastructure CLI Handlers for MDRAP.
Backing commands: backtest, risk, bars, options, news, alert, watchlist, portfolio, corpact, features, schedule.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _t(title: str | None, cols: list[tuple[str, dict] | str], rows: list[list[Any]], border: str = "cyan") -> Table:
    t = Table(title=title, border_style=border) if title else Table(border_style=border)
    for c in cols:
        name, kw = c if isinstance(c, tuple) else (c, {})
        t.add_column(name, **kw)
    for r in rows:
        t.add_row(*[str(x) for x in r])
    return t
def cmd_backtest(args: argparse.Namespace) -> None:
    from backtest import BacktestEngine, load_events_from_store
    from strategy_sdk import WhaleMomentumStrategy, SpreadCaptureMarketMaker

    symbol = getattr(args, "symbol", "AAPL") or "AAPL"
    capital = getattr(args, "capital", 100_000.0)
    db_path = getattr(args, "db", "data/mdrap.db")
    strat_name = getattr(args, "strategy", "whale_momentum")

    console.print(Panel.fit(
        f"[bold cyan]MDRAP Historical Backtesting Engine[/bold cyan]\n"
        f"Strategy: [yellow]{strat_name}[/yellow] | Symbol: [green]{symbol}[/green] | Capital: [bold]${capital:,.2f}[/bold]",
        border_style="cyan"
    ))

    events = []
    if os.path.exists(db_path):
        events = load_events_from_store(db_path, instrument_id=symbol)

    if not events:
        console.print(f"[yellow]No events found in {db_path} for {symbol}. Generating simulated benchmark stream...[/yellow]")
        from models import CanonicalEvent, EventType, QualityStatus
        import random
        rnd = random.Random(42)
        price = 150.0
        t0 = time.time() - 3600
        for i in range(500):
            price += rnd.gauss(0.05, 0.4)
            t = t0 + i * 5.0
            bid = round(price - 0.05, 2)
            ask = round(price + 0.05, 2)
            # Quote event
            events.append(CanonicalEvent(
                event_id=f"sim-q-{i}",
                instrument_id=symbol,
                event_type=EventType.QUOTE,
                exchange_timestamp=t,
                receive_timestamp=t,
                processing_timestamp=t,
                source="SIM",
                sequence_number=i * 2,
                bid_price=bid,
                bid_size=500.0,
                ask_price=ask,
                ask_size=500.0,
                price=round(price, 2),
                quantity=100.0,
                quality_status=QualityStatus.VALID
            ))
            # Trade event (occasionally whale size)
            is_whale = (i % 25 == 0 and i > 0)
            qty = 1500.0 if is_whale else float(rnd.choice([100, 200, 300]))
            events.append(CanonicalEvent(
                event_id=f"sim-t-{i}",
                instrument_id=symbol,
                event_type=EventType.TRADE,
                exchange_timestamp=t + 0.1,
                receive_timestamp=t + 0.1,
                processing_timestamp=t + 0.1,
                source="SIM",
                sequence_number=i * 2 + 1,
                price=round(price, 2),
                quantity=qty,
                quality_status=QualityStatus.VALID
            ))

    strategy = WhaleMomentumStrategy(symbol=symbol) if strat_name == "whale_momentum" else SpreadCaptureMarketMaker(symbol=symbol)
    engine = BacktestEngine(initial_capital=capital)
    res = engine.run(strategy, events)

    console.print(_t("Backtest Performance Summary", [("Metric", {"style": "cyan"}), ("Value", {"style": "bold green"})], [
        ["Total Return", f"{res.total_return_pct:+.2f}%"],
        ["Annualized Return", f"{res.annualized_return_pct:+.2f}%"],
        ["Final Equity", f"${res.final_equity:,.2f}"],
        ["Max Drawdown", f"{res.max_drawdown_pct:.2f}%"],
        ["Sharpe Ratio", f"{res.sharpe_ratio:.2f}"],
        ["Sortino Ratio", f"{res.sortino_ratio:.2f}" if not math.isinf(res.sortino_ratio) else "N/A"],
        ["Calmar Ratio", f"{res.calmar_ratio:.2f}" if not math.isinf(res.calmar_ratio) else "N/A"],
        ["Total Trades", str(res.total_trades)],
        ["Win Rate", f"{res.win_rate:.1f}%"],
        ["Profit Factor", f"{res.profit_factor:.2f}" if not math.isinf(res.profit_factor) else "N/A"],
    ], border="bright_blue"))


# ---------------------------------------------------------------------------
# 2. Portfolio Risk CLI
# ---------------------------------------------------------------------------
def cmd_risk(args: argparse.Namespace) -> None:
    from risk import PortfolioRiskEngine, PositionLimits, DrawdownCircuitBreaker

    conf = getattr(args, "confidence", 0.95)
    capital = getattr(args, "capital", 100_000.0)

    console.print(Panel.fit(
        f"[bold red]Institutional Portfolio Risk & VaR Engine[/bold red]\n"
        f"Confidence: [bold]{conf*100:.1f}%[/bold] | Capital: [bold]${capital:,.2f}[/bold]",
        border_style="red"
    ))

    engine = PortfolioRiskEngine(confidence_level=conf, initial_capital=capital)
    # Feed sample return path
    import random
    rnd = random.Random(42)
    val = capital
    t0 = time.time() - 252 * 86400
    for i in range(252):
        ret = rnd.gauss(0.0005, 0.015)
        val *= (1.0 + ret)
        engine.observe(t0 + i * 86400, val)

    s = engine.summary()
    console.print(_t("Risk & VaR Analytics", [("Metric", {"style": "cyan"}), ("Value", {"style": "bold yellow"})], [
        ["Portfolio Value", f"${s['current_value']:,.2f}"],
        ["Historical VaR (95%)", f"${s['historical_var']:,.2f}"],
        ["Parametric VaR (95%)", f"${s['parametric_var']:,.2f}"],
        ["Monte Carlo VaR (95%)", f"${s['monte_carlo_var']:,.2f}"],
        ["Expected Shortfall (CVaR)", f"${s['expected_shortfall']:,.2f}"],
        ["Max Drawdown", f"{s['max_drawdown']:.2f}%"],
        ["Current Drawdown", f"{s['current_drawdown']:.2f}%"],
        ["Annualized Sharpe", f"{s['sharpe_ratio']:.2f}"],
        ["Annualized Sortino", f"{s['sortino_ratio']:.2f}"],
    ], border="red"))


# ---------------------------------------------------------------------------
# 3. Bar Database CLI
# ---------------------------------------------------------------------------
def cmd_bars(args: argparse.Namespace) -> None:
    from bardb import BarDatabase

    action = getattr(args, "action", "summary") or "summary"
    db_path = getattr(args, "db", "data/bars.db")
    symbol = getattr(args, "symbol", "AAPL")
    interval = getattr(args, "interval", "1m")

    with BarDatabase(db_path) as db:
        if action == "summary":
            s = db.summary()
            console.print(Panel.fit(f"[bold cyan]Bar Database: {db_path}[/bold cyan]"))
            console.print(_t(None, [("Metric", {"style": "cyan"}), ("Value", {"style": "green"})], [
                ["Total Bars", str(s['total_bars'])],
                ["Total Instruments", str(s['total_instruments'])],
                ["Configured Intervals", ", ".join(s['intervals'])],
            ]))
        elif action == "query":
            bars = db.query_bars(symbol, interval=interval, limit=getattr(args, "limit", 15))
            if not bars:
                console.print(f"[yellow]No {interval} bars found for {symbol} in {db_path}.[/yellow]")
                return
            console.print(_t(f"Recent {interval} Bars: {symbol}", [
                ("Timestamp", {"style": "dim"}), ("Open", {"justify": "right"}),
                ("High", {"justify": "right", "style": "green"}), ("Low", {"justify": "right", "style": "red"}),
                ("Close", {"justify": "right"}), ("Volume", {"justify": "right"}), ("VWAP", {"justify": "right", "style": "yellow"})
            ], [[time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(b.bucket_start)), f"{b.open:.2f}", f"{b.high:.2f}",
                 f"{b.low:.2f}", f"{b.close:.2f}", f"{b.volume:,.0f}", f"{b.vwap:.2f}"] for b in bars]))


# ---------------------------------------------------------------------------
# 4. Options Pricing & Greeks CLI
# ---------------------------------------------------------------------------
def cmd_options(args: argparse.Namespace) -> None:
    from options import (
        OptionContract, OptionType, ExerciseStyle,
        price_option, OptionsChain, implied_volatility
    )

    action = getattr(args, "action", "price") or "price"
    spot = getattr(args, "spot", 150.0)
    strike = getattr(args, "strike", 150.0)
    expiry = getattr(args, "expiry", 30.0)
    vol = getattr(args, "vol", 0.25)
    rate = getattr(args, "rate", 0.05)
    opt_type = OptionType.PUT if getattr(args, "type", "call").lower() == "put" else OptionType.CALL

    underlying = getattr(args, "underlying", "AAPL") or "AAPL"

    if action == "price":
        contract = OptionContract(
            underlying=underlying,
            strike=strike,
            expiry_days=expiry,
            option_type=opt_type,
            exercise_style=ExerciseStyle.EUROPEAN
        )
        res = price_option(contract, spot=spot, risk_free_rate=rate, volatility=vol)
        console.print(Panel.fit(
            f"[bold magenta]Black-Scholes-Merton Options Pricing: {underlying}[/bold magenta]\n"
            f"Spot: [green]${spot:.2f}[/green] | Strike: [yellow]${strike:.2f}[/yellow] | Expiry: [cyan]{expiry} days[/cyan] | Vol: [bold]{vol*100:.1f}%[/bold]",
            border_style="magenta"
        ))
        console.print(_t(None, [("Property", {"style": "cyan"}), ("Value", {"style": "bold green"})], [
            ["Theoretical Price", f"${res.theoretical:.4f}"], ["Intrinsic Value", f"${res.intrinsic:.4f}"],
            ["Time Value", f"${res.time_value:.4f}"], ["Delta (dV/dS)", f"{res.greeks.delta:+.4f}"],
            ["Gamma (d²V/dS²)", f"{res.greeks.gamma:+.4f}"], ["Theta (per day)", f"${res.greeks.theta:+.4f}"],
            ["Vega (per 1% vol)", f"${res.greeks.vega:+.4f}"], ["Rho (per 1% rate)", f"${res.greeks.rho:+.4f}"],
            ["Vanna", f"{res.greeks.vanna:+.4f}"], ["Volga", f"{res.greeks.volga:+.4f}"],
        ], border="magenta"))
    elif action == "chain":
        chain = OptionsChain(underlying=underlying, spot=spot, risk_free_rate=rate)
        strikes = [round(spot * factor, 1) for factor in [0.90, 0.95, 1.0, 1.05, 1.10]]
        chain.add_expiry(expiry_days=expiry, strikes=strikes, volatility=vol)
        console.print(_t(f"Options Chain (Expiry: {expiry}d, Spot: ${spot:.2f})", [
            ("Type", {"style": "bold"}), ("Strike", {"justify": "right"}), ("Price", {"justify": "right", "style": "green"}),
            ("Delta", {"justify": "right"}), ("Gamma", {"justify": "right"}), ("Vega", {"justify": "right"}), ("Theta", {"justify": "right"})
        ], [[c['option_type'], f"${c['strike']:.2f}", f"${c['price']:.2f}", f"{c['delta']:+.3f}", f"{c['gamma']:.3f}", f"{c['vega']:.3f}", f"{c['theta']:.3f}"]
            for c in chain.chain()], border="magenta"))


# ---------------------------------------------------------------------------
# 5. News & Sentiment CLI
# ---------------------------------------------------------------------------
def cmd_news(args: argparse.Namespace) -> None:
    import urllib.request
    from news import FinancialSentimentAnalyzer, TickerExtractor, NewsFeed, NewsItem, SentimentScore

    action = getattr(args, "action", "latest") or "latest"
    symbol = getattr(args, "symbol", None)
    limit = getattr(args, "limit", 10) or 10
    analyzer = FinancialSentimentAnalyzer()

    # Handle case where user provided symbol positionally or action was an unknown string
    if action not in ("latest", "analyze", "summary", "fetch"):
        if not symbol and action.isalpha() and len(action) <= 8:
            symbol = action.upper()
            action = "latest"
        else:
            action = "analyze"

    feed = NewsFeed()

    def _populate_feed(target_sym: str | None = None) -> None:
        fetched = False
        fetch_err = None
        sym_query = target_sym if target_sym else "AAPL,MSFT,NVDA,TSLA"
        try:
            url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym_query}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                xml_data = resp.read().decode("utf-8", errors="replace")
                items = feed.parse_rss_xml(xml_data, source="YahooFinance")
                if items:
                    fetched = True
        except Exception as exc:
            fetch_err = str(exc)

        if not fetched and target_sym:
            try:
                from research import EdgarClient
                client = EdgarClient()
                sec_events = client.get_material_events(target_sym, limit=limit)
                for ev in sec_events:
                    desc = getattr(ev, "description", "") or (", ".join(getattr(ev, "decoded_items", [])) or "SEC 8-K Event")
                    feed.add_headline(f"{target_sym}: {desc}", source="SEC_8K", url=getattr(ev, "filing_url", ""))
                if sec_events:
                    fetched = True
            except Exception as exc:
                if not fetch_err:
                    fetch_err = str(exc)

        if not feed._items:
            if fetch_err:
                console.print(f"[dim yellow][Notice] Live news retrieval unavailable ({fetch_err}). Showing offline baseline feed.[/dim yellow]")
            now = time.time()
            seed_headlines = [
                ("NVIDIA announces next-generation Blackwell Ultra architecture with record energy efficiency", "Reuters", now - 300, ["NVDA"]),
                ("NVIDIA data center revenue surges 142% year-over-year beating analyst expectations", "Bloomberg", now - 1800, ["NVDA"]),
                ("Apple expands enterprise AI partnership and raises quarterly share repurchase program", "WSJ", now - 3600, ["AAPL"]),
                ("Microsoft cloud Azure gross margin expands amid surging enterprise AI workload adoption", "FT", now - 5400, ["MSFT"]),
                ("Tesla robotaxi commercial deployment clears regulatory milestone in key state markets", "CNBC", now - 7200, ["TSLA"]),
                ("NVIDIA faces minor supply chain bottleneck for advanced CoWoS packaging capacity", "TechCrunch", now - 10800, ["NVDA"]),
                ("Federal Reserve signals benchmark interest rate cut citing easing core inflation data", "Bloomberg", now - 14400, ["SPY"]),
                ("Major semiconductor index rallies as global AI accelerator demand accelerates", "Reuters", now - 18000, ["NVDA", "AMD"]),
                ("NVIDIA partners with global telecommunications giants on 6G AI-RAN infrastructure", "PR_Newswire", now - 21600, ["NVDA"]),
                ("Securities and Exchange Commission approves new institutional market microstructure transparency rule", "SEC", now - 28800, ["SPY"])
            ]
            for hl, src, ts, syms in seed_headlines:
                item = feed.add_headline(hl, source=src, timestamp=ts)
                item.symbols = syms

    if action in ("latest", "fetch"):
        _populate_feed(symbol)
        items = feed.query(symbol=symbol, limit=limit)
        if not items and symbol:
            items = feed.query(limit=limit)

        title = f"Latest Financial News & Sentiment · {symbol.upper() if symbol else 'Market Desk'}"
        columns = [
            ("Date / Time", {"style": "dim"}),
            ("Source", {"style": "cyan"}),
            ("Symbols", {"style": "bold yellow"}),
            ("Sentiment", {"justify": "center"}),
            ("Score", {"justify": "right"}),
            ("Urgency", {"style": "dim"}),
            ("Headline", {"style": "white"}),
        ]
        rows = []
        for it in items:
            color = "green" if "BULLISH" in it.sentiment.value else ("red" if "BEARISH" in it.sentiment.value else "yellow")
            sent_str = f"[{color}]{it.sentiment.value}[/{color}]"
            score_str = f"{it.sentiment_score:+.2f}"
            t_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.timestamp))
            syms_str = ", ".join(it.symbols) if it.symbols else (symbol.upper() if symbol else "-")
            urgency_str = f"[bold red]{it.urgency}[/bold red]" if it.urgency in ("HIGH", "CRITICAL") else it.urgency
            rows.append([t_str, it.source, syms_str, sent_str, score_str, urgency_str, it.headline])

        console.print(_t(title, columns, rows, border="blue"))
        if getattr(args, "json", False):
            import json
            print(json.dumps([{"headline": i.headline, "sentiment": i.sentiment.value, "score": i.sentiment_score, "symbols": i.symbols, "timestamp": i.timestamp} for i in items], indent=2))
        return

    elif action == "summary":
        _populate_feed(symbol)
        summary = feed.sentiment_summary(symbol=symbol)
        sym_label = symbol.upper() if symbol else "Market Universe"
        console.print(Panel.fit(f"[bold]Sentiment Summary for {sym_label}[/bold]"))
        avg_score = summary["avg_score"]
        consensus = "BULLISH" if avg_score > 0.15 else ("BEARISH" if avg_score < -0.15 else "NEUTRAL")
        cons_color = "green" if consensus == "BULLISH" else ("red" if consensus == "BEARISH" else "yellow")
        console.print(_t(None, [("Metric", {"style": "cyan"}), ("Value", {"style": "bold"})], [
            ["Total Articles", str(summary["total"])],
            ["Bullish Articles", f"[green]{summary['bullish']}[/green]"],
            ["Bearish Articles", f"[red]{summary['bearish']}[/red]"],
            ["Neutral Articles", f"[yellow]{summary['neutral']}[/yellow]"],
            ["Average Score", f"{avg_score:+.2f}"],
            ["Consensus", f"[{cons_color}]{consensus}[/{cons_color}]"],
        ], border="magenta"))
        return

    elif action == "analyze":
        text = getattr(args, "text", "") or "Apple beats Q4 revenue expectations, raises dividend and buyback program"
        sentiment, score, urgency, kw = analyzer.analyze(text)
        extractor = TickerExtractor()
        tickers = extractor.extract(text)

        console.print(Panel.fit(f"[bold]News Headline:[/bold] [italic]{text}[/italic]"))
        color = "green" if "BULLISH" in sentiment.value else ("red" if "BEARISH" in sentiment.value else "yellow")
        console.print(_t(None, [("Field", {"style": "cyan"}), ("Value", {"style": "bold"})], [
            ["Sentiment", f"[{color}]{sentiment.value}[/{color}]"],
            ["Sentiment Score", f"{score:+.2f}"],
            ["Urgency", urgency],
            ["Keywords", ", ".join(kw) if kw else "None"],
            ["Extracted Tickers", ", ".join(tickers) if tickers else "None"],
        ], border="blue"))
        return


# ---------------------------------------------------------------------------
# 6. Alerting CLI
# ---------------------------------------------------------------------------
def cmd_alert(args: argparse.Namespace) -> None:
    from alerts import AlertEngine

    action = getattr(args, "action", "list") or "list"
    engine = AlertEngine(db_path=getattr(args, "db", "data/alerts.db"))

    if action == "add":
        symbol, atype, target = getattr(args, "symbol", "AAPL"), getattr(args, "type", "ABOVE"), getattr(args, "target", 200.0)
        a = engine.add_price_alert(symbol, atype, target)
        console.print(f"[bold green]Alert created:[/bold green] ID={a.alert_id} for {symbol} {atype} {target}")
    elif action == "list":
        console.print(_t("Configured Trading Alerts", [
            ("ID", {"style": "dim"}), ("Symbol", {"style": "bold cyan"}), ("Type", {"style": "yellow"}),
            ("Target", {"justify": "right"}), ("Status", {"style": "green"})
        ], [[str(a.alert_id), a.symbol, a.alert_type.value, f"{a.threshold:.2f}", a.status.value] for a in engine.list_alerts()], border="yellow"))
    elif action == "summary":
        console.print(engine.summary())


# ---------------------------------------------------------------------------
# 7. Watchlist & Portfolio CLI
# ---------------------------------------------------------------------------
def cmd_watchlist(args: argparse.Namespace) -> None:
    from portfolio import WatchlistManager

    wm = WatchlistManager(db_path=getattr(args, "db", "data/portfolio.db"))
    action = getattr(args, "action", "list") or "list"

    if action == "list":
        console.print(_t("Watchlists", [("Name", {"style": "bold cyan"}), ("Symbols", {"style": "green"}), ("Description", {"style": "dim"})],
            [[w.name, ", ".join(w.symbols), w.description] for w in wm.list_all()], border="green"))
    elif action == "add":
        name, symbols = getattr(args, "name", "Default"), getattr(args, "symbols", [])
        wm.add_symbols(name, symbols)
        console.print(f"[green]Added {symbols} to watchlist '{name}'[/green]")


def cmd_portfolio(args: argparse.Namespace) -> None:
    from portfolio import PortfolioTracker
    from venues import format_currency
    from fx import convert_currency

    target_curr = getattr(args, "currency", "USD") or "USD"
    tracker = PortfolioTracker(initial_cash=getattr(args, "capital", 100_000.0), db_path=getattr(args, "db", "data/portfolio.db"))
    if (getattr(args, "action", "summary") or "summary") == "summary":
        s = tracker.summary()
        tot_eq = tracker.total_equity_in(target_curr)
        c_val = convert_currency(s['cash'], "USD", target_curr)
        mv_val = convert_currency(s['market_value'], "USD", target_curr)
        r_pnl = convert_currency(s['realized_pnl'], "USD", target_curr)
        ur_pnl = convert_currency(s['unrealized_pnl'], "USD", target_curr)

        console.print(_t(f"Portfolio Overview · {target_curr.upper()}", [("Metric", {"style": "cyan"}), ("Value", {"style": "bold green"})], [
            ["Total Equity", format_currency(tot_eq, target_curr)],
            ["Cash", format_currency(c_val, target_curr)],
            ["Market Value", format_currency(mv_val, target_curr)],
            ["Realized P&L", format_currency(r_pnl, target_curr)],
            ["Unrealized P&L", format_currency(ur_pnl, target_curr)],
            ["Active Positions", str(s['position_count'])],
        ]))


# ---------------------------------------------------------------------------
# 8. Corporate Actions CLI
# ---------------------------------------------------------------------------
def cmd_corpact(args: argparse.Namespace) -> None:
    from corporate_actions import CorporateActionsEngine, ActionType

    db_path = getattr(args, "db", "data/corpact.db")
    engine = CorporateActionsEngine(db_path=db_path)
    symbol = getattr(args, "symbol", "AAPL") or "AAPL"
    actions = engine.actions_for(symbol)

    if not actions:
        if symbol == "AAPL":
            engine.add_split("AAPL", "2020-08-31", 4.0, "4-for-1 forward stock split")
            engine.add_split("AAPL", "2014-06-09", 7.0, "7-for-1 forward stock split")
            engine.add_dividend("AAPL", "2024-02-09", 0.24, "Quarterly cash dividend")
            actions = engine.actions_for(symbol)
        elif symbol == "TSLA":
            engine.add_split("TSLA", "2022-08-25", 3.0, "3-for-1 stock split")
            actions = engine.actions_for(symbol)

    rows = []
    for a in actions:
        val = f"{a.ratio:.1f}:1" if a.action_type in (ActionType.SPLIT, ActionType.REVERSE_SPLIT) else (
            f"${a.amount:.2f}" if a.action_type == ActionType.CASH_DIVIDEND else (a.new_symbol or "-")
        )
        rows.append([a.effective_date, a.action_type.value, val, a.description])
    console.print(_t(f"Corporate Actions for {symbol}", [
        ("Date", {"style": "dim"}), ("Type", {"style": "bold cyan"}), ("Ratio / Amount", {"justify": "right"}), ("Description", {"style": "italic"})
    ], rows, border="blue"))


# ---------------------------------------------------------------------------
# 9. ML Feature Store CLI
# ---------------------------------------------------------------------------
def cmd_features(args: argparse.Namespace) -> None:
    from features import FeatureStore

    if (getattr(args, "action", "list") or "list") == "list":
        console.print(_t("Registered ML & Quantitative Features", [("Feature Name", {"style": "bold cyan"}), ("Description", {"style": "dim"})],
            [[f['name'], f['description']] for f in FeatureStore().registry.list_all()]))


# ---------------------------------------------------------------------------
# 10. Scheduler CLI
# ---------------------------------------------------------------------------
def cmd_schedule(args: argparse.Namespace) -> None:
    from scheduler import Scheduler

    s = Scheduler()
    action = getattr(args, "action", "list") or "list"

    if action == "list":
        s.add_job("EOD Snapshot", "@eod", lambda: "Snapshot completed", job_id="eod_snap")
        s.add_job("Hourly Rollup", "@hourly", lambda: "Rollup completed", job_id="hr_rollup")
        console.print(_t("Scheduled Platform Jobs", [
            ("Job ID", {"style": "dim"}), ("Name", {"style": "bold cyan"}), ("Schedule", {"style": "yellow"}), ("Next Run", {"style": "green"})
        ], [[j.job_id, j.name, j.cron_expr, time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(j.next_run))] for j in s.list_jobs()], border="magenta"))
    elif action == "eod":
        console.print(Panel.fit("[bold green]Generated EOD Report[/bold green]"))
        console.print(json.dumps(s.generate_eod_report(), indent=2))


# ---------------------------------------------------------------------------
# 11. Global Markets & Venues CLI
# ---------------------------------------------------------------------------
def cmd_markets(args: argparse.Namespace) -> None:
    """Displays real-time multi-market clocks, trading session phases, and FX matrix."""
    import datetime
    from venues import GLOBAL_VENUES, get_session_phase
    from fx import GLOBAL_FX

    table = Table(title="🌐 Global Financial Markets & Exchange Microstructure")
    table.add_column("Market / Venue", style="bold white")
    table.add_column("MIC", style="dim", justify="center")
    table.add_column("Local Time", style="bold cyan", justify="center")
    table.add_column("Session Status", justify="center")
    table.add_column("Base Currency", style="bold yellow", justify="center")
    table.add_column("Benchmark Index", style="bold green")
    table.add_column("Circuit Band", style="dim", justify="right")

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    for mic, v in GLOBAL_VENUES.items():
        is_open, phase, desc = get_session_phase(v)
        offset_hrs = v.utc_offset_hours
        local_dt = now_utc + datetime.timedelta(hours=offset_hrs)
        local_time_str = local_dt.strftime("%H:%M %a")

        if is_open:
            status_str = f"[bold green]● {desc}[/bold green]"
        elif phase.value == "PRE_OPEN":
            status_str = f"[bold yellow]◐ {desc}[/bold yellow]"
        else:
            status_str = f"[dim red]○ {desc}[/dim red]"

        table.add_row(
            f"{v.flag} {v.name}",
            v.mic,
            local_time_str,
            status_str,
            f"{v.currency} ({v.currency_symbol})",
            f"{v.benchmark_index} ({v.index_name})",
            f"±{v.circuit_limit_pct:.0f}%",
        )

    console.print(table)
    console.print("\n[bold dim]Institutional FX Matrix Snapshot (vs USD):[/bold dim]")
    fx_summary = GLOBAL_FX.rates_summary()
    fx_parts = [f"USD/{k}: [bold cyan]{v:,.2f}[/bold cyan]" for k, v in fx_summary.items() if k != "USD"]
    console.print("  " + "  •  ".join(fx_parts[:6]))
    console.print()


# ---------------------------------------------------------------------------
# Subparser Registration Helper
# ---------------------------------------------------------------------------
def add_trading_parsers(sub) -> None:
    """Register all trading/research subparsers in argparse."""

    # Backtest
    p_bt = sub.add_parser("backtest", aliases=["bt"], help="Historical backtesting engine with point-in-time event replay")
    p_bt.add_argument("-s", "--strategy", default="whale_momentum", choices=["whale_momentum", "spread_capture"])
    p_bt.add_argument("-i", "--symbol", default="AAPL")
    p_bt.add_argument("-c", "--capital", type=float, default=100_000.0)
    p_bt.add_argument("--db", default="data/mdrap.db")
    p_bt.set_defaults(func=cmd_backtest)

    # Risk
    p_risk = sub.add_parser("risk", aliases=["var", "cvar"], help="Portfolio risk management, VaR, CVaR, and correlation analysis")
    p_risk.add_argument("-c", "--capital", type=float, default=100_000.0)
    p_risk.add_argument("-w", "--window", type=int, default=252)
    p_risk.add_argument("-p", "--confidence", type=float, default=0.95)
    p_risk.add_argument("--db", default="data/mdrap.db")
    p_risk.set_defaults(func=cmd_risk)

    # Bar Database
    p_bars = sub.add_parser("bars", aliases=["bardb", "candles"], help="Multi-timeframe OHLCV bar database and historical lookup")
    p_bars.add_argument("action", nargs="?", default="summary", choices=["summary", "query", "instruments"])
    p_bars.add_argument("-i", "-s", "--symbol", default="AAPL")
    p_bars.add_argument("-t", "--interval", default="1m")
    p_bars.add_argument("-l", "--limit", type=int, default=10)
    p_bars.add_argument("--db", default="data/bars.db")
    p_bars.set_defaults(func=cmd_bars)

    # Options
    p_opt = sub.add_parser("options", aliases=["opt", "greeks"], help="Options pricing models, Greeks chain, and implied volatility solver")
    p_opt.add_argument("action", nargs="?", default="price", choices=["price", "chain", "surface"])
    p_opt.add_argument("-s", "--spot", type=float, default=100.0)
    p_opt.add_argument("-k", "--strike", type=float, default=100.0)
    p_opt.add_argument("-e", "--expiry", type=float, default=30.0)
    p_opt.add_argument("-v", "--vol", type=float, default=0.25)
    p_opt.add_argument("-r", "--rate", type=float, default=0.05)
    p_opt.add_argument("-t", "--type", default="call", choices=["call", "put"])
    p_opt.set_defaults(func=cmd_options)

    # News
    p_news = sub.add_parser("news", aliases=["sentiment"], help="Financial news aggregation, sentiment analysis, and entity extraction")
    p_news.add_argument("action", nargs="?", default="latest", choices=["latest", "analyze", "summary", "fetch"])
    p_news.add_argument("text", nargs="?", default="", help="Headline text to analyze (when action is analyze)")
    p_news.add_argument("-s", "--symbol", default=None, help="Stock or crypto ticker symbol (e.g. NVDA, AAPL, BTC)")
    p_news.add_argument("-l", "--limit", type=int, default=10, help="Maximum number of headlines to display")
    p_news.add_argument("--feed", default=None, help="Custom RSS feed URL or source identifier")
    p_news.add_argument("-j", "--json", action="store_true", help="Output results in JSON format")
    p_news.set_defaults(func=cmd_news)

    # Alert
    p_alert = sub.add_parser("alert", aliases=["alerts"], help="Real-time alert engine (price, spread, volume, drawdown)")
    p_alert.add_argument("action", nargs="?", default="list", choices=["list", "add", "summary"])
    p_alert.add_argument("-i", "--symbol", default="AAPL")
    p_alert.add_argument("-t", "--type", default="ABOVE", choices=["ABOVE", "BELOW"])
    p_alert.add_argument("-v", "--target", type=float, default=200.0)
    p_alert.add_argument("--db", default="data/alerts.db")
    p_alert.set_defaults(func=cmd_alert)

    # Watchlist
    p_wl = sub.add_parser("watchlist", aliases=["wl"], help="Named watchlist management")
    p_wl.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    p_wl.add_argument("-n", "--name", default="Tech")
    p_wl.add_argument("-s", "--symbols", nargs="*", default=["AAPL", "MSFT"])
    p_wl.add_argument("--db", default="data/portfolio.db")
    p_wl.set_defaults(func=cmd_watchlist)

    # Portfolio
    p_port = sub.add_parser("portfolio", aliases=["port"], help="Portfolio tracker with P&L attribution and benchmark comparison")
    p_port.add_argument("action", nargs="?", default="summary", choices=["summary"])
    p_port.add_argument("-c", "--capital", type=float, default=100_000.0)
    p_port.add_argument("--currency", default="USD", help="Base reporting currency (e.g. USD, INR, EUR, JPY)")
    p_port.add_argument("--db", default="data/portfolio.db")
    p_port.set_defaults(func=cmd_portfolio)

    # Corporate Actions
    p_ca = sub.add_parser("corpact", aliases=["splits", "dividends"], help="Corporate actions processor: splits, dividends, ticker changes")
    p_ca.add_argument("action", nargs="?", default="list", choices=["list", "add", "adjust"])
    p_ca.add_argument("-i", "-s", "--symbol", default="AAPL")
    p_ca.set_defaults(func=cmd_corpact)

    # ML Features
    p_feat = sub.add_parser("features", aliases=["feat"], help="ML feature store: technical indicators and microstructure metrics")
    p_feat.add_argument("action", nargs="?", default="list", choices=["list"])
    p_feat.add_argument("-i", "-s", "--symbol", default="AAPL")
    p_feat.set_defaults(func=cmd_features)

    # Schedule
    p_sched = sub.add_parser("schedule", aliases=["sched", "cron"], help="Scheduled tasks and automated EOD reports")
    p_sched.add_argument("action", nargs="?", default="list", choices=["list", "eod"])
    p_sched.set_defaults(func=cmd_schedule)

    # Markets
    p_mkts = sub.add_parser("markets", aliases=["venues", "world"], help="Global financial exchange directory, market clocks, and microstructure")
    p_mkts.set_defaults(func=cmd_markets)
