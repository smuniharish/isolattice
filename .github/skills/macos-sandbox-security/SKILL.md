---
name: macos-sandbox-security
description: "Use when evaluating macOS Seatbelt, App Sandbox, signed helpers, filesystem or process-tree guarantees."
---

# macOS sandbox security

Read [macOS limitations](../../../docs/backends/macos.md) and
[threat model](../../../docs/architecture/17-security-threat-model.md).

## Workflow

1. Identify supported public deployment/entitlement APIs and helper trust.
2. Evaluate actual filesystem/network controls and documented support status.
3. Find an authoritative containment/emptiness mechanism before promising it.
4. If none is qualified, reject required process-tree guarantees.
5. Test configured enforcement on real approved macOS environments.
6. Record limitations, evidence, docs and explicitly permitted alternatives.

Seatbelt restrictions are not proof of complete descendant containment.
Never use process groups, direct-child exit or undocumented API assumptions as
portable security claims. Do not silently switch to a container/remote service.

Done means honest capabilities and qualified evidence, or explicit unsupported
status with actionable limitations; no pretend native implementation.
