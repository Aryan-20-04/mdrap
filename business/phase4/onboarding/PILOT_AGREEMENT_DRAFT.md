# Design Partner Pilot Agreement (Evaluation Draft)

**Platform**: MDRAP — Market Data Reliability & Assurance Platform (Release: `v6.0.0`)  
**Duration**: 30 Calendar Days from Initial Deployment  
**Fee**: \$0 (Non-Commercial Technical Evaluation)  

---

## 1. Parties and Purpose
This Evaluation Agreement is entered into between **MDRAP Platform Engineering** ("Provider") and the participating trading entity ("Design Partner"). The purpose is to evaluate the reliability, data-quality monitoring, and forensic auditing capabilities of MDRAP within Design Partner's self-hosted infrastructure.

---

## 2. Non-Negotiable Operational & Security Boundaries
1. **Zero Order Execution**: Design Partner acknowledges that MDRAP is strictly a read-only passive monitoring tool. Provider shall never request, receive, or store Design Partner's trading credentials, private keys, or execution interfaces.
2. **Self-Hosted Operation**: The software executes exclusively on hardware or VPC infrastructure controlled by Design Partner. No market data, customer telemetry, or trading information is transmitted to Provider's systems.
3. **Passive Shadow Mode**: MDRAP operates as a passive observer. It does not modify, intercept, or reroute live order submissions.

---

## 3. Provider Commitments
During the 30-day evaluation period, Provider agrees to:
- Deliver the official v6.0.0 software distribution (universal wheel and container images).
- Provide deployment assistance and pre-flight environment verification support.
- Participate in weekly 30-minute technical review sessions to analyze detected anomalies.
- Provide post-mortem assistance for any sequence gap or crossed book incidents recorded during the pilot.

---

## 4. Design Partner Commitments
Design Partner agrees to:
- Deploy MDRAP in passive read-only shadow mode on agreed target feeds.
- Provide anonymized operational feedback regarding detection accuracy, false positive rates, and resource utilization.
- Participate in an end-of-pilot evaluation meeting to review measured reliability metrics.

---

## 5. Intellectual Property & Confidentiality
- Provider retains all intellectual property rights to the MDRAP platform and algorithms.
- Design Partner retains all ownership of internal trading data, strategy configurations, and proprietary infrastructure.
- Both parties agree to keep all technical and architectural disclosures confidential.

---

## 6. Signatures

**For Provider (MDRAP Platform Engineering)**:  
Name: _______________________________ Title: _______________________________ Date: ______________

**For Design Partner**:  
Entity Name: _______________________________________________________________  
Name: _______________________________ Title: _______________________________ Date: ______________
