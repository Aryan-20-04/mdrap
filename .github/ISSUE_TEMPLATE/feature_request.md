---
name: Feature Request / RFC
about: Suggest an idea or enhancement for MDRAP
title: "[RFC] "
labels: ["enhancement"]
assignees: ""
---

## Feature Description
A clear and concise description of the proposed feature or improvement.

## Problem Statement / Motivation
Is your feature request related to an observed market data reliability problem? (e.g. "We need support for venue X multicast framing...")

## Proposed Technical Solution
Describe how this should be designed within MDRAP's read-only sidecar architecture.

## Non-Negotiable Constraints
- [ ] Confirmed: Does NOT introduce order-routing, execution, or private trading secrets.
- [ ] Confirmed: Preserves pure-Python stdlib fallback parity.
- [ ] Confirmed: Maintains microsecond-level latency boundaries.
