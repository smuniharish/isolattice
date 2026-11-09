---
name: sandbox-backend-development
description: "Use when developing approved backend protocols, adapters, discovery, health or enforcement conformance."
---

# Backend development

Read [contracts](../../../docs/architecture/07-backend-contracts.md) and
[resolution](../../../docs/architecture/08-backend-resolution.md).

## Workflow

1. Confirm approved boundary/identity mechanism and dependencies.
2. Probe privileges/features/configuration and report scoped capabilities.
3. Establish restrictions before workload admission.
4. Implement bounded async operations, observation and owned rollback/release.
5. Reject unsupported optional operations; preserve explicit engine identity.
6. Add conformance tests and real platform security evidence, docs and examples.

Use maintained SDKs/native primitives. Blocking adapters need transport deadlines
and tracked workers; cancellation does not automatically stop SDK calls.
Never return EMPTY on probe failure or silently choose another backend.

Done requires real enforcement/termination evidence for advertised guarantees;
mock conformance and skips cannot certify a backend.
