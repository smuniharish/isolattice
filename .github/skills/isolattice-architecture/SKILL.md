---
name: isolattice-architecture
description: "Use when proposing subsystem architecture, API boundaries, ADRs or implementation gates for Isolattice."
---

# Architecture workflow

## Responsibility and inputs

Consult [overview](../../../docs/architecture/02-architecture-overview.md) and
[ADRs](../../../docs/architecture/22-architecture-decision-records.md).
Preserve the portable API with backend-specific enforcement, and keep required
policies fail-closed.

## Procedure

1. Inspect existing contracts and feature status in `features.yml`.
2. Explain responsibility, dependencies, interfaces and ownership.
3. Express each extension point as an `abc.ABC` with a default implementation, and
   make it injectable so downstream packages can replace it.
4. Evaluate maintained libraries and OS primitives; document alternatives and the
   trusted computing base.
5. Describe failure, race and security cases, and the evidence each needs.
6. Update ADRs, API, coverage and the subsystem's tests and examples plan.
7. Obtain architectural approval before runtime implementation.

## Tools, mistakes and done

Use source review, authoritative documentation and repository validation.
Do not make an OS name, mock or architecture diagram proof of support.
Done means consistent contracts, explicit open decisions, required docs, tests and
examples, and passing repository checks.
