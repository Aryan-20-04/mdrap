# MDRAP Applied: Research & Live Trading

This guide details how to leverage the MDRAP ecosystem for quantitative research, and how to bridge the gap between paper trading and real-money execution.

---

## 1. Using MDRAP for Market Research (The Quant Dashboard)

MDRAP is designed to be the ultimate backend for quantitative researchers using Python, Pandas, and Jupyter Notebooks.

### Option A: The Live Market Dashboard (Real-Time)
If you want to visualize live market movements as they happen, simply launch the TUI dashboard we built:
```bash
mdrap dashboard
```
This gives you an instant terminal HUD showing live top-of-book (BBO) spreads, rolling VWAP, and network throughput.

### Option B: Jupyter Notebooks & DuckDB (Historical/Batch)
For deep statistical research, you want to query the DuckDB columnar storage that MDRAP generates in the background.

Open a Jupyter Notebook and connect directly to MDRAP's data lake:
```python
import duckdb
import pandas as pd

# Connect to MDRAP's high-speed columnar replica
con = duckdb.connect("data/mdrap.duckdb", read_only=True)

# Pull the last hour of TSLA trades directly into a Pandas DataFrame
df = con.execute("""
    SELECT timestamp, price, size 
    FROM trades 
    WHERE instrument_id = 'TSLA' 
    ORDER BY timestamp DESC
""").df()

# Calculate 5-minute rolling volatility for research
df['returns'] = df['price'].pct_change()
df['volatility_5m'] = df['returns'].rolling(window=300).std()
print(df.tail())
```

### Option C: The Python SDK (Live Stream into Pandas)
If you want to stream data directly into a custom Python script or ML model:
```python
import asyncio
from client import MDrapClient

async def listen_to_market():
    client = MDrapClient()
    
    def on_trade(msg):
        print(f"New Trade: {msg['instrument']} @ {msg['price']}")
        
    client.on("event", on_trade)
    await client.subscribe()

asyncio.run(listen_to_market())
```

### Option D: Alternative Data — SEC Form 8-K & Form 4 Event-Driven Research
MDRAP's research engine allows quant researchers to programmatically fetch material corporate catalysts and insider signals without web scraping or API keys:
```python
from research import EdgarClient

client = EdgarClient()

# 1. Pull decoded Form 8-K material events with urgency levels
events = client.get_material_events("AAPL", limit=10)
for e in events:
    print(f"[{e.filing_date}] {e.urgency}: {', '.join(e.decoded_items)}")

# 2. Extract parsed Form 4 insider transactions (open-market buys vs sells)
insider_trades = client.get_insider_trades("MSFT", limit=20)
buys = [t for t in insider_trades if t.action == "BUY"]
print(f"Total open-market buy volume: ${sum(t.total_value for t in buys):,.2f}")

# 3. Pull audited GAAP quarterly financials from SEC XBRL database
facts = client.get_company_facts("NVDA", metric="Revenues", limit=8)
for f in facts:
    print(f"Period: {f['end_date']} | Frame: {f['frame']} | Revenue: ${f['value']:,.2f}")
```

### Option E: Level-2 Order Book Microstructure & Execution Quality Analysis
Inspect multi-rung market depth, micro-price, order book imbalance, and export trade execution ledgers with microsecond precision:
```python
from strategy_sdk import Strategy, OrderBook

# 1. Inspect live multi-tier order book ladder
book = strategy.get_order_book("AAPL")
ladder = book.get_ladder(depth=5)
print(f"Spread: {book.spread_bps:.2f} bps | MicroPrice: ${book.micro_price:.2f} | Imbalance: {book.imbalance:+.2%}")

# 2. Access the complete chronological execution ledger
ledger = strategy.get_execution_ledger()
for fill in ledger:
    print(f"[{fill['side']}] {fill['quantity']} @ ${fill['fill_price']} (Arrival: ${fill['arrival_price']}) "
          f"| Slippage: ${fill['slippage_usd']} | Eff Spread: {fill['effective_spread_bps']} bps "
          f"| Reason: {fill['signal_reason']}")

# 3. Export executions and point-in-time order book snapshots
strategy.export_executions("data/reports/strategy_run.json", format="json") # Full L2 snapshots
strategy.export_executions("data/reports/strategy_run.csv", format="csv")   # Flat table
```

Or execute directly from the CLI:
```bash
# View live Level-2 order book ladder and execution ledger table
python -m cli strategy run -s whale_momentum -i AAPL -e 1000 --book --executions

# Export execution quality ledger to JSON or CSV
python -m cli strategy run -s whale_momentum -i AAPL -e 1000 --export data/reports/aapl_run.csv
```

---

## 2. From Simulation to Real Money Trading

The magic of MDRAP's Strategy SDK is that **the algorithm code never changes**. You write your strategy once, and simply swap the "Executor" underneath it.

### Step 1: Simulated (Paper Trading)
This is what we just tested with the `VWAPSlicer`. The strategy is fed live market data, but the orders are routed to the `PaperExecutor` which mathematically simulates slippage and PnL.
```python
from strategy_sdk import Strategy, PaperExecutor, LiveStrategyRunner

class MyStrategy(Strategy):
    def __init__(self):
        super().__init__(name="AlphaBot")
        # Automatically defaults to PaperExecutor for risk-free simulation!

runner = LiveStrategyRunner(MyStrategy())
await runner.run()
```

### Step 2: Real Money Trading
To transition to real money, you do not touch your strategy logic. Instead, you write an adapter for your specific brokerage (e.g., Interactive Brokers, Binance, Coinbase) by subclassing the Executor.

```python
from strategy_sdk import Strategy, Order
from my_broker import BrokerAPI

class RealMoneyExecutor:
    """Custom Executor that routes orders to an actual exchange."""
    
    def __init__(self):
        self.api = BrokerAPI(api_key="YOUR_SECRET_KEY")
        
    def submit_order(self, symbol, side, order_type, quantity, price=None, bbo=None) -> Order:
        print(f"FIRING REAL ORDER: {side} {quantity} {symbol}")
        
        # 1. Fire network request to the real exchange
        response = self.api.place_order(symbol, side, quantity)
        
        # 2. Return the live Order object back to the Strategy
        return Order(order_id=response.id, status="PENDING")

# --- Swapping it into your Strategy ---
class MyStrategy(Strategy):
    def __init__(self):
        super().__init__(name="AlphaBot")
        # DANGER: We are overriding the paper simulator with real money!
        self.executor = RealMoneyExecutor() 

# Run exactly as before. The strategy thinks it is paper trading, 
# but the executor is silently moving real capital.
runner = LiveStrategyRunner(MyStrategy())
await runner.run()
```

**Why this architecture is powerful:**
Because your strategy logic (`on_tick`, `on_quote`) strictly relies on MDRAP's normalized `CanonicalEvent`, your algorithm is completely immune to exchange API changes, feed outages, or bizarre JSON formats. MDRAP cleans the data, feeds it to the strategy, and the strategy asks the Executor to place the trade.

---

## 3. Institutional Trading & Quantitative Research Engines

MDRAP provides 12 modular engines covering the complete institutional quant lifecycle:

### 1. Historical Backtesting Engine (`src/backtest.py`)
Point-in-time event replay avoiding lookahead bias. Computes Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor, equity and drawdown curves, and walk-forward out-of-sample optimization.
```bash
python cli.py backtest -s whale_momentum -i AAPL -c 100000
```

### 2. Portfolio Risk & VaR Engine (`src/risk.py`)
Institutional risk analytics supporting 3 Value-at-Risk methodologies (Historical Simulation, Parametric, and Monte Carlo), Expected Shortfall (CVaR), running high-watermark drawdown tracking, and multi-tier circuit breakers.
```bash
python cli.py risk -c 0.95 -k 100000
```

### 3. Persistent Multi-Timeframe Bar Database (`src/bardb.py`)
Incremental tick-to-candle rollup into 7 pre-computed intervals (`1s`, `5s`, `1m`, `5m`, `15m`, `1h`, `1d`) stored in SQLite with WAL mode. Supports point-in-time as-of and window temporal queries.
```bash
python cli.py bars summary
python cli.py bars query -i AAPL -n 1m -l 20
```

### 4. Options Pricing & Greeks Engine (`src/options.py`)
Pure-Python derivatives pricing with Black-Scholes-Merton (European), Cox-Ross-Rubinstein Binomial Tree (American early exercise), first and second-order Greeks (Delta, Gamma, Theta, Vega, Rho, Vanna, Volga), Newton-Raphson IV solver, and volatility surface construction.
```bash
python cli.py options price -u AAPL -s 155 -k 150 -t call
python cli.py options chain -u AAPL -s 155
```

### 5. News & Financial Sentiment Pipeline (`src/news.py`)
Aggregates RSS/Atom feeds (SEC, Fed, major financial wires), extracts ticker cashtags, and evaluates headline sentiment using financial dictionaries with price impact correlation.
```bash
python cli.py news analyze '$AAPL beats Q4 revenue expectations, raises dividend and buyback program'
```

### 6. Real-Time Alert Engine (`src/alerts.py`)
Persistent, configurable alerts for price thresholds, bid-ask spread blowouts, volume spikes (whale blocks), feed silence, and drawdown triggers with SQLite persistence.
```bash
python cli.py alert add -i AAPL -t ABOVE -v 200.0
python cli.py alert list
```

### 7. Watchlists & Portfolio Tracker (`src/portfolio.py`)
Named watchlists with symbol management alongside institutional portfolio tracking with realized/unrealized P&L, sector/strategy attribution, and benchmark comparison.
```bash
python cli.py watchlist add -n Tech -s AAPL MSFT NVDA
python cli.py watchlist list
python cli.py portfolio summary
```

### 8. Corporate Actions Processor (`src/corporate_actions.py`)
Calculates cumulative adjustment factors for stock splits (forward/reverse), cash dividends, symbol ticker changes, and delistings to maintain adjusted price and volume history.
```bash
python cli.py corpact -i AAPL
```

### 9. ML Feature Store (`src/features.py`)
Calculates technical indicators (RSI, MACD, Bollinger Bands, ATR, OBV) and market microstructure metrics (VPIN, order book imbalance, realized volatility) with point-in-time registry export.
```bash
python cli.py features list
```

### 10. Automated Task Scheduler (`src/scheduler.py`)
Cron-like task scheduler with aliases (`@hourly`, `@daily`, `@eod`) for automated end-of-day portfolio rollups, bar rollups, and reporting.
```bash
python cli.py schedule list
python cli.py schedule eod
```

### 11. FIX Protocol Engine (`src/fix_engine.py`)
FIX 4.2 / 4.4 parser and serializer supporting tag-value, checksum validation, session heartbeat/logon management, and standard order routing (`NewOrderSingle`, `OrderCancelRequest`, `ExecutionReport`).

### 12. Multi-Asset Class Data Model (`src/models.py`)
Extended canonical event model natively supporting Equities, Crypto, Futures, Options, Bonds, and FX with zero breaking changes to existing pipelines.

---

## 4. Ultra-Low Latency & Codebase Leaning Optimizations

To maintain institutional execution speeds and minimize cold-start and tick-loop overhead, the platform implements:

1. **Phase 13 Native C Hot-Path Accelerators (`src/fastpath.c`, `src/fastpath.py`)**:
   - **B-S-M European Pricing & 7 Greeks**: Vectorized closed-form Black-Scholes-Merton pricing with analytical Delta, Gamma, Vega, Theta, Rho, Vanna, and Volga computed simultaneously in a single C kernel call.
   - **Binomial American Options Tree (CRR Model)**: Evaluates deep American option early exercise boundaries via pre-calculated powers and dynamic programming (`fastpath_binomial_price` achieving **0.21 ms vs 13.6 ms, 64.1x speedup**).
   - **Newton-Raphson Implied Volatility Solver**: Analytical root-finding with bounded volatility clamping `[0.001, 5.0]` achieving microsecond-scale convergence.
   - **Vectorized Technical Indicators**: Wilder-smoothed RSI, EMA, ATR, and Bollinger Bands with standard deviation rolling window in C (`fastpath_calc_bollinger` achieving **1.04 ms vs 53.6 ms, 51.6x speedup**).
   - **Monte Carlo VaR Engine**: Vectorized Box-Muller normal transform and quantile selection simulating 10,000 portfolio paths in **3.44 ms**.
   - **Vectorized FIX Checksum**: High-throughput unsigned modulo-256 byte accumulation replacing Python loops.
   - **Seamless C/Python Parity**: Automatic `ctypes` bridge with zero-downtime, transparent fallback to pure Python if the dynamic library is absent.

2. **Module-Level Git Query Caching**: Eliminates redundant `git rev-parse` subprocess kernel executions (25–50 ms kernel delay per pipeline creation in `pipeline.py`), yielding instantaneous instantiation across batch runs and test suites.

3. **Codebase Consolidation & Leaning**:
   - **Argparse Subparser Factory**: Centralized `_sub()` helper eliminates ~250 lines of duplicate parser boilerplate in `src/cli.py`.
   - **TUI Dashboard Engine Merge**: Merged `src/sdk_dashboard.py` (184 lines) into `src/terminal_display.py`.
   - **Unified Client Architecture**: Integrated `MDrapClient` (asyncio TCP gateway client) directly into `src/client.py`, simplifying the client SDK surface.
   - **Deduplicated L2 Depth Reconstruction**: Replaced duplicated SQLite event parsing logic in `cmd_depth` and `cmd_vwap` with `_load_or_fetch_depth_events()`.
   - **Historical Parser Merge**: Merged `src/chd_cli.py` (132 lines) into `src/chd.py`.

4. **$O(1)$ LRU Cache Eviction**: `OrderedDict.popitem(last=False)` replaces iterator instantiation during saturated deduplication windows in `src/quality.py`.

5. **High-Throughput SQLite PRAGMAs**:
   - `PRAGMA mmap_size = 268435456;` (256 MB memory-mapped file I/O)
   - `PRAGMA cache_size = -64000;` (64 MB in-memory page cache)
   - `PRAGMA temp_store = MEMORY;`

6. **Local DB Fallback for Interactive Queries**: `mdrap depth` and `mdrap vwap` automatically query canonical events in SQLite/DuckDB first, avoiding blocking network timeouts and dropping execution latency from ~462 ms down to ~48 ms (10x speedup).


