---
name: remote-backend
description: "Use for remote protocol, endpoint scheduling, authentication, leases, failover or orphan reconciliation."
---

# Remote backend

Read [protocol](../../../docs/architecture/15-remote-backend-protocol.md) and
[resolver](../../../docs/architecture/08-backend-resolution.md).

## Workflow

1. Confirm server responsibility, versioned schemas and configured endpoint trust.
2. Authorize every owner/generation-scoped operation and bound inputs/streams.
3. Persist idempotency/request digest and reconcile uncertain acceptance.
4. Filter health/capacity/capabilities before scheduling.
5. Use server-owned leases and independent orphan reconciliation.
6. Keep disconnected termination UNKNOWN until trusted evidence recovers.
7. Test partitions, duplicate keys, ownership races and expired leases.

Use maintained async transport; TLS is not enforcement qualification.
Never restart accepted work on another endpoint without explicit duplicate-risk
policy; migration needs compatible checkpoints or authorized restart semantics.

Done requires protocol tests, failure recovery, server evidence and honest
capabilities, with docs/examples and no universal exactly-once claim.
