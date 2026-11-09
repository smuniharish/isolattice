---
category: 11
status: proposed
---

# 11 Remote execution

**Proposed.** Remote execution and the remote backend protocol are not implemented.
There is no remote backend class and no script in this directory.

Today every backend runs against an endpoint the caller configures explicitly: a
Podman connection, a Docker API endpoint or a kubeconfig context. Those engines may
run on another machine, but Isolattice has no multi-endpoint scheduling, leases or
failover.

## Prerequisites

None yet. A remote example requires an implemented, versioned client and service with
authenticated, owner-scoped operations, durable idempotency and leases.

## Acceptance

When implemented, examples should cover single and multiple endpoints, endpoint
selection strategies, health and capacity, heartbeats and lease expiry, and test lost
responses, partitions, duplicate request keys and ownership isolation.

## Cleanup

The service, not the client, must own lease-expiry termination and keep verified or
failed termination reports. A disconnected client must never assume a boundary is
empty.

## Security

No credentials in example code. Accepted work must be reconciled before any restart or
failover, and duplicate-execution risk must be stated explicitly.

See [remote execution](https://isolattice.readthedocs.io/en/latest/guides/remote-execution/)
and the [remote backend protocol](https://isolattice.readthedocs.io/en/latest/architecture/15-remote-backend-protocol/).
