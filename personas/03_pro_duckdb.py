"""
Persona 3: The Pro Quant
Queries DuckDB natively for massive analytics.
"""
import time
import os
import sys

try:
    import duckdb
except ImportError:
    print("Please install duckdb: pip install duckdb")
    sys.exit(1)

DUCKDB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mdrap.duckdb'))

def main():
    print("Pro Persona: Querying DuckDB Columnar Store...")
    if not os.path.exists(DUCKDB_PATH):
        print("DuckDB not found. Please run 00_run_market_server.py first!")
        return

    # We don't connect to the locked database file anymore! 
    # We use an entirely in-memory temporary DuckDB to query the replicated parquet file!
    try:
        conn = duckdb.connect(":memory:")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    queries_run = 0
    parquet_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'trades.parquet'))
    
    try:
        while True:
            if not os.path.exists(parquet_path):
                print("Waiting for server to generate parquet replication...")
                time.sleep(1)
                continue
                
            t0 = time.perf_counter()
            try:
                # Heavy analytical VWAP calculation directly on the replicated file
                query = f"""
                    SELECT instrument_id, 
                           SUM(price * size) / SUM(size) as vwap,
                           SUM(size) as total_volume
                    FROM '{parquet_path}' 
                    GROUP BY instrument_id
                    ORDER BY total_volume DESC
                """
                df = conn.execute(query).fetchdf()
                t1 = time.perf_counter()
                latency = (t1 - t0) * 1000
                queries_run += 1
                
                print(f"[Query {queries_run}] VWAP from Parquet Replica | Latency: {latency:.2f} ms")
                if not df.empty:
                    print(df.head(2))
                    
            except Exception as e:
                print(f"Error querying parquet: {e}")
            
            print("-" * 50)
            time.sleep(1.0) # Query every second
            
    except KeyboardInterrupt:
        print(f"\nStopping. Total queries run: {queries_run}")

if __name__ == "__main__":
    main()
