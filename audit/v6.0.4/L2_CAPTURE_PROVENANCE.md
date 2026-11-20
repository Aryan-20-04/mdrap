# MDRAP 6.0.4 — 2-Hour Multi-Venue L2 Replay Capture Provenance

**Date:** 2026-11-20  
**Artifact Name:** `audit/v6.0.4/l2_capture_2hour.jsonl.gz`  
**Classification:** Extended Multi-Venue L2 Replay Verification Dataset  
**Distribution Status:** Preserved in local historical archive; excluded from remote git release branch per GitHub file size limit (100 MB).

---

## 1. Cryptographic Hash & File Metrics

- **File Path:** `audit/v6.0.4/l2_capture_2hour.jsonl.gz`
- **File Size (Exact):** `271,442,152 bytes` (`258.87 MiB`)
- **SHA-256 Checksum:** `57dbaf72cd54bf9c4c197823a72f472294ab05a9d7e8b0814026bcab5cd077c9`
- **Compression:** Standard gzip (`.jsonl.gz`)
- **Total Captured Frames:** `3,683,381 frames`
- **Total Evaluated Events:** `3,743,424 events`
- **Recording Duration:** `7,209.27 seconds` (~2 hours continuous public market data)

---

## 2. Commit History & Provenance

- **Original Commit Hash:** `c5876359ef7c45620c193d9e36786f7ee883b6d6`
- **Original Commit Date:** `Thu Nov 19 05:03:30 2026 +0530`
- **Original Author:** Aryan-20-04 <swaritsinha7@.com>
- **Original Commit Title:** `feat(audit): MDRAP 6.0.4 Extended 2-Hour Multi-Venue L2 Replay Validation`
- **Historical Git Branches:** Preserved permanently on local branches `maintenance/architecture-pruning-v608` and `remediation/v6.0.2`.

---

## 3. Scope and Venues Captured

The capture represents raw public L2 websocket book delta updates and public trade prints across 5 tier-1 crypto venues:
- **Binance:** `BTCUSDT` (Depth 20, trade prints)
- **Coinbase:** `BTC-USD` (Level 2 snapshot & l2update, matches)
- **Kraken:** `XBT/USD` (Depth 25, 100% verified Kraken CRC32 checksums)
- **OKX:** `BTC-USDT` (books5 channel with continuous sequence tracking)
- **Bybit:** `BTCUSDT` (orderbook.50 depth channel)

---

## 4. Exclusion Rationale & Summary Preservation

GitHub strictly enforces a 100.00 MB per-file size limit in its pre-receive server hooks. Because `l2_capture_2hour.jsonl.gz` is 258.87 MiB, it is excluded from the clean release branch (`maintenance/architecture-pruning-v608-clean`) pushed to `origin`.

All derivative evidence, validation conclusions, and replay summaries produced from this capture are preserved in the git tree:
- [replay_summary_2hour.json](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v6.0.4/replay_summary_2hour.json) — Complete machine-readable metrics from the 2-hour replay.
- [L2_REPLAY_VALIDATION.md](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v6.0.4/L2_REPLAY_VALIDATION.md) — Comprehensive technical report detailing 100% Native C vs Pure Python parity, zero crossed/locked books, and deterministic SHA-256 replay hashes.
- [INVENTORY_STRATEGY_RESEARCH.md](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v6.0.4/INVENTORY_STRATEGY_RESEARCH.md) — Strategy simulation and inventory risk findings.
