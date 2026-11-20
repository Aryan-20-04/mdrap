# MDRAP 6.0.1 Live Feed Validation

## Scope and run control

- Revision: `068df50c09d7352edba496538345d1f9a44f7422` on `remediation/v6.0.1`.
- Run: 2026-11-14 20:58:11 UTC to 2026-11-14 21:28:32 UTC; requested 30 minutes, actual 1,820.35 seconds.
- Feed-only: public market-data WebSockets only. No account credentials, order endpoints, orders, or trades were submitted.
- Instruments: BTC/USD and ETH/USD.
- Quote/order-book venues: Binance, Coinbase, Kraken, OKX, Bybit (10 concurrent subscriptions; separately managed per instrument).
- Trade venues: Binance and Kraken (four concurrent public trade streams, parsed by the existing `BinanceAdapter` and `KrakenAdapter`). The WebSocket manager has no trade subscription channel for Coinbase, OKX, or Bybit.
- One controlled client-side disconnect/reconnect was triggered at five minutes. A separate 20-second backpressure probe intentionally stopped draining a 1,000-item queue.

## Actual event counts

| Venue | Instrument | Trades | Quotes |
|---|---:|---:|---:|
| Binance | BTC/USD | 351,643 | 17,934 |
| Binance | ETH/USD | 127,327 | 17,938 |
| Coinbase | BTC/USD | unavailable | 201,078 |
| Coinbase | ETH/USD | unavailable | 23,391 |
| Kraken | BTC/USD | 10,782 | 2 |
| Kraken | ETH/USD | 4,004 | 2 |
| OKX | BTC/USD | unavailable | 17,645 |
| OKX | ETH/USD | unavailable | 17,797 |
| Bybit | BTC/USD | unavailable | 70,775 |
| Bybit | ETH/USD | unavailable | 84,625 |
| **Total** |  | **493,756** | **451,187** |

Total processed and persisted canonical rows: **944,943** (519.1 events/s). There were no malformed parsed frames and no `INVALID` statuses; **941,035 (99.59%)** were marked `SUSPICIOUS`, and 3,908 were `VALID`.

## Connectivity, recovery, sequence and reconciliation

- All five quote venue workers reported connected at shutdown. Binance, Coinbase, OKX, and Bybit produced continuing updates. Bybit had two server-side connection closures (two errors, six attempts total) and had reconnected by shutdown.
- Kraken quote workers produced only four frames total: one initial book frame per instrument per manager generation. At shutdown both Kraken quote subscriptions were `STALE`, with last data about 24.9 minutes old, despite the socket’s connected flag. No continuing Kraken quote ingestion or meaningful Kraken quote recovery was established.
- At shutdown telemetry, Binance quote streams were CONNECTED, Coinbase/OKX/Bybit streams were DEGRADED, and Kraken streams were STALE. All four Binance/Kraken trade streams were connected before shutdown, with zero worker errors and zero trade-queue drops. After the controlled disconnect, trade socket reopen delays were Binance BTC 5.158s, Binance ETH 5.173s, Kraken BTC 1.057s, and Kraken ETH 0.955s.
- After the controlled reconnect, first quote frames arrived in **4.916–5.381 seconds**; all ten streams produced a first frame by 5.381 seconds. This verifies reconnect and first-frame recovery, not sequence-correct full-book reconstruction. Kraken’s post-reconnect frame was only a snapshot.
- Pipeline quality reasons: `STALE` 826,613; `SEQUENCE_GAP` 415,297; `OUT_OF_ORDER` 474,943; `CROSSED_QUOTE` 439,048; `DUPLICATE` 99; `PRICE_ANOMALY` 8. Reasons overlap. The raw-ID monitor also saw 2,160 repeated IDs, all from Kraken; 1,353 Kraken raw IDs appeared more than once. Store conflict count was zero.
- The validation sequence monitor observed 894,281 sequence-bearing events and its Coinbase numeric-gap estimate totaled 8,905,876 sequence values. The manager also reported multi-million per-provider gap values. These are anomaly signals, **not verified lost-frame counts**: venue sequence semantics are not normalized consistently, and the manager does not scope all sequence checks by channel. Its per-provider gap values reached millions. Binance quote update IDs are not exposed as the manager’s sequence field; Kraken quote frames have no sequence field.
- Reconciliation ran 658,923 decisions (439,414 quote, 219,509 trade); 439,048 were disagreements and reported crossed books. That is 66.6% of decisions. No independent consolidated-market reference was available to determine whether each disagreement represented a real venue opportunity or a stale/misaligned quote.

## Latency and resources

Latency samples were capped at 100,000. Exchange-to-receive uses exchange timestamps against the local wall clock, so it includes clock skew and is not pure network latency. Internal processing and persistence are local MDRAP measurements.

| Measurement | p50 | p95 | p99 | p99.9 |
|---|---:|---:|---:|---:|
| Exchange timestamp to receive (ms) | 132.14 | 520.46 | 1,243.62 | 4,379.59 |
| Receive to pipeline start (ms) | 4.46 | 21.43 | 72.88 | 306.41 |
| Pipeline processing call (ms) | 0.094 | 0.395 | 0.931 | 4.789 |
| `flush(wait=True)` persistence latency (ms; 179 samples) | 18.53 | 67.27 | 82.77 | 102.34 |

- Process CPU: p50 15.0%, p95 34.3%.
- Process RSS: p50 197.2 MiB, max 245.1 MiB. This includes the validation harness’s bounded raw-ID set and latency samples; it is not an isolated production-only memory figure.
- Aggregate queue depth: max 72, p95 0. Main-run manager and trade queue drops: **0**.
- Runtime resource stop thresholds were 1 GiB RSS and 30,000 aggregate queued events; neither was reached. Peak thread count was not sampled; shutdown left three process threads.

## Backpressure and persistence

- In the separate live overload probe, 4,567 frames were counted as dropped from the 1,000-item queue in 20 seconds: Binance 302, Coinbase 2,242, OKX 301, Bybit 1,722, Kraken 0. The in-memory tombstone ring retained 1,000 entries, so it did **not** retain one tombstone per drop; the aggregate drop counter did record all 4,567. Durable persistence of the probe’s tombstones was unavailable.
- The main run had zero counted queue drops. SQLite close/reopen returned `PRAGMA integrity_check = ok`, with 944,943 canonical rows and zero Store conflicts.
- Abrupt child-process termination after fsyncing one live trade payload to the pipeline journal recovered that row on Store reopen; child exit code 0 and integrity check `ok`.

## Defects, unavailable checks and soak decision

- The quality pipeline marked 99.59% of live events suspicious. Sequence-gap/out-of-order and stale classifications are not credible as verified packet-loss counts until sequence scopes and per-venue update semantics are handled correctly. Cross-venue disagreement was also frequent and lacks an independent truth source.
- Kraken quote parsing/streaming yielded only initial snapshots, then went stale. The manager’s connected flag alone overstated feed health; freshness telemetry exposed the stale stream.
- Existing manager subscriptions do not provide genuine trades for Coinbase, OKX, or Bybit. Trade tests for those venues are unavailable through the existing manager/adapters used here.
- The 30-minute SQLite database reached about 1.18 GB. A longer soak was **not run**: the initial validation exposed correctness/coverage failures, and available free-disk capacity could not be established safely. Synthetic invalid/stale injections were not mixed into live counts.
- No production code was modified. Results are from public feeds; no exchange orders were placed.

## Final verdict

**FAIL** — Connectivity, throughput, bounded queues in the main run, SQLite integrity, and one crash-recovery record were demonstrated. The stale Kraken quote feed, pervasive suspicious classifications, unvalidated sequence anomalies, high reconciliation disagreement, and incomplete trade-channel coverage prevent a reliability pass.


