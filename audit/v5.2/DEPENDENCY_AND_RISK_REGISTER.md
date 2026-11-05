# MDRAP v5.2 — Dependency & Systemic Risk Register

## 1. External & Platform Dependency Inventory

```
+---------------------------------------------------------------------------------------------------------+
|                                  DEPENDENCY AUDIT & SECURITY AUDIT                                      |
+----------------------+----------------+-----------------+-----------------------+-----------------------+
| Dependency Name      | Version In Use | Source Origin   | Architectural Scope   | Security Footprint    |
+----------------------+----------------+-----------------+-----------------------+-----------------------+
| Python               | 3.13.1         | CPython Core    | Base Runtime          | Free-threaded ready   |
| MSVC C Compiler      | 19.42 (VS 2022)| Microsoft       | Native C Hot-Path DLL | /O2 /Oi /Ot compiled  |
| rich                 | >= 13.0.0      | PyPI            | CLI Formatting        | Low (Presentation)    |
| websockets           | >= 12.0.0      | PyPI            | Live Crypto Feeds     | Medium (Network I/O)  |
| pytest               | 8.4.2          | PyPI            | Test Runner           | Development only      |
| sqlite3              | 3.47.2         | Python stdlib   | WAL Async Projections | Built-in C extension  |
| mmap / ctypes        | stdlib         | Python stdlib   | SHM v3 Seqlock IPC    | Direct OS memory access|
| hashlib / hmac       | stdlib         | Python stdlib   | Testnet Auth & Idemp  | Cryptographic auth    |
+----------------------+----------------+-----------------+-----------------------+-----------------------+
```

---

## 2. Institutional Risk Matrix & Mitigation Strategies

```
+---------------------------------------------------------------------------------------------------------+
|                                    INSTITUTIONAL RISK REGISTER                                          |
+----+-----------------------+----------+--------+--------------------------------------------------------+
| ID | Risk Category         | Severity | Impact | Verified Mitigation & Architectural Control            |
+----+-----------------------+----------+--------+--------------------------------------------------------+
| R1 | Real-Money Live Order | CRITICAL | FATAL  | Hard-coded SecurityViolationError; prod URLs blocked.  |
| R2 | Annualization Fallacy | HIGH     | LOSS   | Reject per-trade annualization; mandate fixed bars.    |
| R3 | Taker Fee Alpha Drain | HIGH     | LOSS   | Mandate maker quoting or require minimum edge > 15 bps.|
| R4 | Drawdown Accumulation | HIGH     | LOSS   | Gate 14 Circuit Breaker abruptly halts trading at 5.0%.|
| R5 | Cross-Venue Basis Err | HIGH     | LOSS   | Distinguish fiat USD from Tether USDT in symbology.    |
| R6 | Process Crash Loss    | MEDIUM   | DOWN   | Binary WAL append + atomic state persistence to disk.  |
| R7 | Sequence Packet Drops | MEDIUM   | DATA   | LiveCryptoFeed monotonic gap detector & tombstoning.   |
| R8 | Python GIL Bottleneck | MEDIUM   | PERF   | Offload hot paths to native C DLL & process shards.    |
| R9 | Seqlock Reader Starv  | LOW      | PERF   | Reader backoff retry with max loop threshold (1,000).  |
| R10| Stale Market Quotes   | MEDIUM   | LOSS   | Gate 4 rejects quotes older than 2.0s before routing.  |
+----+-----------------------+----------+--------+--------------------------------------------------------+
```

### Risk R1: Inadvertent Real-Money Execution
- **Hazard**: An algorithmic test suite or paper trading session pointing to a live production exchange API could accidentally trigger real capital loss.
- **Control Enforced**: In `packages/mdrap-strategies/mdrap_strategies/backends/testnet.py`, `ExchangeTestnetBackend` validates that the URL hostname matches `ALLOWED_TESTNET_HOSTS` (`testnet.binance.vision`, `api-public.sandbox.exchange.coinbase.com`, `testnet.bybit.com`). Any other hostname immediately triggers a fatal `SecurityViolationError`. Real-money trading is disabled platform-wide.

### Risk R2: Backtest Overfitting & False Sharpe Ratios
- **Hazard**: Inflated Sharpe ratios create false confidence in unviable strategies.
- **Control Enforced**: In v5.2, all performance tear sheets enforce strict periodic sampling (1-minute or 5-minute bars) with the exact matching annualization multiplier ($\sqrt{252 \times 390}$ for 1-minute bars).

### Risk R3: Taker Fee Drag in High-Frequency Trading
- **Hazard**: Momentum strategies that cross the spread pay exchange taker fees (5.0 to 10.0 bps) that erase gross alpha.
- **Control Enforced**: Strategy tear sheets report **Net P&L (post-fees)** as the primary decision metric; strategies with negative net returns are rejected regardless of gross profits.
