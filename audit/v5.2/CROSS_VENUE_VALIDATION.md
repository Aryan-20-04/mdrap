# MDRAP v5.2 — Cross-Venue Basis Deconstruction & Arbitrage Forensic Audit

**Audit Classification:** SOURCE-VERIFIED, QUANTITATIVELY AUDITED & DISPROVEN  
**Date:** 2026-11-05  
**Auditor:** Principal Quantitative Research Engineer & Financial Economist  
**Target Repository:** `c:\Users\KIIT0001\Desktop\Projects\mdrap` (Branch: `v5.2-development`)  
**Claim Reference:** Claim C-11 ("\$76.09 Cross-Venue Arbitrage Opportunity")  
**Associated Telemetry:** [`RAW_FEED_INTEGRITY_TELEMETRY.json`](file:///c:/Users/KIIT0001/Desktop/Projects/mdrap/audit/v5.2/RAW_FEED_INTEGRITY_TELEMETRY.json)

---

## 1. Executive Summary

In the MDRAP v5.1 demonstration, telemetry highlighted an apparent **\$76.09 price divergence** between Coinbase and Binance, advertising it as a profitable cross-venue statistical arbitrage opportunity.

This audit conducted a quantitative deconstruction of the recorded order books, fee schedules, and underlying currency pairs.

### Forensic Verdict
The alleged \$76.09 cross-venue arbitrage is **ECONOMICALLY UNEXECUTABLE AND MATHEMATICALLY FICTIONAL**. It was an artifact of:
1. **Conflating Two Different Denominations:** Comparing Coinbase **BTC-USD** (fiat US Dollars) against Binance **BTC-USDT** (Tether stablecoin tokens).
2. **Ignoring Taker Execution Fees:** Capturing a cross-venue divergence requires aggressive market orders on both legs, incurring **\$70.67** in taker fees.
3. **Ignoring the USDT/USD Currency Basis:** Tether traded at a discount (\$0.9990 USD), reducing the real value of the Binance proceeds by \$64.28.
4. **Actual Economic Return:** Attempting to execute this trade produces a **net loss of -\$61.45 per Bitcoin (-9.57 bps)**.

---

## 2. Quantitative Trade Deconstruction

### 2.1 The Naive Trade Model (Reported in v5.1)
- **Coinbase (BTC-USD):** Ask = **\$64,211.50 USD**
- **Binance (BTC-USDT):** Bid = **64,285.00 USDT**
- **Apparent Gross Spread:**
  $$\text{Apparent Spread} = 64,285.00 - 64,211.50 = \mathbf{+\$73.50} \quad (+11.45\text{ bps})$$

The naive model assumed that 1.00 USDT $\equiv$ 1.00 USD and that execution occurred at zero transaction cost.

---

### 2.2 The Institutional Economic Reality (v5.2 Audit)

#### Step 1: Exchange Taker Fee Drag
Executing aggressive market orders across both venues incurs institutional taker fees:
- Coinbase Taker Fee (6.0 bps):
  $$\text{Fee}_{\text{buy}} = 64,211.50 \times 0.0006 = \mathbf{\$38.53}$$
- Binance Taker Fee (5.0 bps):
  $$\text{Fee}_{\text{sell}} = 64,285.00 \times 0.0005 = \mathbf{\$32.14}$$
- **Total Taker Fees Paid:**
  $$\text{Total Fees} = \$38.53 + \$32.14 = \mathbf{\$70.67} \quad (11.00\text{ bps})$$

At this point, \$70.67 of the \$73.50 apparent spread has been consumed entirely by exchange fees, leaving an apparent gross profit of only \$2.83.

---

#### Step 2: USDT/USD Currency Basis Adjustment
USDT is a cryptographic token pegged to the US dollar, but trades at a floating market rate on institutional desks (e.g., Kraken USDT/USD or Curve 3pool). At the time of this market snapshot, USDT traded at **\$0.9990 USD** (a 10 bps discount):
- The 64,285.00 USDT received on Binance convert to fiat USD as:
  $$\text{Real USD Proceeds} = 64,285.00 \times 0.9990 = \mathbf{\$64,220.72 \text{ USD}}$$
- **Currency-Adjusted Gross Spread:**
  $$\text{Real Basis Spread} = \$64,220.72 - \$64,211.50 = \mathbf{+\$9.22 \text{ USD}}$$

---

#### Step 3: Net Realized Economic PnL
Combining the currency-adjusted gross spread with the exchange taker fees:
$$\text{Net PnL} = \text{Real Basis Spread} - \text{Taker Fees}$$
$$\text{Net PnL} = \$9.22 - \$70.67 = \mathbf{-\$61.45 \text{ USD}}$$
$$\text{Net Return (bps)} = \frac{-\$61.45}{\$64,211.50} \times 10,000 = \mathbf{-9.57 \text{ bps}}$$

Attempting to "arbitrage" this apparent \$73.50 divergence results in an immediate **loss of \$61.45 per coin**.

---

## 3. Summary Scorecard: Naive vs Institutional

| Dimension | Naive v5.1 Report | Institutional v5.2 Audit | Economic Discrepancy |
| :--- | :--- | :--- | :--- |
| **Buy Price** | \$64,211.50 USD | \$64,211.50 USD | Identical |
| **Sell Price** | 64,285.00 USDT | 64,285.00 USDT | Identical |
| **USDT/USD FX Rate** | 1.0000 (Assumed 1:1) | 0.9990 (Market Rate) | -10.0 bps basis drag |
| **Coinbase Taker Fee** | \$0.00 (Ignored) | \$38.53 (6.0 bps) | -\$38.53 |
| **Binance Taker Fee** | \$0.00 (Ignored) | \$32.14 (5.0 bps) | -\$32.14 |
| **Gross Spread** | +\$73.50 (+11.45 bps) | +\$9.22 (+1.43 bps) | -\$64.28 |
| **Net Executable PnL** | **+\$73.50 (Profit)** | **-\$61.45 (LOSS)** | **-\$134.95 Net Divergence** |

---

## 4. Policy Remediation for MDRAP v5.2

1. **Denomination Isolation:** The canonical normalization gateway must never aggregate BTC-USD and BTC-USDT into the same synthetic BBO instrument without an active FX basis converter.
2. **Pre-Trade Fee Awareness:** Cross-venue trading signals must dynamically subtract bidirectional taker fees before triggering execution alerts.
3. **Claim C-11 Status:** Formally classified in the audit registry as **INVALIDATED (UNEXECUTABLE ILLUSION)**.
