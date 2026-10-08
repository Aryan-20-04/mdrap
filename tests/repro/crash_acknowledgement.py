"""Reproduce loss between Pipeline.process_one acknowledgement and storage flush.

Run from any directory with the repository's supported Python available:

    python tests/repro/crash_acknowledgement.py --events 1000

The child is force-terminated after all process_one calls return and before the
configured one-hour flush interval. Windows uses TerminateProcess through
Popen.kill(); POSIX uses SIGKILL. The parent compares acknowledged calls with
rows recovered from the database after reopening it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
CHILD = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from mdrap.models import RawEvent
from mdrap.pipeline import Pipeline
from mdrap.storage import Store

db_path, ack_path, src_path = sys.argv[1:4]
count = int(sys.argv[4])
store = Store(db_path)
pipeline = Pipeline(store=store, async_writer=True, flush_interval_s=3600)
now = time.time()
for i in range(count):
    event = RawEvent(source="CRASH_REPRO", payload={
        "instrument": "MDRAP-TEST", "event_type": "TRADE",
        "exchange_ts": now, "sequence": i + 1,
        "price": 100.0 + i / 10000, "quantity": 1.0,
    })
    if pipeline.process_one(event) is None:
        raise RuntimeError(f"event {i + 1} was not acknowledged as a canonical event")
Path(ack_path).write_text(str(count), encoding="ascii")
while True:
    time.sleep(1)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.events < 2000:
        parser.error("--events must be from 1 to 1999 to stay below batch flush size")

    sys.path.insert(0, str(SRC))
    from mdrap.storage import Store

    with tempfile.TemporaryDirectory(prefix="mdrap-crash-repro-") as temp_dir:
        db_path = Path(temp_dir) / "events.db"
        ack_path = Path(temp_dir) / "acknowledged.txt"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CHILD,
                str(db_path),
                str(ack_path),
                str(SRC),
                str(args.events),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 60
            while (
                not ack_path.exists() or ack_path.stat().st_size == 0
            ) and child.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("child did not finish acknowledging events")
                time.sleep(0.01)
            if child.poll() is not None:
                stderr = child.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"child exited before acknowledgement: {stderr}")

            acknowledged = int(ack_path.read_text(encoding="ascii"))
            child.kill()
            child.wait(timeout=10)

            recovered_store = Store(str(db_path))
            try:
                canonical = recovered_store.conn.execute(
                    "SELECT count(*) FROM canonical_events"
                ).fetchone()[0]
                quarantined = recovered_store.conn.execute(
                    "SELECT count(*) FROM quarantine"
                ).fetchone()[0]
            finally:
                recovered_store.close()

            recovered = canonical + quarantined
            print(f"acknowledged={acknowledged}")
            print(f"recovered_canonical={canonical}")
            print(f"recovered_quarantine={quarantined}")
            print(f"lost_at_least={max(0, acknowledged - recovered)}")
            return 0 if recovered >= acknowledged else 2
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
