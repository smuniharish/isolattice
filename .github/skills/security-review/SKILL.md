---
name: security-review
description: "Use when reviewing sandbox guarantees, threat boundaries, secret handling, escape prevention or dependency risks."
---

# Security review workflow

Read [threat model](../../../docs/architecture/17-security-threat-model.md) and
[security policy](../../../SECURITY.md).

## Workflow

1. Establish scope, implementation status, assets/adversary and TCB.
2. Trace required guarantees through validation, setup, execution and teardown.
3. Review privilege/broker/socket/namespace boundaries and identity authority.
4. Check secrets, untrusted archives, remote ownership and dependency advisories.
5. Distinguish observed defects from unverified proposals and platform skips.
6. Report evidence, impact, confidence, remediation and residual risks.
7. Reproduce safely only in approved isolated test environments.

Do not publish sensitive payloads or equate passing mocks with enforcement.
Use authoritative sources and real integration tests where claims are made.
Documentation consistency review is not an exploit audit.

Done means actionable evidence-scoped findings and explicit unverified areas,
with fixes/tests/docs tracked before qualification.
