"""
Persona 1: The Beginner
Polls the SQLite database sequentially. Watch for 'database is locked' errors 
when the main server flushes a batch.
"""
import sqlite3
import time
import os

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mdrap.db'))

def main():
    print("Beginner Persona: Polling SQLite DB for BBO...")
    if not os.path.exists(DB_PATH):
        print("Database not found. Please run 00_run_market_server.py first!")
        return

    # Using a short timeout to explicitly trigger the lock exception
    conn = sqlite3.connect(DB_PATH, timeout=0.1)
    
    queries_run = 0
    errors = 0
    
    try:
        while True:
            t0 = time.perf_counter()
            try:
                cur = conn.execute("SELECT COUNT(*) FROM bbo_quotes")
                count = cur.fetchone()[0]
                t1 = time.perf_counter()
                latency = (t1 - t0) * 1000
                queries_run += 1
                
                print(f"[Query {queries_run}] Total Rows: {count} | Latency: {latency:.2f} ms", flush=True)
                
                if latency > 100:
                    print(f"   ⚠️ WARNING: High latency detected ({latency:.2f} ms). Database was likely flushing.")
                    
            except sqlite3.OperationalError as e:
                errors += 1
                print(f"   ❌ ERROR: {e}. The SQLite database is strictly locked by the ingestion server!", flush=True)
            
            time.sleep(0.2) # Poll 5 times a second
    except KeyboardInterrupt:
        print("\nStopping...")
        print(f"Total Queries: {queries_run}, Total Lock Errors: {errors}")

if __name__ == "__main__":
    main()
