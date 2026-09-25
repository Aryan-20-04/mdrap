"""Deterministic CHD contracts using real Parquet bytes and mocked HTTP."""

import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chd import (
    CHDClient,
    CHDError,
    HistoricalRequest,
    IntegrityError,
    MissingPartition,
    HistoricalRecord,
    Partition,
    received_ns,
)
from chd_history import ingest_history, iter_events, timestamp_ns

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")
BASE = 1754078400


def request(
    data_type="trades", start="2025-08-01T20:00:00Z", end="2025-08-01T21:00:00Z"
):
    return HistoricalRequest("binance_spot", "BTCUSDT", data_type, start, end)


def trade(offset=0, **updates):
    row = dict(
        received_time=BASE * 10**9 + offset,
        event_time=BASE * 1000,
        trade_time=BASE * 1000,
        trade_id=42,
        symbol="BTCUSDT",
        price="113328.68000000",
        quantity="0.00156000",
        is_buyer_maker=False,
        order_type="MARKET",
    )
    row.update(updates)
    return row


def level(offset=0, kind="snapshot", side="bid", price="100", quantity="2", **updates):
    row = dict(
        received_time=BASE * 10**9 + offset,
        event_time=BASE * 1000 + offset // 10**6,
        symbol="BTCUSDT",
        event_type=kind,
        side=side,
        price=price,
        quantity=quantity,
        final_update_id=10,
        first_update_id=None,
        prev_final_update_id=None,
        last_update_id=None,
    )
    row.update(updates)
    return row


def parquet(rows):
    buf = io.BytesIO()
    pq.write_table(
        pa.Table.from_pylist(rows), buf, compression="zstd", row_group_size=2
    )
    return buf.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body)), **(headers or {})}


def http_error(code, headers=None):
    return HTTPError(
        "https://api.cryptohftdata.com/v1/download", code, "error", headers or {}, None
    )


@pytest.fixture
def network(monkeypatch):
    calls = []
    replies = []

    def open_request(req, timeout):
        calls.append(req)
        response = replies.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr("chd.urlopen", open_request)
    monkeypatch.setattr("chd.time.sleep", lambda _: None)
    monkeypatch.delenv("CRYPTOHFTDATA_API_KEY", raising=False)
    return calls, replies


def client_with_rows(tmp_path, network, rows, data_type="trades"):
    calls, replies = network
    replies.append(Response(parquet(rows)))
    c = CHDClient(cache_dir=tmp_path / "cache")
    c.fetch(next(request(data_type).partitions()))
    return c


def test_utc_plan_exact_exclusive_boundary():
    r = request(start="2025-08-01T23:30:00+02:00", end="2025-08-02T01:00:00+02:00")
    assert [p.key for p in r.partitions()] == [
        "binance_spot/2025-08-01/21/BTCUSDT_trades.parquet",
        "binance_spot/2025-08-01/22/BTCUSDT_trades.parquet",
    ]
    assert len(list(request(start="2025-08-01", end="2025-08-02").partitions())) == 24


@pytest.mark.parametrize(
    "updates",
    [
        dict(start="2025-08-01T20:00:00"),
        dict(end="2025-08-01"),
        dict(exchange="binance"),
        dict(symbol="../BTC"),
        dict(symbol="BTC/USD"),
        dict(data_type="mark_price"),
        dict(symbol=""),
        dict(symbol="a\nb"),
    ],
)
def test_invalid_request(updates):
    values = dict(
        exchange="binance_spot",
        symbol="BTCUSDT",
        data_type="trades",
        start="2025-08-01",
        end="2025-08-02",
    )
    values.update(updates)
    with pytest.raises(ValueError):
        HistoricalRequest(**values)


def test_partition_validation():
    with pytest.raises(ValueError):
        Partition("binance_spot", "BTC", "trades", "2025-08-01T20:01:00Z")


@pytest.mark.parametrize("compressed", [False, True])
def test_download_cache_integrity_and_lossless_rows(tmp_path, network, compressed):
    calls, replies = network
    body = parquet([trade(), trade(1000)])
    if compressed:
        zstd = pytest.importorskip("zstandard")
        body = zstd.ZstdCompressor().compress(body)
    replies.append(Response(body))
    c = CHDClient(cache_dir=tmp_path)
    p = next(request().partitions())
    path, meta = c.fetch(p)
    assert path.read_bytes().startswith(b"PAR1")
    assert meta["rows"] == 2
    assert parse_qs(urlparse(calls[0].full_url).query)["file"] == [p.key]
    c.offline = True
    assert c.fetch(p)[1] == meta
    records = list(c.iter_records(request(), batch_size=1))
    assert [r.row_index for r in records] == [0, 1]
    assert records[0].values["price"] == "113328.68000000"
    assert records[0].values["received_time"] == BASE * 10**9
    assert len(calls) == 1
    path.write_bytes(b"corruption")
    with pytest.raises(IntegrityError, match="integrity"):
        c.fetch(p)


def test_exact_nanosecond_interval_and_stable_identity(tmp_path, network):
    c = client_with_rows(
        tmp_path, network, [trade(999), trade(1000), trade(1999), trade(2000)]
    )
    r = request(start="2025-08-01T20:00:00.000001Z", end="2025-08-01T20:00:00.000002Z")
    narrow = list(c.iter_records(r))
    full = list(c.iter_records(request()))
    assert [x.row_index for x in narrow] == [1, 2]
    assert narrow[0].raw_id == full[1].raw_id


def test_jwt_header_refresh_and_no_credential_leak(tmp_path, network):
    calls, replies = network
    replies.extend(
        [
            Response(b'{"jwt_token":"first"}'),
            http_error(401),
            Response(b'{"jwt_token":"second"}'),
            Response(parquet([trade()])),
        ]
    )
    c = CHDClient("secret-key", tmp_path)
    _, meta = c.fetch(next(request().partitions()))
    assert calls[0].method == "POST"
    assert calls[0].get_header("X-api-key") == "secret-key"
    assert calls[1].get_header("Authorization") == "Bearer first"
    assert calls[3].get_header("Authorization") == "Bearer second"
    assert all("secret-key" not in req.full_url for req in calls)
    assert "secret-key" not in json.dumps(meta)
    assert "second" not in json.dumps(meta)


@pytest.mark.parametrize("code", [401, 403])
def test_auth_failure_does_not_fall_back_to_anonymous(tmp_path, network, code):
    calls, replies = network
    replies.append(http_error(code))
    with pytest.raises(CHDError, match="access denied"):
        CHDClient("secret", tmp_path).fetch(next(request().partitions()))
    assert len(calls) == 1
    assert not list(tmp_path.rglob("*.parquet"))


def test_retries_rate_limit_server_and_network(tmp_path, network):
    calls, replies = network
    replies.extend(
        [
            http_error(429, {"Retry-After": "2"}),
            http_error(503),
            URLError("secret from remote"),
            Response(parquet([trade()])),
        ]
    )
    with patch("chd.time.sleep") as sleep:
        CHDClient(cache_dir=tmp_path).fetch(next(request().partitions()))
    assert len(calls) == 4
    assert any(call.args == (2,) for call in sleep.call_args_list)


def test_retry_exhaustion_safe_and_no_cache(tmp_path, network):
    calls, replies = network
    replies.extend([URLError("private URL secret")] * 2)
    with pytest.raises(CHDError) as error:
        CHDClient(cache_dir=tmp_path, max_retries=1).fetch(next(request().partitions()))
    assert "private" not in str(error.value)
    assert len(calls) == 2
    assert not list(tmp_path.rglob("*.parquet"))


def test_missing_hours_are_never_success(tmp_path, network):
    calls, replies = network
    replies.extend([http_error(404), http_error(404)])
    c = CHDClient(cache_dir=tmp_path)
    with pytest.raises(MissingPartition):
        c.download(request())
    result = c.download(request(), allow_missing=True)
    assert result["complete"] is False
    assert result["missing"] == [next(request().partitions()).key]
    assert result["files"] == []


@pytest.mark.parametrize(
    "body", [b"", b"<html>error</html>", b"PAR1brokenPAR1", b"\x28\xb5\x2f\xfdbroken"]
)
def test_corrupt_download_never_committed(tmp_path, network, body):
    calls, replies = network
    replies.append(Response(body))
    with pytest.raises(CHDError):
        CHDClient(cache_dir=tmp_path, max_retries=0).fetch(next(request().partitions()))
    assert not list(tmp_path.rglob("*.parquet"))


def test_truncation_retried(tmp_path, network):
    calls, replies = network
    body = parquet([trade()])
    replies.extend(
        [Response(body, {"Content-Length": str(len(body) + 1)}), Response(body)]
    )
    CHDClient(cache_dir=tmp_path).fetch(next(request().partitions()))
    assert len(calls) == 2


def test_offline_never_accesses_network(tmp_path, network):
    c = CHDClient(cache_dir=tmp_path, offline=True)
    with pytest.raises(MissingPartition):
        c.download(request())
    with pytest.raises(CHDError, match="Offline"):
        c.list_symbols("binance_spot")
    assert not network[0]


def test_symbol_discovery_and_schema_validation(tmp_path, network):
    calls, replies = network
    replies.extend(
        [Response(b'{"symbols":["BTCUSDT","DELISTED"]}'), Response(b'{"symbols":null}')]
    )
    c = CHDClient(cache_dir=tmp_path)
    assert c.list_symbols("binance_spot", "trades") == ["BTCUSDT", "DELISTED"]
    with pytest.raises(CHDError):
        c.list_symbols("binance_spot")


@pytest.mark.parametrize("scale", [1, 1000, 10**6, 10**9])
def test_exchange_timestamp_scales(scale):
    assert timestamp_ns(BASE * scale) == BASE * 10**9


@pytest.mark.parametrize("value", [True, None, 0, 123, "1754078400", float("nan")])
def test_timestamp_rejects_invalid_values(value):
    with pytest.raises(IntegrityError):
        timestamp_ns(value)


def test_trade_mapping_preserves_capture_time_and_market(tmp_path, network):
    c = client_with_rows(tmp_path, network, [trade(123456789)])
    raw = next(iter_events(c, request()))
    assert raw.source == "CHD_binance_spot_trades"
    assert raw.payload["instrument"] == "CHD:binance_spot:BTCUSDT"
    assert raw.payload["exchange_ts"] == BASE
    assert raw.receive_timestamp == (BASE * 10**9 + 123456789) / 10**9
    assert raw.payload["chd"]["received_timestamp_ns"] == BASE * 10**9 + 123456789
    assert raw.payload["side"] == "BUY"
    assert raw.payload["chd"]["native"]["trade_id"] == 42


@pytest.mark.parametrize(
    "updates",
    [
        dict(price="NaN"),
        dict(quantity="0"),
        dict(price="Infinity"),
        dict(price="1e-1000"),
        dict(symbol="ETHUSDT"),
    ],
)
def test_bad_trade_fails_explicitly(tmp_path, network, updates):
    c = client_with_rows(tmp_path, network, [trade(**updates)])
    with pytest.raises(IntegrityError):
        list(iter_events(c, request()))


def book_rows():
    return [
        level(),
        level(side="ask", price="102"),
        level(
            10**9, "update", price="101", final_update_id=11, prev_final_update_id=10
        ),
        level(
            2 * 10**9,
            "update",
            price="101",
            quantity="0",
            final_update_id=12,
            prev_final_update_id=11,
        ),
        level(3 * 10**9, price="90", final_update_id=100),
        level(3 * 10**9, side="ask", price="91", final_update_id=100),
    ]


def test_orderbook_atomic_snapshot_update_delete_and_reset(tmp_path, network):
    c = client_with_rows(tmp_path, network, book_rows(), "orderbook")
    events = list(iter_events(c, request("orderbook")))
    assert [(e.payload["bid"], e.payload["ask"]) for e in events] == [
        (100, 102),
        (101, 102),
        (100, 102),
        (90, 91),
    ]
    assert [e.payload["sequence"] for e in events] == [1, 2, 3, 4]
    assert events[0].payload["chd"]["group_first_row_index"] == 0
    assert events[0].payload["chd"]["row_index"] == 1


def test_orderbook_warmup_before_requested_start(tmp_path, network):
    c = client_with_rows(tmp_path, network, book_rows(), "orderbook")
    r = request("orderbook", start="2025-08-01T20:00:01Z")
    events = list(iter_events(c, r))
    assert len(events) == 3
    assert events[0].payload["bid"] == 101


@pytest.mark.parametrize(
    "rows",
    [
        [level(kind="update")],
        [
            level(),
            level(side="ask", price="102"),
            level(10**9, "update", prev_final_update_id=9, final_update_id=11),
        ],
        [
            level(),
            level(side="ask", price="102"),
            level(10**9, "update", first_update_id=15, final_update_id=16),
        ],
        [level(side="buy")],
    ],
)
def test_orderbook_unknown_state_gap_or_invalid_side_rejected(tmp_path, network, rows):
    c = client_with_rows(tmp_path, network, rows, "orderbook")
    with pytest.raises(IntegrityError):
        list(iter_events(c, request("orderbook")))


def test_pipeline_import_raw_replay_lineage_and_no_false_age(tmp_path, network):
    c = client_with_rows(tmp_path, network, [trade(1000), trade(2000, trade_id=99999)])
    out = tmp_path / "run"
    manifest = ingest_history(c, request(), out)
    from storage import Store
    from archive import replay
    from pipeline import Pipeline

    original = Store(str(out / "mdrap.db"))
    assert sum(original.counts().values()) == 2
    rows = original.conn.execute(
        "SELECT source, reasons FROM canonical_events"
    ).fetchall()
    assert all("STALE" not in row[1] and "SEQUENCE_GAP" not in row[1] for row in rows)
    assert original.conn.execute("SELECT COUNT(*) FROM lineage").fetchone()[0] == 2
    original.close()
    raws = list(replay(str(out / "raw")))
    assert len(raws) == 2
    assert raws[0].payload["chd"]["native"]["price"] == "113328.68000000"
    replayed = Store(str(tmp_path / "replayed.db"))
    pipeline = Pipeline(replayed)
    for raw in raws:
        pipeline.process_one(raw)
    pipeline.finish()
    assert sum(replayed.counts().values()) == 2
    replayed.close()
    assert manifest["complete"] and manifest["events"] == 2
    with pytest.raises(FileExistsError):
        ingest_history(c, request(), out)


def test_failed_ingest_publishes_nothing_but_keeps_cache(tmp_path, network):
    c = client_with_rows(tmp_path, network, [trade(), trade(quantity="bad")])
    with pytest.raises(IntegrityError):
        ingest_history(c, request(), tmp_path / "run")
    assert not (tmp_path / "run").exists()
    assert not list(tmp_path.glob(".chd-run-*"))
    assert list((tmp_path / "cache").rglob("*.parquet"))


def test_native_only_datasets_not_misrepresented_as_trades(tmp_path):
    c = CHDClient(cache_dir=tmp_path, offline=True)
    r = request("ticker")
    with pytest.raises(ValueError, match="Canonical"):
        list(iter_events(c, r))


def test_cli_plan_alias_json_and_validation(capsys):
    from cli import build_parser

    p = build_parser()
    args = p.parse_args(
        [
            "chd",
            "plan",
            "--exchange",
            "binance_spot",
            "--symbol",
            "BTCUSDT",
            "--start",
            "2025-08-01",
            "--end",
            "2025-08-02",
            "--json",
        ]
    )
    args.func(args)
    assert json.loads(capsys.readouterr().out)["hours"] == 24
    args.end = args.start
    with pytest.raises(SystemExit) as err:
        args.func(args)
    assert err.value.code == 1


def test_cli_download_partial_json_exit_two(tmp_path, network, capsys):
    from cli import build_parser

    network[1].append(http_error(404))
    args = build_parser().parse_args(
        [
            "historical",
            "download",
            "--exchange",
            "binance_spot",
            "--symbol",
            "BTCUSDT",
            "--start",
            "2025-08-01T20:00:00Z",
            "--end",
            "2025-08-01T21:00:00Z",
            "--cache-dir",
            str(tmp_path),
            "--allow-missing",
            "--json",
        ]
    )
    with pytest.raises(SystemExit) as err:
        args.func(args)
    assert err.value.code == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False


def test_duplicate_trade_ids_quarantined_without_artificial_gaps(tmp_path, network):
    c = client_with_rows(tmp_path, network, [trade(), trade(1), trade(2, trade_id=200)])
    out = tmp_path / "duplicates"
    manifest = ingest_history(c, request(), out)
    assert manifest["metrics"]["quality_counts"]["INVALID"] == 1
    from storage import Store

    store = Store(str(out / "mdrap.db"))
    assert sum(store.counts().values()) == 2
    reasons = [row[0] for row in store.conn.execute("SELECT reasons FROM quarantine")]
    assert any("DUPLICATE" in reason for reason in reasons)
    assert all("SEQUENCE_GAP" not in reason for reason in reasons)
    store.close()


def test_warmup_skips_unknown_preinterval_state_only(tmp_path, network):
    rows = [
        level(kind="update", final_update_id=1),
        level(10**9),
        level(10**9, side="ask", price="102"),
        level(2 * 10**9, "update", final_update_id=11, prev_final_update_id=10),
    ]
    c = client_with_rows(tmp_path, network, rows, "orderbook")
    r = request("orderbook", start="2025-08-01T20:00:02Z")
    events = list(iter_events(c, r, book_start="2025-08-01T20:00:00Z"))
    assert len(events) == 1
    with pytest.raises(ValueError, match="at or before"):
        list(iter_events(c, r, book_start="2025-08-01T20:30:00Z"))
    with pytest.raises(ValueError, match="only valid"):
        list(iter_events(c, request(), book_start="2025-08-01"))


def test_cross_hour_book_continuity(tmp_path, network):
    calls, replies = network
    replies.extend(
        [
            Response(parquet([level(), level(side="ask", price="102")])),
            Response(
                parquet(
                    [
                        level(
                            3600 * 10**9,
                            "update",
                            price="101",
                            final_update_id=11,
                            prev_final_update_id=10,
                        )
                    ]
                )
            ),
        ]
    )
    c = CHDClient(cache_dir=tmp_path / "cache")
    r = request("orderbook", start="2025-08-01T21:00:00Z", end="2025-08-01T22:00:00Z")
    out = tmp_path / "run"
    manifest = ingest_history(c, r, out, book_start="2025-08-01T20:00:00Z")
    assert manifest["events"] == 1
    assert len(manifest["files"]) == 2
    assert manifest["book_start"] == "2025-08-01T20:00:00+00:00"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "data_type", ["ticker", "mark_price", "open_interest", "liquidations"]
)
def test_native_research_datasets_preserve_nulls_and_extra_fields(
    tmp_path, network, data_type
):
    calls, replies = network
    row = dict(
        received_time=BASE * 10**9,
        symbol="BTCUSDT",
        funding_rate="0.0000123456789",
        index_price=None,
        timestamp=BASE * 1000,
        venue_extension="preserve me",
    )
    replies.append(Response(parquet([row, row])))
    c = CHDClient(cache_dir=tmp_path)
    r = HistoricalRequest(
        "binance_futures",
        "BTCUSDT",
        data_type,
        "2025-08-01T20:00:00Z",
        "2025-08-01T21:00:00Z",
    )
    records = list(c.iter_records(r, batch_size=1))
    assert [record.values for record in records] == [row, row]
    assert records[0].raw_id != records[1].raw_id


def test_missing_receive_column_and_invalid_received_time(tmp_path, network):
    c = client_with_rows(tmp_path, network, [{"symbol": "BTCUSDT"}])
    with pytest.raises(IntegrityError, match="Missing received_time"):
        list(c.iter_records(request()))
    with pytest.raises(IntegrityError):
        received_ns({"received_time": True})


def test_http_redirects_never_forward_credentials():
    from chd import _DownloadRedirects
    from urllib.request import Request

    req = Request(
        "https://api.cryptohftdata.com/v1/jwt-token", headers={"X-API-Key": "secret"}
    )
    assert (
        _DownloadRedirects().redirect_request(
            req, None, 302, "", {}, "https://another.invalid"
        )
        is None
    )


def test_long_retry_after_stops_without_sleeping(tmp_path, network):
    network[1].append(http_error(429, {"Retry-After": "3600"}))
    with patch("chd.time.sleep") as sleep:
        with pytest.raises(CHDError, match="resume later"):
            CHDClient(cache_dir=tmp_path).fetch(next(request().partitions()))
    sleep.assert_not_called()


def test_empty_filtered_interval_never_publishes(tmp_path, network):
    c = client_with_rows(tmp_path, network, [trade()])
    r = request(start="2025-08-01T20:30:00Z")
    with pytest.raises(CHDError, match="No canonical events"):
        ingest_history(c, r, tmp_path / "empty")
    assert not (tmp_path / "empty").exists()


def test_download_redirects_follow_https_without_origin_credentials():
    from chd import _DownloadRedirects
    from urllib.request import Request

    original = Request(
        "https://api.cryptohftdata.com/v1/download",
        headers={
            "Authorization": "Bearer secret",
            "X-API-Key": "key",
            "Cookie": "session=secret",
        },
    )
    original.chd_download = True
    handler = _DownloadRedirects()
    target = "https://storage.example/file.parquet?signature=opaque"
    redirected = handler.redirect_request(original, None, 302, "", {}, target)
    assert redirected.full_url == target
    assert redirected.get_method() == "GET"
    assert redirected.data is None
    assert redirected.chd_download
    assert not any(
        name.lower() in ("authorization", "x-api-key", "cookie")
        for name, _ in redirected.header_items()
    )
    assert handler.redirect_request(redirected, None, 302, "", {}, target).chd_download


@pytest.mark.parametrize(
    "target",
    [
        "http://storage.example/file",
        "https://user:password@storage.example/file",
        "file:///tmp/file",
        "https:///file",
    ],
)
def test_download_redirects_reject_unsafe_targets(target):
    from chd import _DownloadRedirects
    from urllib.request import Request

    original = Request("https://api.cryptohftdata.com/v1/download")
    original.chd_download = True
    assert (
        _DownloadRedirects().redirect_request(original, None, 302, "", {}, target)
        is None
    )


def test_metadata_redirects_restrict_to_origin():
    from chd import _DownloadRedirects
    from urllib.request import Request

    handler = _DownloadRedirects()
    original = Request(
        "https://api.cryptohftdata.com/v1/symbols",
        headers={"Authorization": "Bearer secret"},
    )
    # Cross-origin redirect must be rejected to prevent token leakage
    assert (
        handler.redirect_request(
            original, None, 302, "", {}, "https://malicious.com/api"
        )
        is None
    )
    # HTTP redirect must be rejected
    assert (
        handler.redirect_request(
            original, None, 302, "", {}, "http://api.cryptohftdata.com/v1/symbols"
        )
        is None
    )


def test_malformed_content_length_handled(tmp_path):
    # First response has a malformed Content-Length; second response is valid
    calls, replies = (
        [],
        [
            Response(b"PAR1bad", headers={"Content-Length": "invalid_string"}),
            Response(b"PAR1good", headers={"Content-Length": "8"}),
        ],
    )

    def open_request(req, timeout):
        calls.append(req)
        return replies.pop(0)

    c = CHDClient(cache_dir=tmp_path, max_retries=2)
    dest = tmp_path / "download.tmp"
    with patch("chd.urlopen", side_effect=open_request):
        # Should catch invalid Content-Length as IntegrityError, retry, and succeed on second attempt
        c._request("/download", destination=dest)
    assert dest.read_bytes() == b"PAR1good"
    assert len(calls) == 2


def test_raw_native_is_json_serializable():
    from datetime import datetime, timezone
    from decimal import Decimal
    from chd_history import _raw
    import json

    rec = HistoricalRecord(
        Partition(
            "binance_spot",
            "BTCUSDT",
            "trades",
            datetime(2025, 8, 1, 20, 0, tzinfo=timezone.utc),
        ),
        0,
        "hash123",
        {
            "received_time": 1000000000,
            "time": datetime(2025, 8, 1, 20, 0),
            "dec": Decimal("123.45"),
            "raw_bytes": b"abc",
        },
    )
    event = _raw(rec, 1, "TRADE", 1000000000, {"price": 100.0, "quantity": 1.0})
    dumped = json.dumps(event.payload)
    assert "123.45" in dumped
    assert "616263" in dumped


def test_orderbook_gap_detection_preserves_last_update_id_across_idless_messages():
    from datetime import datetime, timezone
    from chd_history import OrderBook

    book = OrderBook()
    # 1. Initial snapshot with final_update_id = 10
    msg1 = [
        HistoricalRecord(
            Partition(
                "binance_spot",
                "BTCUSDT",
                "orderbook",
                datetime(2025, 8, 1, 20, 0, tzinfo=timezone.utc),
            ),
            0,
            "h",
            {
                "received_time": 1,
                "event_type": "snapshot",
                "side": "bid",
                "price": "100",
                "quantity": "1",
                "final_update_id": 10,
                "symbol": "BTCUSDT",
            },
        )
    ]
    book.apply(iter(msg1))
    assert book.last_update_id == 10

    # 2. Intermediate update without update IDs (e.g. heartbeat or partial)
    msg2 = [
        HistoricalRecord(
            Partition(
                "binance_spot",
                "BTCUSDT",
                "orderbook",
                datetime(2025, 8, 1, 20, 0, tzinfo=timezone.utc),
            ),
            1,
            "h",
            {
                "received_time": 2,
                "event_type": "update",
                "side": "bid",
                "price": "101",
                "quantity": "1",
                "symbol": "BTCUSDT",
            },
        )
    ]
    book.apply(iter(msg2))
    # Crucial fix: last_update_id must NOT be wiped to None!
    assert book.last_update_id == 10

    # 3. Third message arrives with a gap: first_update_id = 15 (> 10 + 1)
    msg3 = [
        HistoricalRecord(
            Partition(
                "binance_spot",
                "BTCUSDT",
                "orderbook",
                datetime(2025, 8, 1, 20, 0, tzinfo=timezone.utc),
            ),
            2,
            "h",
            {
                "received_time": 3,
                "event_type": "update",
                "side": "bid",
                "price": "102",
                "quantity": "1",
                "first_update_id": 15,
                "final_update_id": 15,
                "symbol": "BTCUSDT",
            },
        )
    ]
    with pytest.raises(IntegrityError, match="sequence gap"):
        book.apply(iter(msg3))
