"""
MDRAP Historical Storage & Partitioning Engine (Spec §14, §23, §26).

Provides institutional historical market data storage with:
- Standardized Hive-partitioned directory layout:
    {base_dir}/year={YYYY}/month={MM}/day={DD}/symbol={SYM}/part_{chunk:04d}.{ext}
- Zero-dependency stdlib fallback formats (jsonl.gz, csv.gz) guaranteeing 100% platform parity.
- Optional PyArrow Parquet columnar acceleration when pyarrow is available.
- Atomic _metadata.json manifest catalog for instantaneous partition pruning.
- High-efficiency range queries, streaming scan iterators, and automated age-based data retention.
"""

from __future__ import annotations

import csv
import datetime
import gzip
import json
import os
import shutil
import sqlite3
import time
from typing import Any, Generator, Iterable, Optional, Sequence

from models import CanonicalEvent

__stability__ = "stable"

# Check for PyArrow parquet availability
try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    HAS_PYARROW = True
except ImportError:
    pa = None
    pq = None
    HAS_PYARROW = False

METADATA_FILENAME = "_metadata.json"
SCHEMA_VERSION = 1

CANONICAL_COLUMNS = [
    "event_id",
    "instrument_id",
    "event_type",
    "exchange_timestamp",
    "receive_timestamp",
    "processing_timestamp",
    "source",
    "sequence_number",
    "price",
    "quantity",
    "bid_price",
    "bid_size",
    "ask_price",
    "ask_size",
    "quality_status",
    "reasons",
    "raw_id",
]


def _event_to_dict(event: CanonicalEvent | dict) -> dict[str, Any]:
    if isinstance(event, CanonicalEvent):
        reasons_raw = event.reasons
        reasons_str = (
            json.dumps(reasons_raw)
            if isinstance(reasons_raw, list)
            else str(reasons_raw or "[]")
        )
        return {
            "event_id": event.event_id,
            "instrument_id": event.instrument_id,
            "event_type": event.event_type.value
            if hasattr(event.event_type, "value")
            else str(event.event_type),
            "exchange_timestamp": float(event.exchange_timestamp or 0.0),
            "receive_timestamp": float(event.receive_timestamp or 0.0),
            "processing_timestamp": float(event.processing_timestamp or 0.0),
            "source": str(event.source or ""),
            "sequence_number": event.sequence_number,
            "price": event.price,
            "quantity": event.quantity,
            "bid_price": event.bid_price,
            "bid_size": event.bid_size,
            "ask_price": event.ask_price,
            "ask_size": event.ask_size,
            "quality_status": event.quality_status.value
            if hasattr(event.quality_status, "value")
            else str(event.quality_status),
            "reasons": reasons_str,
            "raw_id": str(event.raw_id or ""),
        }
    return dict(event)


class HistoricalPartitioner:
    """
    Partitions market events into date-and-symbol directories and writes compressed files.
    """

    def __init__(self, base_dir: str = "data/historical"):
        self.base_dir = os.path.abspath(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.manifest_path = os.path.join(self.base_dir, METADATA_FILENAME)

    def _load_manifest(self) -> dict[str, Any]:
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "schema_version": SCHEMA_VERSION,
            "partitions": {},
            "total_rows": 0,
            "total_bytes": 0,
            "updated_at": 0.0,
        }

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        manifest["updated_at"] = time.time()
        # Atomic write via temporary file
        tmp_path = self.manifest_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp_path, self.manifest_path)

    @staticmethod
    def _partition_key(ts: float, symbol: str) -> str:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        return (
            f"year={dt.year:04d}/month={dt.month:02d}/day={dt.day:02d}/symbol={symbol}"
        )

    def partition_events(
        self,
        events: Iterable[CanonicalEvent | dict],
        chunk_size: int = 10000,
        fmt: str = "auto",
    ) -> dict[str, Any]:
        """
        Partition and persist events into Hive-style directory hierarchy.
        Supported formats: 'auto', 'parquet' (if available), 'jsonl.gz', 'csv.gz'.
        """
        if fmt == "auto":
            selected_fmt = "parquet" if HAS_PYARROW else "jsonl.gz"
        else:
            selected_fmt = fmt.lower()
            if selected_fmt == "parquet" and not HAS_PYARROW:
                selected_fmt = "jsonl.gz"

        # Group records by partition key
        buckets: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            row = _event_to_dict(ev)
            ts = float(row.get("exchange_timestamp") or 0.0)
            sym = str(row.get("instrument_id") or "UNKNOWN")
            key = self._partition_key(ts, sym)
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(row)

        manifest = self._load_manifest()
        partitions_dict = manifest.setdefault("partitions", {})

        written_files = []
        rows_added = 0
        bytes_added = 0

        for key, records in buckets.items():
            part_dir = os.path.join(self.base_dir, key)
            os.makedirs(part_dir, exist_ok=True)

            existing_part = partitions_dict.get(
                key,
                {
                    "files": [],
                    "format": selected_fmt,
                    "row_count": 0,
                    "min_exchange_ts": float("inf"),
                    "max_exchange_ts": float("-inf"),
                    "min_seq": None,
                    "max_seq": None,
                    "size_bytes": 0,
                },
            )

            # Chunk records
            for i in range(0, len(records), chunk_size):
                chunk = records[i : i + chunk_size]
                chunk_id = len(existing_part["files"]) + 1
                ext = (
                    "parquet"
                    if selected_fmt == "parquet"
                    else ("csv.gz" if selected_fmt == "csv.gz" else "jsonl.gz")
                )
                fname = f"part_{chunk_id:04d}.{ext}"
                target_path = os.path.join(part_dir, fname)
                tmp_path = target_path + ".tmp"

                # Write chunk atomically
                file_bytes = 0
                if selected_fmt == "parquet" and HAS_PYARROW:
                    table = pa.Table.from_pylist(chunk)
                    pq.write_table(table, tmp_path, compression="snappy")
                    os.replace(tmp_path, target_path)
                    file_bytes = os.path.getsize(target_path)
                elif selected_fmt == "csv.gz":
                    with gzip.open(tmp_path, "wt", encoding="utf-8", newline="") as gz:
                        writer = csv.DictWriter(gz, fieldnames=CANONICAL_COLUMNS)
                        writer.writeheader()
                        for r in chunk:
                            writer.writerow({c: r.get(c) for c in CANONICAL_COLUMNS})
                    os.replace(tmp_path, target_path)
                    file_bytes = os.path.getsize(target_path)
                else:  # jsonl.gz
                    with gzip.open(tmp_path, "wt", encoding="utf-8") as gz:
                        for r in chunk:
                            gz.write(json.dumps(r) + "\n")
                    os.replace(tmp_path, target_path)
                    file_bytes = os.path.getsize(target_path)

                rel_file_path = os.path.join(key, fname).replace("\\", "/")
                existing_part["files"].append(rel_file_path)
                written_files.append(rel_file_path)

                # Update bounds
                for r in chunk:
                    ts = float(r.get("exchange_timestamp") or 0.0)
                    existing_part["min_exchange_ts"] = min(
                        existing_part["min_exchange_ts"], ts
                    )
                    existing_part["max_exchange_ts"] = max(
                        existing_part["max_exchange_ts"], ts
                    )
                    seq = r.get("sequence_number")
                    if seq is not None and isinstance(seq, int):
                        existing_part["min_seq"] = (
                            seq
                            if existing_part["min_seq"] is None
                            else min(existing_part["min_seq"], seq)
                        )
                        existing_part["max_seq"] = (
                            seq
                            if existing_part["max_seq"] is None
                            else max(existing_part["max_seq"], seq)
                        )

                existing_part["row_count"] += len(chunk)
                existing_part["size_bytes"] += file_bytes
                rows_added += len(chunk)
                bytes_added += file_bytes

            partitions_dict[key] = existing_part

        manifest["total_rows"] += rows_added
        manifest["total_bytes"] += bytes_added
        self._save_manifest(manifest)

        return {
            "written_files": written_files,
            "rows_added": rows_added,
            "bytes_added": bytes_added,
            "partitions_updated": list(buckets.keys()),
        }

    def partition_from_sqlite(
        self,
        db_path: str,
        chunk_size: int = 10000,
        fmt: str = "auto",
        symbol: Optional[str] = None,
    ) -> dict[str, Any]:
        """Stream events directly from SQLite and partition them into historical storage."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            query = "SELECT * FROM canonical_events WHERE 1=1"
            params: list[Any] = []
            if symbol:
                query += " AND instrument_id = ?"
                params.append(symbol)
            query += " ORDER BY exchange_timestamp ASC, rowid ASC"

            cursor = conn.execute(query, params)
            total_stats = {
                "written_files": [],
                "rows_added": 0,
                "bytes_added": 0,
                "partitions_updated": [],
            }

            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                events = [dict(r) for r in rows]
                res = self.partition_events(events, chunk_size=chunk_size, fmt=fmt)
                total_stats["written_files"].extend(res["written_files"])
                total_stats["rows_added"] += res["rows_added"]
                total_stats["bytes_added"] += res["bytes_added"]
                for p in res["partitions_updated"]:
                    if p not in total_stats["partitions_updated"]:
                        total_stats["partitions_updated"].append(p)

            return total_stats
        finally:
            conn.close()


class HistoricalCatalog:
    """
    Catalog and analytical query reader over historical partition store.
    Leverages partition pruning to avoid unnecessary disk I/O.
    """

    def __init__(self, base_dir: str = "data/historical"):
        self.base_dir = os.path.abspath(base_dir)
        self.manifest_path = os.path.join(self.base_dir, METADATA_FILENAME)
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> dict[str, Any]:
        if os.path.exists(self.manifest_path):
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"partitions": {}, "total_rows": 0, "total_bytes": 0}

    def prune_partitions(
        self,
        symbol: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """
        Prune partitions based on symbol filter and exchange_timestamp intervals.
        Returns list of (partition_key, partition_metadata) tuples.
        """
        matching: list[tuple[str, dict[str, Any]]] = []
        partitions = self.manifest.get("partitions", {})

        for key, meta in partitions.items():
            # Check symbol component
            if symbol:
                sym_token = f"symbol={symbol}"
                if sym_token not in key:
                    continue

            min_ts = meta.get("min_exchange_ts", float("-inf"))
            max_ts = meta.get("max_exchange_ts", float("inf"))

            # Interval overlap check: [min_ts, max_ts] overlaps [start_ts, end_ts]
            if start_ts is not None and max_ts < start_ts:
                continue
            if end_ts is not None and min_ts > end_ts:
                continue

            matching.append((key, meta))

        # Sort chronologically by partition metadata
        matching.sort(key=lambda item: item[1].get("min_exchange_ts", 0.0))
        return matching

    def scan(
        self,
        symbol: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        columns: Optional[Sequence[str]] = None,
    ) -> Generator[dict[str, Any], None, None]:
        """
        Stream events from disk, applying partition pruning and point filtering.
        """
        pruned = self.prune_partitions(symbol=symbol, start_ts=start_ts, end_ts=end_ts)

        for _, meta in pruned:
            files = meta.get("files", [])
            for rel_file in files:
                abs_path = os.path.join(self.base_dir, rel_file)
                if not os.path.exists(abs_path):
                    continue

                if rel_file.endswith(".parquet") and HAS_PYARROW:
                    table = pq.read_table(
                        abs_path, columns=list(columns) if columns else None
                    )
                    for row in table.to_pylist():
                        ts = float(row.get("exchange_timestamp") or 0.0)
                        if start_ts is not None and ts < start_ts:
                            continue
                        if end_ts is not None and ts > end_ts:
                            continue
                        yield row
                elif rel_file.endswith(".csv.gz"):
                    with gzip.open(abs_path, "rt", encoding="utf-8") as gz:
                        reader = csv.DictReader(gz)
                        for row in reader:
                            ts = float(row.get("exchange_timestamp") or 0.0)
                            if start_ts is not None and ts < start_ts:
                                continue
                            if end_ts is not None and ts > end_ts:
                                continue
                            if columns:
                                yield {c: row.get(c) for c in columns}
                            else:
                                yield dict(row)
                else:  # jsonl.gz
                    with gzip.open(abs_path, "rt", encoding="utf-8") as gz:
                        for line in gz:
                            line = line.strip()
                            if not line:
                                continue
                            row = json.loads(line)
                            ts = float(row.get("exchange_timestamp") or 0.0)
                            if start_ts is not None and ts < start_ts:
                                continue
                            if end_ts is not None and ts > end_ts:
                                continue
                            if columns:
                                yield {c: row.get(c) for c in columns}
                            else:
                                yield row

    def query_range(
        self,
        symbol: Optional[str] = None,
        start_ts: Optional[float] = None,
        end_ts: Optional[float] = None,
        columns: Optional[Sequence[str]] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Collect matching events into memory up to limit."""
        results: list[dict[str, Any]] = []
        for rec in self.scan(
            symbol=symbol, start_ts=start_ts, end_ts=end_ts, columns=columns
        ):
            results.append(rec)
            if limit and len(results) >= limit:
                break
        return results


class RetentionPolicy:
    """
    Manages historical data retention and automatic cold partition purging.
    """

    def __init__(self, base_dir: str = "data/historical"):
        self.base_dir = os.path.abspath(base_dir)
        self.partitioner = HistoricalPartitioner(base_dir=self.base_dir)

    def apply_retention(
        self, max_age_days: int, dry_run: bool = False
    ) -> dict[str, Any]:
        """
        Delete partitions whose max_exchange_ts is older than max_age_days.
        """
        if max_age_days <= 0:
            raise ValueError(f"max_age_days must be positive: {max_age_days}")

        cutoff_ts = time.time() - (max_age_days * 86400.0)
        manifest = self.partitioner._load_manifest()
        partitions = manifest.get("partitions", {})

        pruned_keys = []
        deleted_files_count = 0
        reclaimed_bytes = 0
        rows_removed = 0

        for key, meta in list(partitions.items()):
            max_ts = meta.get("max_exchange_ts", 0.0)
            if max_ts < cutoff_ts:
                pruned_keys.append(key)
                files = meta.get("files", [])
                for rel_file in files:
                    abs_path = os.path.join(self.base_dir, rel_file)
                    if os.path.exists(abs_path):
                        reclaimed_bytes += os.path.getsize(abs_path)
                        deleted_files_count += 1
                        if not dry_run:
                            try:
                                os.remove(abs_path)
                            except OSError:
                                pass

                rows_removed += meta.get("row_count", 0)

                # Clean up directory hierarchy if empty
                if not dry_run:
                    part_dir = os.path.join(self.base_dir, key)
                    try:
                        shutil.rmtree(part_dir, ignore_errors=True)
                    except OSError:
                        pass
                    partitions.pop(key, None)

        if not dry_run and pruned_keys:
            manifest["total_rows"] = max(
                0, manifest.get("total_rows", 0) - rows_removed
            )
            manifest["total_bytes"] = max(
                0, manifest.get("total_bytes", 0) - reclaimed_bytes
            )
            self.partitioner._save_manifest(manifest)

        return {
            "cutoff_timestamp": cutoff_ts,
            "pruned_partitions": len(pruned_keys),
            "deleted_files": deleted_files_count,
            "reclaimed_bytes": reclaimed_bytes,
            "rows_removed": rows_removed,
            "dry_run": dry_run,
        }
