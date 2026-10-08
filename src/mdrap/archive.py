"""
Immutable Raw Event Archive module.

Implements Spec §6.10 and §19 — immutable raw event persistence.
This is a write-ahead JSONL archive that captures every raw event BEFORE processing,
partitioned by date and source.
"""

import json
import os
import re
import time
import warnings
from typing import Iterator, Optional, Any

from .models import RawEvent

__stability__ = "stable"

_SAFE_SOURCE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _safe_source(source: str) -> str:
    """Validate and sanitize source identifier to prevent path traversal (Finding M1)."""
    if not source or not isinstance(source, str):
        return "UNKNOWN"
    base = os.path.basename(source.replace("\\", "/"))
    if not _SAFE_SOURCE_RE.match(base) or base in (".", ".."):
        base = re.sub(r"[^A-Za-z0-9_\-]", "_", base)
    return base or "UNKNOWN"


class RawArchive:
    def __init__(self, base_dir: str = "data/raw_archive", buffer_size: int = 500):
        warnings.warn(
            "RawArchive in mdrap.archive is deprecated in MDRAP v3.0.0; "
            "use IngestLog WAL as the mandatory durability boundary instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.base_dir = os.path.abspath(base_dir)
        self.buffer_size = buffer_size
        self._buffer: list[RawEvent] = []
        self._file_handles: dict[str, Any] = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def write(self, raw: RawEvent) -> None:
        self._buffer.append(raw)
        if len(self._buffer) >= self.buffer_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return

        grouped: dict[str, list[str]] = {}
        for raw in self._buffer:
            safe_src = _safe_source(raw.source)
            # Format receive_timestamp to YYYY-MM-DD for partitioning
            date_str = time.strftime("%Y-%m-%d", time.gmtime(raw.receive_timestamp))
            dir_path = os.path.join(self.base_dir, date_str)
            file_path = os.path.abspath(os.path.join(dir_path, f"{safe_src}.jsonl"))

            # Enforce destination stays strictly inside base_dir (Finding M1)
            if not file_path.startswith(self.base_dir):
                raise ValueError(f"Path traversal detected for source {raw.source!r}")

            os.makedirs(dir_path, exist_ok=True)

            if file_path not in grouped:
                grouped[file_path] = []

            # Line is a JSON object
            line = json.dumps(
                {
                    "raw_id": raw.raw_id,
                    "source": raw.source,
                    "payload": raw.payload,
                    "receive_timestamp": raw.receive_timestamp,
                }
            )
            grouped[file_path].append(line)

        for file_path, lines in grouped.items():
            if file_path not in self._file_handles:
                self._file_handles[file_path] = open(file_path, "a", encoding="utf-8")

            handle = self._file_handles[file_path]
            for line in lines:
                handle.write(line + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

        # Close stale file handles across date boundaries to prevent FD leak (OPS-03)
        if len(self._file_handles) > 16:
            active_keys = set(grouped.keys())
            stale_keys = [k for k in self._file_handles if k not in active_keys]
            for k in stale_keys:
                try:
                    self._file_handles[k].close()
                except Exception:
                    pass
                del self._file_handles[k]

        self._buffer.clear()

    def close(self) -> None:
        self.flush()
        for handle in self._file_handles.values():
            handle.close()
        self._file_handles.clear()

    def stats(self) -> dict:
        total_events = 0
        dates = set()
        sources = set()
        size_bytes = 0

        if not os.path.exists(self.base_dir):
            return {"total_events": 0, "dates": [], "sources": [], "size_bytes": 0}

        for root, _, files in os.walk(self.base_dir):
            for file in files:
                if file.endswith(".jsonl"):
                    file_path = os.path.join(root, file)
                    date_dir = os.path.basename(root)
                    dates.add(date_dir)

                    source = file[:-6]  # remove .jsonl
                    sources.add(source)

                    size_bytes += os.path.getsize(file_path)

                    # count lines
                    with open(file_path, "r", encoding="utf-8") as f:
                        for _ in f:
                            total_events += 1

        return {
            "total_events": total_events,
            "dates": sorted(list(dates)),
            "sources": sorted(list(sources)),
            "size_bytes": size_bytes,
        }


def replay(
    base_dir: str, date: Optional[str] = None, source: Optional[str] = None
) -> Iterator[RawEvent]:
    """
    Yields RawEvent objects from archived JSONL files.
    """
    if not os.path.exists(base_dir):
        return

    # Get all date directories sorted
    date_dirs = sorted(
        [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    )

    if date:
        date_dirs = [d for d in date_dirs if d == date]

    for date_dir in date_dirs:
        dir_path = os.path.join(base_dir, date_dir)

        # Get all jsonl files
        files = sorted([f for f in os.listdir(dir_path) if f.endswith(".jsonl")])

        if source:
            safe_src = _safe_source(source)
            target_file = f"{safe_src}.jsonl"
            files = [f for f in files if f == target_file]

        for file in files:
            file_path = os.path.join(dir_path, file)
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            data = json.loads(line)
                            yield RawEvent(
                                raw_id=data["raw_id"],
                                source=data["source"],
                                payload=data["payload"],
                                receive_timestamp=data["receive_timestamp"],
                            )
                        except (json.JSONDecodeError, KeyError):
                            # Skip corrupted/truncated archive lines without crashing replay
                            continue
