# MDRAP Customer Discovery & Market Validation Playbook

**Document Version**: 2.0  
**Responsible Agent**: Lead Engineering Architect / Commercial Strategy  
**Status**: APPROVED PLAYBOOK  

---

## 1. Commercial Thesis

Trading firms do not want another trading terminal or generic order-execution engine. They are already locked into internal execution management systems (EMS) or broker infrastructure.

However, **data-quality failures, unannounced exchange feed shifts, and post-incident investigation overhead** cost prop shops and market makers millions of dollars annually in unexpected losses and lost developer hours.

**MDRAP Value Proposition**:
*"Deploy a zero-friction reliability sidecar alongside your existing crypto feed handlers. Detect anomalies instantly, preserve cryptographic evidence, and reproduce any incident bitwise in under 30 seconds."*

---

## 2. Key Hypotheses to Validate

| Hypothesis # | Commercial Hypothesis | Validation Indicator |
| :--- | :--- | :--- |
| **H-1** | Post-incident root-cause investigation currently takes between 4 and 48 engineering hours per incident. | Quantitative developers confirm spending significant time hunting raw logs and comparing exchange timestamps after an unexpected PnL drop. |
| **H-2** | Firms will not install software that requires replacing their existing feed handlers or submitting orders. | CTOs express willingness to deploy an out-of-band TAP/sidecar if it is purely read-only and adds zero risk to order routing. |
| **H-3** | Cryptographic evidence of data quality provides measurable compliance value (SEC Rule 606 / MiFID II / internal LP audit). | Risk managers actively demand tamper-evident proof that execution models did not receive stale or crossed data. |

---

## 3. Targeted Customer Discovery Interview Framework

### 3.1 For Quantitative Developers & Researchers
1. *"When an automated trading model produces an anomalous execution or enters an unexpected risk state, what is your current step-by-step process to determine whether the market moved or the feed glitched?"*
2. *"Do your production logs preserve the exact untouched raw wire bytes from the exchange, or only normalized internal objects?"*
3. *"If you need to reproduce what the top-of-book looked like at a specific millisecond last Tuesday, can you do that deterministically today? How long does it take?"*
4. *"What percentage of your research time is spent cleaning and debugging data anomalies vs. building alpha?"*

### 3.2 For Market-Data & Infrastructure Engineers
1. *"How do you currently monitor WebSocket connection health across Bybit, Binance, OKX, and Kraken?"*
2. *"How do you detect silent sequence gaps when an exchange drops packets without dropping the TCP socket?"*
3. *"What happens to your trading pipeline when exchange server clocks drift by 500ms or 2 seconds?"*
4. *"What latency budget would you tolerate for an assurance sidecar running off a local shared-memory ring buffer?"*

### 3.3 For CTOs & Heads of Trading
1. *"How much money has your firm lost in the past 12 months due to bad market data, feed outages, or anomalous fills?"*
2. *"If a software product could give you an immutable incident report and bitwise deterministic replay of every feed anomaly, would that be an internal build or an external buy?"*
3. *"What are your security prerequisites for deploying third-party market-data infrastructure into your co-located servers?"*
4. *"Would an annual subscription of \$25,000 – \$75,000 per desk be justifiable if it eliminated manual incident forensic investigations?"*

---

## 4. Discovery Execution Plan & Next Steps

1. **Target Cohort**: Conduct 15 structured discovery interviews across:
   - 6 Cryptocurrency Market Makers (Singapore, London, Chicago, Dubai)
   - 5 Prop Quantitative Desks
   - 4 High-Frequency Infrastructure Startups
2. **Success Gate**: At least 8 out of 15 firms confirm:
   - Incident investigation takes $>4$ hours.
   - They would pilot a read-only sidecar if it runs alongside their current setup.
3. **Pilot Commitments**: Secure 2 unpaid pilot design partners to install MDRAP v5.3.0 in shadow/tap mode.
