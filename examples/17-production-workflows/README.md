---
category: 17
status: index
---

# 17 Production workflows

This directory has no scripts. No production workflow example exists. The built-in
backends share the host (or node) kernel, so kernel, engine, runtime or cluster
exploits are outside their threat model, and none is certified for hostile
multi-tenant use.

The building blocks that exist are shown elsewhere: explicit backend selection and
fail-closed capabilities ([07](../07-capabilities-and-backends/README.md)), resource
budgets ([04](../04-resource-management/README.md)), verified termination
([06](../06-async-and-cancellation/README.md), [18](../18-failure-scenarios/README.md))
and artifact stores ([12](../12-snapshots/README.md)).

## Prerequisites

A future example needs a deployment threat review, authentication and tenant
isolation, persistent ownership records and operational reporting. Remote execution
and failover, which several workflows depend on, are proposed.

## Acceptance

Planned coverage: a concurrent workload runner, a code-execution service, admission
quotas, per-tenant data and secret scoping, trusted images, graceful shutdown,
recovery and redacted observability.

## Cleanup

An independent reconciler must handle lost owners. Work in an unknown termination
state is reported, never restarted blindly.

## Security

A directory name is not a qualification. Hostile tenants may need VM or microVM
boundaries; shared-kernel backends give no protection against kernel compromise.

See [deployment](https://isolattice.readthedocs.io/en/latest/operations/deployment/)
and [security best practices](https://isolattice.readthedocs.io/en/latest/security/best-practices/).
