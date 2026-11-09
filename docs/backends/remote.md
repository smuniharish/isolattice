# Remote backend

**Proposed, not implemented.** Isolattice has no remote backend, remote execution
service or client API. All built-in backends run on the local host or against a
locally configured engine or cluster.

!!! note "Status"
    The [remote backend protocol](../architecture/15-remote-backend-protocol.md) is a
    design document only. No wire protocol, endpoint configuration or scheduler exists.

The design calls for:

- authenticated, trusted endpoints with owner authorization and reported capabilities;
- endpoint selection only after filtering on required guarantees, health and capacity;
- durable operation identity and server-controlled leases, so a lost response is
  reconciled before another endpoint is tried;
- server-side orphan cleanup, because a client disconnect never proves a workload
  stopped.

Running workloads cannot move transparently between endpoints. Any implementation
needs architecture approval for authentication, lease storage and remote termination
verification.

## Related

- [Remote execution guide](../guides/remote-execution.md)
- [Remote execution compared with Isolattice](../comparisons/remote-execution-vs-isolattice.md)
- [Custom backend guide](../guides/custom-backend.md)
