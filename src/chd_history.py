"""CHD trade normalization, snapshot-aware L2 reconstruction and atomic imports."""

from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

from chd import (
    CHDClient,
    CHDError,
    HistoricalRequest,
    IntegrityError,
    received_ns,
    utc_datetime,
)
from models import RawEvent


def _json_safe(val):
    """Ensure values extracted from PyArrow or native rows are JSON-serializable for RawArchive."""
    if isinstance(val, dict):
        return {k: _json_safe(v) for k, v in val.items()}
    elif isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    elif isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    elif isinstance(val, Decimal):
        f = float(val)
        return f if math.isfinite(f) else str(val)
    elif isinstance(val, (bytes, bytearray)):
        return val.hex()
    return val


def timestamp_ns(value, unit="auto") -> int:
    """Resolve exchange-dependent epoch units without passing through a float.

    Auto requires a plausible 2000–2100 timestamp at exactly one scale. Missing
    or ambiguous timestamps fail, rather than being silently stamped as now.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IntegrityError("CHD exchange timestamp must be a positive integer")
    scales = {"s": 10**9, "ms": 10**6, "us": 1000, "ns": 1}
    if unit != "auto" and unit not in scales:
        raise ValueError("timestamp_unit must be auto, s, ms, us or ns")
    candidates = [
        value * scale
        for name, scale in scales.items()
        if unit in ("auto", name)
        and 946684800 * 10**9 <= value * scale < 4102444800 * 10**9
    ]
    if len(candidates) != 1:
        raise IntegrityError(
            "Cannot resolve CHD timestamp unit; specify timestamp_unit explicitly"
        )
    return candidates[0]


def _number(value, field, *, zero=False) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (not zero and number == 0):
            raise ValueError()
        converted = float(number)
        if not math.isfinite(converted) or (number != 0 and converted == 0):
            raise ValueError()
        return number
    except (InvalidOperation, ValueError, OverflowError):
        raise IntegrityError(
            f"CHD {field} must be a finite {'nonnegative' if zero else 'positive'} number"
        ) from None


def _raw(record, sequence, event_type, exchange_ns, fields, *, first=None):
    p = record.partition
    payload = dict(
        instrument=f"CHD:{p.exchange}:{p.symbol}",
        event_type=event_type,
        exchange_ts=exchange_ns / 10**9,
        sequence=sequence,
        **fields,
    )
    payload["chd"] = dict(
        provider="chd",
        exchange=p.exchange,
        symbol=p.symbol,
        data_type=p.data_type,
        key=p.key,
        file_sha256=record.file_sha256,
        row_index=record.row_index,
        exchange_timestamp_ns=exchange_ns,
        received_timestamp_ns=received_ns(record.values),
        sequence_kind="synthetic_replay_ordinal",
        native=_json_safe(record.values),
    )
    if first is not None:
        payload["chd"]["group_first_raw_id"] = first.raw_id
        payload["chd"]["group_first_key"] = first.partition.key
        payload["chd"]["group_first_row_index"] = first.row_index
    return RawEvent(
        source=f"CHD_{p.exchange}_{p.data_type}",
        payload=payload,
        receive_timestamp=received_ns(record.values) / 10**9,
        raw_id=record.raw_id,
    )


def _check_symbol(record):
    if record.values.get("symbol") != record.partition.symbol:
        raise IntegrityError(
            f"CHD row symbol does not match requested market: {record.partition.key}, row {record.row_index}"
        )


class OrderBook:
    """Reconstruct one exact market; apply complete message groups atomically.

    A message is contiguous rows sharing capture time, event time, event type
    and update IDs. Snapshot clears both sides once. Zero quantity deletes.
    Unknown initial state and explicit exchange sequence gaps stop ingestion.
    """

    def __init__(self):
        self.bids = {}
        self.asks = {}
        self.initialized = False
        self.last_update_id = None

    @staticmethod
    def message_key(record):
        row = record.values
        return tuple(
            row.get(k)
            for k in (
                "received_time",
                "event_time",
                "event_type",
                "first_update_id",
                "final_update_id",
                "prev_final_update_id",
                "last_update_id",
            )
        )

    def apply(self, records):
        first = next(records)
        row = first.values
        kind = row.get("event_type")
        if kind not in ("snapshot", "update"):
            raise IntegrityError("Unknown CHD orderbook event_type")
        final = row.get("final_update_id")
        if final is None:
            final = row.get("last_update_id")
        for key in (
            "first_update_id",
            "final_update_id",
            "prev_final_update_id",
            "last_update_id",
        ):
            val = row.get(key)
            if val is not None and (isinstance(val, bool) or not isinstance(val, int)):
                raise IntegrityError(f"Invalid orderbook {key}")
        if kind == "snapshot":
            self.bids.clear()
            self.asks.clear()
            self.initialized = True
        elif not self.initialized:
            raise IntegrityError(
                "Orderbook starts with updates; request an earlier interval containing a snapshot"
            )
        elif self.last_update_id is not None:
            previous = row.get("prev_final_update_id")
            initial = row.get("first_update_id")
            if previous is not None and previous != self.last_update_id:
                raise IntegrityError(
                    "CHD orderbook sequence gap (prev_final_update_id)"
                )
            if initial is not None and initial > self.last_update_id + 1:
                raise IntegrityError("CHD orderbook sequence gap (first_update_id)")
            if final is not None and final <= self.last_update_id:
                raise IntegrityError("CHD orderbook update IDs regressed or repeated")
        last = first
        for record in itertools.chain((first,), records):
            _check_symbol(record)
            row = record.values
            side = row.get("side")
            if side not in ("bid", "ask"):
                raise IntegrityError("CHD orderbook side must be bid or ask")
            price = _number(row.get("price"), "price")
            quantity = _number(row.get("quantity"), "quantity", zero=True)
            book = self.bids if side == "bid" else self.asks
            if quantity == 0:
                book.pop(price, None)
            else:
                book[price] = quantity
            last = record
        if final is not None:
            self.last_update_id = final
        if not self.bids or not self.asks:
            return first, last, None
        bid, ask = max(self.bids), min(self.asks)
        return (
            first,
            last,
            dict(
                bid=float(bid),
                ask=float(ask),
                bid_size=float(self.bids[bid]),
                ask_size=float(self.asks[ask]),
            ),
        )


def iter_events(
    client: CHDClient,
    request: HistoricalRequest,
    *,
    timestamp_unit="auto",
    book_start=None,
):
    """Yield pipeline-ready trades or BBO quotes, retaining native provenance.

    Synthetic sequence ordinals express replay order only, never exchange gap
    evidence. Raw IDs identify immutable file rows across runs and intervals.
    """
    if request.data_type not in ("trades", "orderbook"):
        raise ValueError(
            "Canonical ingestion supports trades and orderbook; use iter_records/download for other datasets"
        )
    if timestamp_unit not in ("auto", "s", "ms", "us", "ns"):
        raise ValueError("Invalid timestamp_unit")
    read_request = _read_request(request, book_start)
    records = client.iter_records(
        read_request, include_warmup=request.data_type == "orderbook"
    )
    if request.data_type == "trades":
        # Exchange trade IDs are identities, not necessarily contiguous feed
        # sequence numbers. Reuse an ordinal for repeated IDs so MDRAP's
        # bounded duplicate check still works without inventing sequence gaps.
        seen = {}
        sequence = 0
        for record in records:
            _check_symbol(record)
            row = record.values
            trade_id = row.get("trade_id")
            if (
                isinstance(trade_id, bool)
                or not isinstance(trade_id, (int, str))
                or trade_id == ""
            ):
                raise IntegrityError(
                    "CHD trade_id must be an integer or nonempty string"
                )
            if trade_id not in seen:
                sequence += 1
                seen[trade_id] = sequence
                if len(seen) > 200_000:
                    del seen[next(iter(seen))]
            ordinal = seen[trade_id]
            fields = dict(
                price=float(_number(row.get("price"), "price")),
                quantity=float(_number(row.get("quantity"), "quantity")),
            )
            maker = row.get("is_buyer_maker")
            if isinstance(maker, bool):
                fields["side"] = "SELL" if maker else "BUY"
            exchange_ns = timestamp_ns(row.get("trade_time"), timestamp_unit)
            yield _raw(record, ordinal, "TRADE", exchange_ns, fields)
    else:
        book = OrderBook()
        start_ns = (
            int(request.start.timestamp()) * 10**9 + request.start.microsecond * 1000
        )
        sequence = 0
        for _, group in itertools.groupby(records, key=book.message_key):
            group = iter(group)
            head = next(group)
            if (
                not book.initialized
                and head.values.get("event_type") == "update"
                and received_ns(head.values) < start_ns
            ):
                # Only pre-interval warmup may discard unknown state.
                continue
            first, last, quote = book.apply(itertools.chain((head,), group))
            if quote is None or received_ns(last.values) < start_ns:
                continue
            sequence += 1
            value = last.values.get("event_time")
            # REST snapshots can lack an exchange time; the capture clock is
            # the explicit fallback, recorded in the raw payload.
            fallback = (
                value in (None, 0) and last.values.get("event_type") == "snapshot"
            )
            exchange_ns = (
                received_ns(last.values)
                if fallback
                else timestamp_ns(value, timestamp_unit)
            )
            raw = _raw(last, sequence, "QUOTE", exchange_ns, quote, first=first)
            raw.payload["chd"]["timestamp_basis"] = (
                "received_time" if fallback else "event_time"
            )
            yield raw


def _read_request(request, book_start):
    if book_start is None:
        return request
    if request.data_type != "orderbook":
        raise ValueError("book_start is only valid for orderbook ingestion")
    start = utc_datetime(book_start)
    if start > request.start:
        raise ValueError("book_start must be at or before start")
    return HistoricalRequest(
        request.exchange, request.symbol, request.data_type, start, request.end
    )


def ingest_history(
    client: CHDClient,
    request: HistoricalRequest,
    output: str | Path,
    *,
    timestamp_unit="auto",
    book_start=None,
) -> dict:
    """Publish a new run directory only after complete download and ingestion.

    Existing runs are never appended to or overwritten. Failed imports leave
    verified download cache entries for resumption, but no partial database.
    """
    from archive import RawArchive
    from pipeline import Pipeline
    from storage import Store

    if request.data_type not in ("trades", "orderbook"):
        raise ValueError(
            "Ingest supports trades/orderbook; use download for native datasets"
        )
    output = Path(output)
    if output.exists():
        raise FileExistsError(
            f"Historical output already exists: {output}; choose a new run directory"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    read_request = _read_request(request, book_start)
    manifest = client.download(read_request)
    manifest["download_request"] = read_request.as_dict()
    manifest["request"] = request.as_dict()
    manifest["book_start"] = (
        utc_datetime(book_start).isoformat() if book_start is not None else None
    )
    if not any(f["rows"] for f in manifest["files"]):
        raise CHDError("CHD request contains no records; no run was published")
    temporary = Path(tempfile.mkdtemp(prefix=".chd-run-", dir=output.parent))
    try:
        store = Store(str(temporary / "mdrap.db"))
        try:
            with RawArchive(str(temporary / "raw")) as archive:
                pipeline = Pipeline(store, archive=archive)
                count = 0
                for raw in iter_events(
                    client,
                    request,
                    timestamp_unit=timestamp_unit,
                    book_start=book_start,
                ):
                    pipeline.process_one(raw)
                    count += 1
                pipeline.finish()
                if not count:
                    raise CHDError(
                        "No canonical events in requested interval; no run was published"
                    )
                manifest["events"] = count
                manifest["metrics"] = pipeline.metrics.summary()
                manifest["code_version"] = pipeline.code_version
                manifest["timestamp_unit"] = timestamp_unit
                manifest["sequence_kind"] = "synthetic_replay_ordinal"
                manifest["instrument"] = f"CHD:{request.exchange}:{request.symbol}"
                manifest["raw_archive"] = "raw"
                manifest["database"] = "mdrap.db"
        finally:
            store.close()
        (temporary / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        # On supported filesystems, a directory rename publishes the database,
        # raw archive and manifest together. Recheck to avoid replacing a run.
        if output.exists():
            raise FileExistsError(f"Historical output already exists: {output}")
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest
