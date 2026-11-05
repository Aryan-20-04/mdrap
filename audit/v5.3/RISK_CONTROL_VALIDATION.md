# MDRAP v5.3 — Pre-Trade Risk Control & Gate Validation Report

## Overview
This document evaluates the 14 pre-trade institutional risk controls implemented in `packages/mdrap-strategies/mdrap_strategies/risk_engine.py` and `RealisticMatchingEngine`.

---

## 1. Inventory of Fourteen Institutional Risk Gates

| Gate ID | Risk Control Name | Function / Threshold | Action on Breach |
| :--- | :--- | :--- | :--- |
| **G01** | Max Single Order Notional | Rejects orders where $\text{price} \times \text{qty} > \$100,000$ | Reject Order |
| **G02** | Max Single Order Quantity | Rejects orders where $\text{qty} > 1,000$ shares | Reject Order |
| **G03** | Max Gross Position Notional| Total long + short exposure $< \$500,000$ | Reject Order |
| **G04** | Max Net Position Notional  | Absolute net directional exposure $< \$250,000$ | Reject Order |
| **G05** | Fat-Finger Price Collar    | Price must be within $\pm 50\text{ bps}$ of prevailing mid | Reject Order |
| **G06** | Maximum Daily Drawdown     | Halts trading if daily loss $> 5.0\%$ of capital | Activate Kill Switch |
| **G07** | Intraday Drawdown Step     | Halts trading if rolling 1h drawdown $> 2.5\%$ | Pause Trading |
| **G08** | Order Rate Limiter         | Max 100 orders per second per account | Reject Order |
| **G09** | Minimum Tick Size Enforcer | Price must align to exchange tick increments | Reject Order |
| **G10** | Minimum Lot Size Enforcer  | Quantity must align to exchange lot increments | Reject Order |
| **G11** | Short Sale Compliance      | Prevents naked short selling if borrow is absent | Reject Order |
| **G12** | Duplicate Order Guard      | Prevents re-submission of active Order IDs | Reject Order |
| **G13** | Stale Market Data Guard    | Rejects order if L2 book update age $> 1.0\text{ s}$ | Reject Order |
| **G14** | Emergency Global Kill Switch| Manual or programmatic master execution kill | Cancel All Active |

---

## 2. Test Execution & Evidence

All 14 risk gates were verified under automated unit tests in `tests/test_v53_quantitative_remediation.py` and `tests/gate_g2_harness.py`.
- Breaches of G01–G05 and G08–G13 cleanly reject order submission without side effects.
- Breaches of G06 and G14 immediately engage the global kill switch and initiate rapid cancel-all sweeps.
- Real-money trading credentials remain permanently disabled platform-wide; execution routes exclusively to paper trading or exchange testnets.
