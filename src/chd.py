"""CryptoHFTData historical provider: verified hourly files and lossless row access.

Transport is standard-library only. Install ``mdrap[chd]`` to decode Parquet.
See docs/CHD.md for interval, timestamp, cache and completeness contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


class _DownloadRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Strictly reject non-HTTPS targets or embedded user/password credentials
        target = urlsplit(newurl)
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username is not None
            or target.password is not None
        ):
            return None

        # Download requests redirect to file storage (S3/Cloudflare); start a fresh HTTPS GET
        # without forwarding origin credentials, tokens, cookies or request bodies.
        if getattr(req, "chd_download", False):
            if req.get_method() != "GET":
                return None
            redirected = Request(
                newurl,
                headers={
                    "User-Agent": "MDRAP-CHD/1",
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            redirected.chd_download = True
            return redirected

        # For metadata endpoints, restrict redirects strictly to the API origin to prevent bearer token leakage
        origin = urlsplit(API_URL)
        if target.hostname.lower() != origin.hostname.lower():
            return None

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def urlopen(request, *, timeout):
    return build_opener(_DownloadRedirects()).open(request, timeout=timeout)


API_URL = "https://api.cryptohftdata.com/v1"
DATA_TYPES = (
    "trades",
    "orderbook",
    "ticker",
    "mark_price",
    "open_interest",
    "liquidations",
)
EXCHANGES = (
    "binance_spot",
    "binance_futures",
    "bybit_spot",
    "bybit",
    "kraken_spot",
    "kraken_derivatives",
    "okx_spot",
    "okx_futures",
    "bitget_spot",
    "bitget_futures",
    "hyperliquid_spot",
    "hyperliquid_futures",
    "lighter",
    "aster_futures",
    "bitmex",
)
UTC = timezone.utc


class CHDError(Exception):
    """Provider failure with a credential-free, actionable message."""


class MissingPartition(CHDError):
    """An hourly file is unavailable; never interpreted as an empty hour."""


class IntegrityError(CHDError):
    """Invalid download, schema, or local cache contents."""


def utc_datetime(value: str | datetime) -> datetime:
    """Parse ISO dates as UTC midnight; require a timezone on full timestamps."""
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            value = datetime.fromisoformat(value).replace(tzinfo=UTC)
        else:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Use a UTC date or a timezone-aware ISO timestamp")
    return value.astimezone(UTC)


def _validate_market(exchange: str, data_type: str | None = None) -> None:
    if exchange not in EXCHANGES:
        raise ValueError(f"Unknown CHD exchange: {exchange}; use historical providers")
    if data_type is not None and data_type not in DATA_TYPES:
        raise ValueError(f"Unknown CHD data type: {data_type}")
    if exchange.endswith("_spot") and data_type in (
        "mark_price",
        "open_interest",
        "liquidations",
    ):
        raise ValueError(f"{data_type} requires a derivatives exchange")


@dataclass(frozen=True)
class HistoricalRequest:
    exchange: str
    symbol: str
    data_type: str
    start: datetime | str
    end: datetime | str

    def __post_init__(self):
        _validate_market(self.exchange, self.data_type)
        # Symbols remain exact, including case and punctuation. Path separators
        # are encoded in the cache filename rather than interpreted as directories.
        if (
            not isinstance(self.symbol, str)
            or not self.symbol
            or any(ord(c) < 32 or c in "\\/" for c in self.symbol)
            or self.symbol in (".", "..")
        ):
            raise ValueError(
                "Use the exact CHD symbol from historical symbols (no path separators)"
            )
        object.__setattr__(self, "start", utc_datetime(self.start))
        object.__setattr__(self, "end", utc_datetime(self.end))
        if self.start >= self.end:
            raise ValueError("start must be before end (exclusive)")

    def partitions(self) -> Iterator[Partition]:
        hour = self.start.replace(minute=0, second=0, microsecond=0)
        while hour < self.end:
            yield Partition(self.exchange, self.symbol, self.data_type, hour)
            hour += timedelta(hours=1)

    def as_dict(self) -> dict:
        return dict(
            exchange=self.exchange,
            symbol=self.symbol,
            data_type=self.data_type,
            start=self.start.isoformat(),
            end=self.end.isoformat(),
            interval="[start, end)",
            time_basis="received_time",
        )


@dataclass(frozen=True)
class Partition:
    exchange: str
    symbol: str
    data_type: str
    hour: datetime

    def __post_init__(self):
        _validate_market(self.exchange, self.data_type)
        # Reuse request validation even for partitions created directly by SDK users.
        hour = utc_datetime(self.hour)
        HistoricalRequest(
            self.exchange, self.symbol, self.data_type, hour, hour + timedelta(hours=1)
        )
        if hour.minute or hour.second or hour.microsecond:
            raise ValueError("Partition hour must be aligned to a UTC hour")
        object.__setattr__(self, "hour", hour)

    @property
    def key(self) -> str:
        return f"{self.exchange}/{self.hour:%Y-%m-%d/%H}/{self.symbol}_{self.data_type}.parquet"


@dataclass(frozen=True)
class HistoricalRecord:
    partition: Partition
    row_index: int
    file_sha256: str
    values: dict

    @property
    def raw_id(self) -> str:
        identity = f"{self.partition.key}\0{self.file_sha256}\0{self.row_index}"
        return "chd-" + hashlib.sha256(identity.encode()).hexdigest()


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        digest = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _parquet():
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise CHDError('Parquet support requires: pip install "mdrap[chd]"') from None
    return pq


def _validate_parquet(path: Path) -> int:
    pq = _parquet()
    try:
        with path.open("rb") as f_obj:
            with pq.ParquetFile(f_obj) as f:
                return f.metadata.num_rows
    except (OSError, ValueError, Exception) as exc:
        raise IntegrityError("Invalid or truncated CHD Parquet file") from exc


def received_ns(row: dict) -> int:
    value = row.get("received_time")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IntegrityError(
            "CHD received_time must be a positive integer in nanoseconds"
        )
    return value


class CHDClient:
    """Serial, bounded-memory REST client with atomic, checksum-verified caching.

    A client belongs to one worker. Anonymous requests are paced at <=60/minute.
    Keys are read from CRYPTOHFTDATA_API_KEY and exchanged for bearer tokens;
    credentials never appear in file paths, manifests or error messages.
    """

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: str | Path = "data/chd-cache",
        *,
        timeout: float = 60,
        max_retries: int = 3,
        offline: bool = False,
    ):
        if not math.isfinite(timeout) or timeout <= 0 or not 0 <= max_retries <= 10:
            raise ValueError("timeout must be positive and retries between 0 and 10")
        self._api_key = (
            api_key
            if api_key is not None
            else os.environ.get("CRYPTOHFTDATA_API_KEY", "")
        )
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.max_retries = max_retries
        self.offline = offline
        self._token = None
        self._token_expires = 0.0
        self._last_request = -math.inf

    def _pace(self):
        if not self._api_key:
            delay = 1.05 - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
        self._last_request = time.monotonic()

    def _authenticate(self):
        if self._api_key and (
            not self._token or time.monotonic() >= self._token_expires
        ):
            result = self._json(
                "/jwt-token",
                auth=False,
                method="POST",
                headers={"X-API-Key": self._api_key},
            )
            token = result.get("jwt_token")
            if not isinstance(token, str) or not token:
                raise CHDError("CHD authentication returned no jwt_token")
            self._token = token
            self._token_expires = time.monotonic() + 3.5 * 3600

    def _request(
        self,
        endpoint,
        params=None,
        *,
        destination=None,
        auth=True,
        method="GET",
        headers=None,
    ):
        if self.offline:
            raise CHDError("Offline mode: requested CHD resource is not cached")
        url = API_URL + endpoint + ("?" + urlencode(params) if params else "")
        refreshed = False
        attempt = 0
        while True:
            if auth:
                self._authenticate()
            request_headers = {
                "User-Agent": "MDRAP-CHD/1",
                "Accept-Encoding": "identity",
                "Cache-Control": "no-cache",
                **(headers or {}),
            }
            if auth and self._token:
                request_headers["Authorization"] = "Bearer " + self._token
            request = Request(url, headers=request_headers, method=method)
            request.chd_download = endpoint == "/download"
            self._pace()
            retry_after = None
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    if destination is None:
                        body = response.read(4 * 1024 * 1024 + 1)
                        if len(body) > 4 * 1024 * 1024:
                            raise CHDError("CHD metadata response exceeds 4 MiB")
                        return body
                    count = 0
                    with destination.open("wb") as target:
                        while chunk := response.read(1024 * 1024):
                            target.write(chunk)
                            count += len(chunk)
                    expected = response.headers.get("Content-Length")
                    expected_bytes = None
                    if expected is not None:
                        try:
                            expected_bytes = int(expected.strip())
                            if expected_bytes < 0:
                                raise ValueError
                        except (ValueError, TypeError):
                            raise IntegrityError(
                                "CHD download returned invalid Content-Length header"
                            )
                    if count == 0 or (
                        expected_bytes is not None and count != expected_bytes
                    ):
                        raise IntegrityError(
                            "CHD download has an empty or incomplete body"
                        )
                    return None
            except HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After")
                exc.close()
                if status == 401 and auth and self._api_key and not refreshed:
                    self._token = None
                    refreshed = True
                    continue
                if status in (401, 403):
                    raise CHDError(
                        f"CHD authentication/access denied (HTTP {status}); check CRYPTOHFTDATA_API_KEY"
                    ) from None
                if status == 404:
                    raise MissingPartition(
                        f"CHD resource unavailable: {(params or {}).get('file', endpoint)}"
                    ) from None
                if status not in (408, 429, 500, 502, 503, 504):
                    raise CHDError(f"CHD request failed (HTTP {status})") from None
                error = f"HTTP {status}"
            except (
                URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
                IntegrityError,
            ):
                error = "connection failure or incomplete response"
            if attempt >= self.max_retries:
                raise CHDError(f"CHD request failed after retries: {error}") from None
            delay = min(2**attempt, 30)
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(
                            delay,
                            parsedate_to_datetime(retry_after).timestamp()
                            - time.time(),
                        )
                    except (ValueError, TypeError):
                        pass
            if not math.isfinite(delay) or delay > 60:
                raise CHDError(
                    "CHD requested a retry delay above 60 seconds; resume later"
                )
            time.sleep(delay)
            attempt += 1

    def _json(self, endpoint, params=None, **kwargs):
        try:
            value = json.loads(self._request(endpoint, params, **kwargs))
        except (ValueError, UnicodeError):
            raise CHDError("CHD returned invalid JSON metadata") from None
        if not isinstance(value, dict):
            raise CHDError("CHD returned invalid metadata structure")
        return value

    def list_symbols(self, exchange: str, data_type: str | None = None) -> list[str]:
        _validate_market(exchange, data_type)
        params = {"exchange": exchange}
        if data_type:
            params["data_type"] = data_type
        result = self._json("/symbols", params)
        symbols = result.get("symbols")
        if not isinstance(symbols, list) or not all(
            isinstance(s, str) and s for s in symbols
        ):
            raise CHDError("CHD returned an invalid symbols list")
        return symbols

    def _cache_paths(self, partition: Partition):
        name = hashlib.sha256(partition.key.encode()).hexdigest()
        base = self.cache_dir / partition.exchange / f"{partition.hour:%Y-%m-%d/%H}"
        return base / (name + ".parquet"), base / (name + ".json")

    def fetch(self, partition: Partition) -> tuple[Path, dict]:
        """Return a validated local Parquet file and its provenance receipt.

        Only successful files are cached. A corrupt cache is an explicit error;
        remove the reported file and its receipt to download it again.
        """
        path, receipt_path = self._cache_paths(partition)
        if path.exists() and receipt_path.exists():
            try:
                receipt = json.loads(receipt_path.read_text())
                valid = (
                    receipt["key"] == partition.key
                    and receipt["bytes"] == path.stat().st_size
                    and receipt["sha256"] == _sha256(path)
                )
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                raise IntegrityError(
                    f"CHD cache integrity failure: {path}; remove file and receipt to retry"
                )
            _validate_parquet(path)
            return path, receipt
        if self.offline:
            raise MissingPartition(f"CHD partition is not cached: {partition.key}")
        _parquet()  # Fail before downloading if the optional dependency is absent.
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".chd-", dir=path.parent, ignore_cleanup_errors=True
        ) as tmp:
            wire = Path(tmp) / "download"
            self._request("/download", {"file": partition.key}, destination=wire)
            wire_hash = _sha256(wire)
            wire_bytes = wire.stat().st_size
            with wire.open("rb") as f:
                magic = f.read(4)
            decoded = wire
            if magic == b"\x28\xb5\x2f\xfd":
                try:
                    import zstandard
                except ImportError:
                    raise CHDError(
                        'Legacy CHD files require: pip install "mdrap[chd]"'
                    ) from None
                decoded = Path(tmp) / "decoded"
                try:
                    with wire.open("rb") as source, decoded.open("wb") as target:
                        zstandard.ZstdDecompressor().copy_stream(source, target)
                except zstandard.ZstdError:
                    raise IntegrityError(
                        f"Invalid Zstandard data: {partition.key}"
                    ) from None
            elif magic != b"PAR1":
                raise IntegrityError(f"CHD did not return Parquet: {partition.key}")
            rows = _validate_parquet(decoded)
            receipt = dict(
                provider="chd",
                key=partition.key,
                sha256=_sha256(decoded),
                bytes=decoded.stat().st_size,
                rows=rows,
                wire_sha256=wire_hash,
                wire_bytes=wire_bytes,
                downloaded_at=datetime.now(UTC).isoformat(),
            )
            metadata = Path(tmp) / "receipt.json"
            metadata.write_text(json.dumps(receipt, indent=2) + "\n")
            os.replace(decoded, path)
            os.replace(metadata, receipt_path)
        return path, receipt

    def download(
        self, request: HistoricalRequest, *, allow_missing: bool = False
    ) -> dict:
        """Download every intersecting hour; report explicit missing partitions."""
        manifest = dict(
            version=1, provider="chd", request=request.as_dict(), files=[], missing=[]
        )
        for partition in request.partitions():
            try:
                path, receipt = self.fetch(partition)
            except MissingPartition:
                if not allow_missing:
                    raise
                manifest["missing"].append(partition.key)
                continue
            manifest["files"].append({**receipt, "path": str(path.resolve())})
        manifest["complete"] = not manifest["missing"]
        return manifest

    def iter_records(
        self,
        request: HistoricalRequest,
        *,
        batch_size: int = 8192,
        include_warmup: bool = False,
    ) -> Iterator[HistoricalRecord]:
        """Read native rows in file order, without sorting/deduplication/coercion.

        Filter on provider receive time, in integer nanoseconds. Warmup includes
        rows before start in the first hour for order-book reconstruction.
        """
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        start = (
            int(request.start.timestamp()) * 10**9 + request.start.microsecond * 1000
        )
        end = int(request.end.timestamp()) * 10**9 + request.end.microsecond * 1000
        pq = _parquet()
        for partition in request.partitions():
            path, receipt = self.fetch(partition)
            try:
                with pq.ParquetFile(path) as f:
                    if "received_time" not in f.schema_arrow.names:
                        raise IntegrityError(
                            f"Missing received_time column: {partition.key}"
                        )
                    index = 0
                    for batch in f.iter_batches(batch_size=batch_size):
                        for row in batch.to_pylist():
                            ts = received_ns(row)
                            if (include_warmup or start <= ts) and ts < end:
                                yield HistoricalRecord(
                                    partition, index, receipt["sha256"], row
                                )
                            index += 1
            except (OSError, ValueError) as exc:
                raise IntegrityError(
                    f"Cannot decode CHD partition: {partition.key}"
                ) from exc


def add_historical_parser(subparsers):
    parser = subparsers.add_parser(
        "historical",
        aliases=["history", "chd"],
        help="CHD historical data: discover, download and ingest",
    )
    actions = parser.add_subparsers(dest="historical_action", required=True)
    providers = actions.add_parser(
        "providers", help="List CHD exchanges and datasets (offline)"
    )
    providers.set_defaults(func=cmd_historical)
    symbols = actions.add_parser(
        "symbols", help="Discover exact historical exchange symbols"
    )
    symbols.add_argument("--exchange", required=True, choices=EXCHANGES)
    symbols.add_argument("--data-type", choices=DATA_TYPES)
    symbols.add_argument("--json", action="store_true", help="Machine-readable output")
    symbols.set_defaults(func=cmd_historical)
    for action in ("plan", "download", "ingest"):
        p = actions.add_parser(
            action,
            help={
                "plan": "Show UTC hourly files without downloading",
                "download": "Cache verified native Parquet files and report completeness",
                "ingest": "Create a new run with canonical SQLite data, raw replay archive and manifest",
            }[action],
        )
        p.add_argument("--exchange", required=True, choices=EXCHANGES)
        p.add_argument(
            "--symbol",
            required=True,
            help="Exact CHD symbol; no automatic currency/market conversion",
        )
        p.add_argument(
            "--data-type",
            default="trades",
            choices=("trades", "orderbook") if action == "ingest" else DATA_TYPES,
        )
        p.add_argument(
            "--start",
            required=True,
            help="Inclusive UTC date or ISO timestamp with timezone",
        )
        p.add_argument(
            "--end",
            required=True,
            help="Exclusive UTC date or ISO timestamp with timezone",
        )
        p.add_argument("--json", action="store_true", help="Machine-readable output")
        if action != "plan":
            p.add_argument("--cache-dir", default="data/chd-cache")
            p.add_argument(
                "--offline",
                action="store_true",
                help="Require verified cached files; no network requests",
            )
            p.add_argument("--timeout", type=float, default=60)
            p.add_argument("--retries", type=int, default=3)
        if action == "download":
            p.add_argument(
                "--allow-missing",
                action="store_true",
                help="Report 404 hours as gaps; never marks a partial download complete",
            )
            p.add_argument("--manifest", help="Write manifest to a new JSON file")
        if action == "ingest":
            p.add_argument(
                "--output",
                required=True,
                help="New run directory (existing paths are refused)",
            )
            p.add_argument(
                "--book-start",
                help="Read earlier hours for orderbook snapshot warmup; output still begins at --start",
            )
            p.add_argument(
                "--timestamp-unit",
                choices=("auto", "s", "ms", "us", "ns"),
                default="auto",
            )
        p.set_defaults(func=cmd_historical)
    return parser


def cmd_historical(args):
    try:
        _run_historical(args)
    except (CHDError, ValueError, OSError) as exc:
        print(f"[CHD] {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def _run_historical(args):
    action = args.historical_action
    if action == "providers":
        from term import Console, Table

        table = Table(title="CryptoHFTData · Historical market data")
        table.add_column("Exchange / market")
        table.add_column("Native datasets")
        for exchange in EXCHANGES:
            types = DATA_TYPES[:3] if exchange.endswith("_spot") else DATA_TYPES
            table.add_row(exchange, ", ".join(types))
        Console().print(table)
        print(
            "Discover symbols to check dataset coverage. UTC intervals use [start, end)."
        )
        print(
            'Optional credentials: CRYPTOHFTDATA_API_KEY. Install: pip install "mdrap[chd]"'
        )
        return
    client = CHDClient(
        cache_dir=getattr(args, "cache_dir", "data/chd-cache"),
        timeout=getattr(args, "timeout", 60),
        max_retries=getattr(args, "retries", 3),
        offline=getattr(args, "offline", False),
    )
    if action == "symbols":
        values = client.list_symbols(args.exchange, args.data_type)
        if args.json:
            print(
                json.dumps(
                    dict(
                        exchange=args.exchange,
                        data_type=args.data_type,
                        count=len(values),
                        symbols=values,
                    ),
                    indent=2,
                )
            )
        else:
            print(f"CHD · {args.exchange} · {len(values):,} historical symbols")
            print("\n".join(values))
        return
    request = HistoricalRequest(
        args.exchange, args.symbol, args.data_type, args.start, args.end
    )
    if action == "plan":
        result = dict(
            provider="chd",
            request=request.as_dict(),
            files=[p.key for p in request.partitions()],
        )
        result["hours"] = len(result["files"])
    elif action == "download":
        if args.manifest and Path(args.manifest).exists():
            raise FileExistsError(f"Manifest already exists: {args.manifest}")
        result = client.download(request, allow_missing=args.allow_missing)
        if args.manifest:
            with open(args.manifest, "x", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
                f.write("\n")
    else:
        from chd_history import ingest_history

        if not args.json:
            print(
                f"CHD · {args.exchange} / {args.symbol} · {args.data_type}",
                file=sys.stderr,
            )
            print(
                "Verifying hourly files and building historical run…", file=sys.stderr
            )
        result = ingest_history(
            client,
            request,
            args.output,
            timestamp_unit=args.timestamp_unit,
            book_start=args.book_start,
        )
    if args.json:
        print(json.dumps(result, indent=2))
    elif action == "plan":
        print(f"CHD · {result['hours']:,} hourly files · no downloads made")
        print("\n".join(result["files"]))
    else:
        from term import Console, Table

        table = Table(
            title="CHD · Historical import"
            if action == "ingest"
            else "CHD · Historical download"
        )
        table.add_column("Measure")
        table.add_column("Result")
        table.add_row("Market", f"{request.exchange} / {request.symbol}")
        table.add_row("Dataset", request.data_type)
        table.add_row(
            "UTC interval",
            f"{request.start.isoformat()} → {request.end.isoformat()} (exclusive)",
        )
        table.add_row("Coverage", "Complete" if result["complete"] else "INCOMPLETE")
        table.add_row("Verified hours", str(len(result["files"])))
        table.add_row("Missing hours", str(len(result["missing"])))
        table.add_row(
            "Native rows in files", f"{sum(f['rows'] for f in result['files']):,}"
        )
        if action == "ingest":
            table.add_row("Pipeline events", f"{result['events']:,}")
            table.add_row("Output", str(Path(args.output).resolve()))
        Console().print(table)
        for key in result["missing"]:
            print(f"MISSING {key}")
        if action == "ingest":
            print(f"Database: {Path(args.output) / 'mdrap.db'}")
            print(f"Replay archive: {Path(args.output) / 'raw'}")
            print(f"Provenance: {Path(args.output) / 'manifest.json'}")
    if result.get("complete") is False:
        raise SystemExit(2)
