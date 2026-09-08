"""
MDRAP Simulation Server
Runs the TCP Gateway and continuously flushes batches of mock data to SQLite/DuckDB 
to simulate a heavy market load. Run this first!
"""
import sys
import os
import asyncio
import time
import random
import sqlite3

# Ensure we can import from src/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

try:
    import duckdb
except ImportError:
    print("Please install duckdb: pip install duckdb")
    sys.exit(1)

from gateway_tcp import TCPGatewayServer

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mdrap.db'))
DUCKDB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mdrap.duckdb'))

def setup_databases():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # SQLite Optimizations
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute('''
        CREATE TABLE IF NOT EXISTS bbo_quotes (
            instrument_id TEXT,
            bid_price REAL,
            ask_price REAL,
            bid_size REAL,
            ask_size REAL,
            timestamp REAL,
            quality TEXT
        )
    ''')
    conn.commit()
    
    # DuckDB Optimizations
    dconn = duckdb.connect(DUCKDB_PATH)
    dconn.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            instrument_id VARCHAR,
            price DOUBLE,
            size DOUBLE,
            timestamp DOUBLE
        )
    ''')
    return conn, dconn

async def main():
    print("Starting MDRAP Load Simulation Server...")
    conn, dconn = setup_databases()
    server = TCPGatewayServer(port=9000)
    await server.start()
    
    print("TCP Gateway listening on port 9000.")
    print("Generating heavy market load... (Press Ctrl+C to stop)")
    
    instruments = ["AAPL", "BTC/USD", "TSLA", "NVDA", "SPY"]
    
    try:
        batch_count = 0
        while True:
            # Generate a batch of events
            sqlite_batch = []
            duck_batch = []
            
            for _ in range(5000): # Batch size
                sym = random.choice(instruments)
                price = random.uniform(100, 500)
                ts = time.time()
                
                sqlite_batch.append((sym, price - 0.01, price + 0.01, 100, 100, ts, "VALID"))
                duck_batch.append((sym, price, 100, ts))
                
                # Broadcast over TCP (simulating Intermediate push)
                payload = {
                    "type": "event",
                    "instrument": sym,
                    "event_type": "TRADE",
                    "quality": "VALID",
                    "price": price,
                    "size": 100,
                    "ts": ts
                }
                quote_payload = {
                    "type": "event",
                    "instrument": sym,
                    "event_type": "QUOTE",
                    "quality": "VALID",
                    "bid": price - 0.01,
                    "ask": price + 0.01,
                    "bid_size": 1000,
                    "ask_size": 1000,
                    "ts": ts
                }
                await server.broadcast(quote_payload)
                await server.broadcast(payload)
                
            t0 = time.perf_counter()
            
            # Optimized SQLite Batch Insert
            conn.executemany("INSERT INTO bbo_quotes VALUES (?,?,?,?,?,?,?)", sqlite_batch)
            conn.commit()
            
            # Optimized DuckDB Insert via Pandas
            import pandas as pd
            df = pd.DataFrame(duck_batch, columns=['instrument_id', 'price', 'size', 'timestamp'])
            dconn.append('trades', df)
            
            # Export to Parquet to unblock concurrent Pro Analysts
            parquet_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'trades.parquet'))
            try:
                dconn.execute(f"COPY trades TO '{parquet_path}' (FORMAT PARQUET)")
            except Exception as e:
                # Windows places read-locks on files when another process is actively querying them.
                # If the Pro script is reading at this exact millisecond, we gracefully skip the 
                # export for this batch. It will succeed on the next loop.
                if "Access is denied" in str(e) or "IO Error" in str(e):
                    pass
                else:
                    raise e
            
            t1 = time.perf_counter()
            batch_count += 1
            print(f"Batch {batch_count} flushed (5000 events) in {(t1-t0)*1000:.2f}ms. TCP clients: {len(server.clients)}")
            
            await asyncio.sleep(0.5) # Yield and throttle
            
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        await server.stop()
        conn.close()
        dconn.close()

if __name__ == "__main__":
    try:
        # Suppress the massive traceback on Windows
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Server] Shutdown gracefully.")
    except Exception as e:
        # Suppress CancelledError explicitly
        if "CancelledError" not in str(type(e)):
            raise
