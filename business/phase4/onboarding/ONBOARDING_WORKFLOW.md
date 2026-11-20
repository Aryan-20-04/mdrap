# Design Partner Onboarding Workflow

This document outlines the standard 5-stage onboarding process for institutional design partners evaluating MDRAP.

```
+---------------------------------------------------------------------------------------------------+
|                                 5-STAGE DESIGN PARTNER ONBOARDING                                 |
|                                                                                                   |
|  [Stage 1: Discovery] -> [Stage 2: Deployment] -> [Stage 3: Passive Shadow]                      |
|                                                              |                                    |
|                                                              v                                    |
|                       [Stage 5: Evaluation]   <--  [Stage 4: Incident Review]                     |
+---------------------------------------------------------------------------------------------------+
```

---

## Stage 1: Technical Discovery & Scoping (Days 1–3)
- Execute [Technical Discovery Questionnaire](TECHNICAL_DISCOVERY_QUESTIONNAIRE.md).
- Identify monitored venues (e.g. Coinbase, Kraken, Binance, Databento) and key instruments.
- Confirm host environment specifications (Linux or Windows, bare metal or container).
- Review and sign the [30-Day Pilot Agreement](PILOT_AGREEMENT_DRAFT.md).

## Stage 2: Installation & Pre-Flight Validation (Days 4–5)
- Execute [Deployment Checklist](DEPLOYMENT_CHECKLIST.md).
- Run pre-flight validator: `python deploy/validate_environment.py`.
- Configure `config.toml` with target instruments, venues, and cryptographic salt.
- Verify container or systemd daemon status using health check: `python deploy/healthcheck.py`.

## Stage 3: Passive Shadow Monitoring (Days 6–15)
- MDRAP runs as a passive sidecar alongside existing trading infrastructure.
- Zero impact on live trading: no order execution, no routing interference.
- Sidecar records event provenance and establishes baseline clock drift and spread distributions.

## Stage 4: Incident Detection & Forensic Review (Days 16–25)
- Review detected data quality anomalies (sequence jumps, crossed books, stale ticks).
- Conduct weekly engineering check-ins using [Weekly Review Template](WEEKLY_REVIEW_TEMPLATE.md).
- Execute deterministic incident replay on captured corridors: `python cli.py replay --incident <id>`.
- Audit exported evidence packages using standalone verifier: `python tools/verify_evidence.py`.

## Stage 5: Pilot Evaluation & Decision (Days 26–30)
- Deliver comprehensive Design Partner Pilot Assurance Report.
- Quantify measured anomalies: total dropped packets, crossed book episodes, and prevented bad ticks.
- Evaluate commercial licensing or execute clean [Exit Procedure](EXIT_PROCEDURE.md).
