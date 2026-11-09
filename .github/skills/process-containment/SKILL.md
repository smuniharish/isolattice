---
name: process-containment
description: "Use for descendant containment, orphan prevention, boundary identity, PID reuse or process escape changes."
---

# Process containment

Consult [identity model](../../../docs/architecture/04-domain-model.md) and
[containment contract](../../../docs/architecture/10-process-containment.md).

## Workflow

1. Identify the trusted authoritative backend boundary and generation.
2. Establish restrictions before admission and freeze admissions during teardown.
3. Track resources and stale observations without relying on PID identity.
4. Cover forks, spawns, pools, shells, detached processes and respawners.
5. Add real escape/identity-reuse tests and independent teardown verification.
6. Document kernel/runtime trust and residual broker/external-admission risks.

Use native/runtime identities and maintained primitives. Process groups and
recursive PID scans are diagnostic aids only. UNKNOWN is never EMPTY.
Do not equate direct exit with orphan prevention.

Done requires verified advertised guarantees or explicit capability rejection,
plus termination integration, tests, docs and examples.
