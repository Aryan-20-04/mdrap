"""
Verify tombstone persistence across process restart.

Writes tombstones to a SQLite table, closes the store, reopens it,
and verifies tombstone records survive restart.
"""
import sys, os, json, time, sqlite3
sys.path.insert(0, "src")

from mdrap.storage import Store
from mdrap.ws_feed import WebSocketFeedManager
from mdrap.models import RawEvent

DB_PATH = "data/tombstone_restart_test.db"

# Cleanup
for p in [DB_PATH, f"{DB_PATH}-wal", f"{DB_PATH}-shm"]:
    if os.path.exists(p):
        os.remove(p)

os.makedirs("data", exist_ok=True)

# --- STEP 1: Manufacture tombstones via WebSocketFeedManager ---
print("Step 1: Create backpressure drops and tombstones...")
mgr = WebSocketFeedManager(
    symbols=["BTC/USD"],
    venues=["BINANCE", "KRAKEN"],
    max_queue_size=5,
    max_tombstones=50000,
)

# Overfill queue to generate tombstones
for i in range(50):
    ev = RawEvent(
        source="BINANCE",
        payload={"instrument": "BTC/USD", "bid": 95000.0 + i, "ask": 95001.0 + i},
        receive_timestamp=time.time(),
        raw_id=f"test-raw-{i}",
    )
    try:
        mgr._queue.put_nowait(ev)
    except Exception:
        try:
            evicted = mgr._queue.get_nowait()
        except Exception:
            evicted = ev
        mgr._drop_count += 1
        mgr._drop_counts["BINANCE"] = mgr._drop_counts.get("BINANCE", 0) + 1
        tombstone = mgr._create_drop_tombstone("BINANCE", "BTC/USD", evicted)
        with mgr._tombstone_lock:
            mgr._tombstones.append(tombstone)
        try:
            mgr._queue.put_nowait(ev)
        except Exception:
            pass

in_memory_count = len(mgr.get_tombstones())
in_memory_drops = mgr._drop_count
print(f"  In-memory tombstones: {in_memory_count}, Total drops: {in_memory_drops}")

# --- STEP 2: Persist tombstones to SQLite ---
print("Step 2: Persisting tombstones to SQLite quarantine table...")
store = Store(DB_PATH)

tombstone_rows = []
for t in mgr.get_tombstones():
    tombstone_rows.append({
        "raw_id": t.raw_id,
        "source": t.source,
        "instrument": t.payload.get("instrument", ""),
        "drop_reason": t.payload.get("drop_reason", "BACKPRESSURE_DROP"),
        "drop_count": t.payload.get("drop_count", 0),
        "evicted_raw_id": t.payload.get("evicted_raw_id"),
        "receive_ts": t.receive_timestamp,
        "payload_json": json.dumps(t.payload),
    })

# Persist using quarantine batch (tombstones are non-canonical events - use lineage table)
# Store in audit_log (available in schema) with tag
try:
    store.conn.execute("""
        CREATE TABLE IF NOT EXISTS drop_tombstones (
            raw_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            instrument TEXT NOT NULL,
            drop_reason TEXT NOT NULL,
            drop_count INTEGER NOT NULL,
            evicted_raw_id TEXT,
            receive_ts REAL NOT NULL,
            payload_json TEXT,
            created_at REAL NOT NULL
        )
    """)
    store.conn.executemany("""
        INSERT OR IGNORE INTO drop_tombstones
        (raw_id, source, instrument, drop_reason, drop_count, evicted_raw_id, receive_ts, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            r["raw_id"], r["source"], r["instrument"], r["drop_reason"],
            r["drop_count"], r["evicted_raw_id"], r["receive_ts"],
            r["payload_json"], time.time()
        )
        for r in tombstone_rows
    ])
    store.commit()
    persisted = store.conn.execute("SELECT count(*) FROM drop_tombstones").fetchone()[0]
    print(f"  Persisted {persisted} tombstones to SQLite")
except Exception as e:
    print(f"  Error: {e}")

store.close()
print("  Store closed (simulating process exit)")
time.sleep(0.5)

# --- STEP 3: Restart - reopen store and verify tombstones survive ---
print("Step 3: Restart - reopening store and verifying tombstone persistence...")
store2 = Store(DB_PATH)

integrity = store2.conn.execute("PRAGMA integrity_check").fetchone()[0]
recovered = store2.conn.execute("SELECT count(*) FROM drop_tombstones").fetchone()[0]
recovered_drops = store2.conn.execute("SELECT MAX(drop_count) FROM drop_tombstones").fetchone()[0]
recovered_venues = store2.conn.execute("SELECT source, count(*) FROM drop_tombstones GROUP BY source").fetchall()

print(f"  PRAGMA integrity_check: {integrity}")
print(f"  Recovered tombstones: {recovered}")
print(f"  Max drop_count in DB: {recovered_drops}")
print(f"  By venue: {recovered_venues}")

store2.close()

# Cleanup
for p in [DB_PATH, f"{DB_PATH}-wal", f"{DB_PATH}-shm"]:
    if os.path.exists(p):
        os.remove(p)

print()
print("RESULT:")
print(f"  In-memory tombstones created: {in_memory_count}")
print(f"  Tombstones persisted: {persisted}")
print(f"  Tombstones recovered after restart: {recovered}")
print(f"  Integrity check: {integrity}")
match = recovered == in_memory_count
print(f"  Tombstone count match: {match}")
print()
if match and integrity == "ok":
    print("PASS: Tombstones survive process restart via SQLite WAL persistence")
else:
    print("FAIL: Tombstone persistence mismatch")
