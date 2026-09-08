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
from sdk.client import MDrapClient

async def listen_to_market():
    client = MDrapClient()
    
    def on_trade(msg):
        print(f"New Trade: {msg['instrument']} @ {msg['price']}")
        
    client.on("event", on_trade)
    await client.subscribe()

asyncio.run(listen_to_market())
```

---

## 2. From Simulation to Real Money Trading

The magic of MDRAP's Strategy SDK is that **the algorithm code never changes**. You write your strategy once, and simply swap the "Executor" underneath it.

### Step 1: Simulated (Paper Trading)
This is what we just tested with the `VWAPSlicer`. The strategy is fed live market data, but the orders are routed to the `PaperExecutor` which mathematically simulates slippage and PnL.
```python
from strategy_sdk import Strategy, PaperExecutor
from sdk.execution import LiveStrategyRunner

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
