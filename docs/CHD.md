# CryptoHFTData historical data

MDRAP integrates CHD through its REST API. Use `mdrap historical` (aliases
`history` and `chd`) to discover markets, plan UTC intervals, cache native files,
and create reproducible historical runs. This provider is separate from live
feeds and never enters the live supervisor's lossy queue.

## Quick start

```bash
pip install -e '.[chd]'

# No credentials needed for public access. For authenticated access, set
# CRYPTOHFTDATA_API_KEY in your environment or secret manager.
mdrap historical providers
mdrap historical symbols --exchange binance_spot --data-type trades

# Inspect the file plan without making any network requests.
mdrap historical plan --exchange binance_spot --symbol BTCUSDT \
  --start 2025-08-01T20:00:00Z --end 2025-08-01T20:01:00Z

# Create a new run, including canonical data, lineage, quarantine, and replay.
mdrap historical ingest --exchange binance_spot --symbol BTCUSDT \
  --start 2025-08-01T20:00:00Z --end 2025-08-01T20:01:00Z \
  --output data/chd-runs/btc-minute

mdrap query --db data/chd-runs/btc-minute/mdrap.db \
  --latest CHD:binance_spot:BTCUSDT
mdrap replay --base-dir data/chd-runs/btc-minute/raw \
  --db data/chd-runs/btc-minute-replay.db
```

CHD currently documents anonymous access at 60 requests/minute per IP. MDRAP
paces anonymous requests and respects rate limiting. Authenticated access
exchanges the API key for a short-lived JWT, refreshes it on expiry, and sends
credentials only in headers. Errors and manifests exclude credentials.
Redirects are refused to avoid forwarding credentials to another destination.
See [CHD authentication](https://www.cryptohftdata.com/docs/rest-authentication).

## Datasets and semantics

| Native dataset | Download / Python records | Canonical ingestion |
| --- | --- | --- |
| `trades` | Every native field, including original decimal strings and trade IDs | `TRADE` with execution time, price, quantity and capture time |
| `orderbook` | Full L2 snapshots and price-level updates | Reconstructed `QUOTE` after each complete message group |
| `ticker` | Native rolling statistics and nullable update fields | Native access only |
| `mark_price` | Mark/index prices, funding rates and settlement fields | Native access only |
| `open_interest` | Native observations, including repeated timestamps | Native access only |
| `liquidations` | Native liquidation orders and execution fields | Native access only |

MDRAP's canonical model represents trades and quotes. Ticker statistics are
not fresh trades or BBO quotes; funding and liquidation records remain native
research data. No deduplication, forward filling, sorting or float conversion
is performed by `iter_records`. Use the cached Parquet files directly with
PyArrow or DuckDB for these datasets.

Use exact symbols from discovery. Instruments are namespaced, for example
`CHD:binance_spot:BTCUSDT` and `CHD:binance_futures:BTCUSDT`, so spot, perpetuals,
USD and USDT cannot silently merge. Sources include the dataset, for example
`CHD_binance_spot_trades`. `kraken_derivatives` is the SDK/storage identifier
(also verified against the live symbols endpoint); one REST documentation page
instead lists `kraken_futures`.

### Time and sequence rules

- Intervals are **[start, end)**, filtered on CHD's `received_time` in integer
  nanoseconds. A date means midnight UTC; timestamps must include a timezone.
  `--start 2025-08-01 --end 2025-08-02` selects one day.
- All intersecting hourly files are downloaded. A one-minute request still
  downloads its entire containing hour. Download manifests report full-file
  row counts; ingestion reports the actual derived event count separately.
- Original capture time becomes MDRAP receive time. Staleness measures the
  original collection delay, not time elapsed since collection. The existing
  default 50 ms quality threshold can flag CHD records as suspicious; those
  records remain queryable and also appear in quarantine.
- Trades use `trade_time`; quotes use `event_time`. `--timestamp-unit auto`
  resolves epoch seconds/milliseconds/microseconds/nanoseconds only when the
  result is unambiguous within 2000–2100. Explicit units are also available.
  Snapshots without exchange time explicitly use capture time.
- Native timestamps and decimal strings remain in cached files and raw-event
  provenance. Canonical timestamps/prices use MDRAP's existing float model.
- Canonical sequences are **synthetic replay ordinals**, not exchange packet
  sequence numbers. Noncontiguous trade IDs do not imply lost packets. Repeated
  trade IDs reuse an ordinal within a bounded 200,000-identity window so normal
  pipeline duplicate checks quarantine them. Native IDs remain in provenance.
- Rows stay in stored order; exchange-time regressions remain visible to the
  quality engine. Derived event IDs and processing times are assigned by the
  existing pipeline. Raw IDs are stable hashes of file key, checksum and row
  position; they remain stable across overlapping requests.

### Order-book reconstruction

A price-level row is not a quote. MDRAP groups contiguous rows by capture time,
exchange time, event type and update IDs. It clears both sides once per
snapshot, applies absolute quantities, deletes zero-sized levels, and emits a
BBO only after the entire group. Decimal price keys avoid floating-point level
collisions. One-sided books emit no BBO; their native records remain available.
Crossed BBOs pass through the normal quality engine and are quarantined.

A verified snapshot is required before emitting quotes. Hourly files **are not
guaranteed to start with a snapshot**. To seed an interval from older data:

```bash
mdrap historical ingest --exchange bybit --symbol BTCUSDT --data-type orderbook \
  --book-start 2025-08-01 --start 2025-08-01T20:00:00Z \
  --end 2025-08-01T21:00:00Z --output data/chd-runs/btc-book
```

Choose `--book-start` to include a complete snapshot; this example does not
promise that one exists. Pre-interval updates can be skipped until the first
snapshot. Unknown state at the requested start, malformed levels, regressing
update IDs, or gaps in available continuity IDs abort ingestion. Venues without
continuity IDs can only be checked for snapshot initialization and file
completeness. No book is invented from incomplete deltas. If no usable snapshot
is available, use native downloads for L2 analysis. See
[CHD's L2 caveats](https://www.cryptohftdata.com/datasets/crypto-orderbook-data).

## Cache, completeness and resumption

```bash
mdrap historical download --exchange binance_futures --symbol BTCUSDT \
  --data-type mark_price --start 2025-08-01 --end 2025-08-02 \
  --manifest data/funding-manifest.json --json

# Reuse only verified files: no authentication or network calls.
mdrap historical ingest --exchange binance_spot --symbol BTCUSDT \
  --start 2025-08-01T20:00:00Z --end 2025-08-01T20:01:00Z \
  --offline --output data/chd-runs/btc-offline
```

The default cache is `data/chd-cache`; override it with `--cache-dir`.
Downloads stream to temporary files. Both native Parquet/Zstd and legacy outer
`.parquet.zst` payloads are detected by magic bytes. A file is published to the
cache only after decoding and Parquet metadata validation. Receipts record the
remote key, wire and decoded SHA-256 checksums, sizes, row count, and download
time. Every reuse verifies the decoded checksum. Checksums detect local changes;
they are locally computed, not provider signatures. Corrupt cache entries fail
explicitly: remove the reported file and adjacent JSON receipt to refetch.
Incomplete entries are refetched online and refused offline.

404 means **unavailable**, not zero events. This can mean delayed publication,
missing historical coverage, or a dataset not collected for that market. By
default missing files abort the request. `download --allow-missing` reports
all unavailable hours, sets `complete: false`, and exits **2**. Ingestion always
requires complete files, including explicitly requested warmup hours. Other
errors exit **1**; successful operations exit **0**. `--json` supports scripts.
Timeouts and transient HTTP errors receive bounded retries; long Retry-After
values instruct the caller to resume later.

An ingestion run contains:

```text
btc-minute/
  mdrap.db         canonical_events, quarantine, lineage, source_health
  raw/             normalized RawEvents, compatible with mdrap replay
  manifest.json    exact request, file receipts/paths, transformation settings,
                   pipeline metrics and code revision
```

Native full-depth data remains in the cache; retain the cache files referenced
by the manifest for complete source lineage. Quote raw events contain the last
native row and the first/last group positions, linking reconstruction back to
those files. Replay of `raw/` reproduces normalized trades/BBOs; reprocessing
native L2 uses the verified cache and original request.

Runs are built in a temporary sibling directory and published together after
success. Existing output directories are refused, avoiding duplicate append
and overwrite. Failed imports leave successful cached hours reusable but
publish no partial run. A narrower interval can contain zero events despite a
nonempty hourly file; that also produces no published run. Pipeline processing
metrics describe local import performance, not a historical execution clock.

## Python API

```python
from chd import CHDClient, HistoricalRequest
from chd_history import iter_events, ingest_history

client = CHDClient()  # environment key is optional; no request at construction
request = HistoricalRequest(
    exchange="binance_futures", symbol="BTCUSDT", data_type="mark_price",
    start="2025-08-01T20:00:00Z", end="2025-08-01T21:00:00Z",
)
manifest = client.download(request)
for record in client.iter_records(request, batch_size=8192):
    # Native values, including exact decimal strings and nullable fields.
    funding_rate = record.values.get("funding_rate")
    provenance = (record.partition.key, record.row_index, record.file_sha256)
```

A client is serial and belongs to one worker. Parquet decoding operates in
batches, and downloads/decompression stream to disk. Canonical ingestion also
uses the existing pipeline's bounded storage batches and quality windows; its
metrics have their existing memory behavior. Discovery and planning do not
require the optional Parquet dependencies.

## Why REST rather than the Python SDK?

Reviewed CHD SDK **0.7.0** and official documentation on **2026-09-12**. The SDK
is convenient for pandas notebooks, but high-level date-range methods collect
hourly DataFrames and concatenate them. Its generic download path can return
`None` on parsing failures. MDRAP needs persistent file receipts, strict
completeness, bounded batch decoding, offline replay, and explicit failures.
The REST adapter provides those directly with the standard-library transport
and two optional format dependencies. No SDK internals or pandas dependency
are introduced.

Contract sources:
[overview](https://www.cryptohftdata.com/docs),
[authentication](https://www.cryptohftdata.com/docs/rest-authentication),
[symbols](https://www.cryptohftdata.com/docs/rest-symbols),
[trades schema](https://www.cryptohftdata.com/docs/python-trades),
[order-book schema](https://www.cryptohftdata.com/docs/python-orderbook),
[mark/funding schema](https://www.cryptohftdata.com/docs/python-mark-price),
[SDK distribution](https://pypi.org/project/cryptohftdata/0.7.0/).
