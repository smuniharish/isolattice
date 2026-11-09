# 15. Remote backend protocol

!!! warning "Proposed"
    Remote execution is proposed. There is no remote backend, protocol, server or
    client in Isolattice, and no compatibility or availability is promised.

## Today

Applications that need execution on another host can use the implemented backends
against remote endpoints they already trust:

- `PodmanBackend(connection=...)` with a named Podman connection.
- `DockerBackend(host=...)` with an explicit Docker API endpoint.
- `KubernetesBackend(kubeconfig=..., context=..., namespace=...)` with a cluster.
- A custom `CommandRunner` that wraps the engine clients, for example to add
  auditing or a tunnel.

These keep local verified termination semantics because the engine or API server
remains the authoritative boundary identity. They do not add tenant authentication,
leases or orphan reconciliation beyond what the engine or cluster provides.

## Requirements for a remote design

A remote backend would need an architecture review and approval before
implementation. At minimum it must provide:

- Mutual authentication, authorization per tenant and secret-free logs and events.
- Idempotency keys for create and execute, and an operation lookup that answers
  accepted, completed or unknown after a lost response.
- Leases with explicit expiry, and server-side reconciliation of orphaned boundaries.
- Bounded streaming with backpressure and explicit truncation.
- Termination reports with the same `terminated`, `non_empty` and `unknown` meanings;
  network loss is `unknown`, never success.
- Capability evidence for the remote host, not the client.

![Proposed idempotent remote operation lookup](../assets/diagrams/remote-execution-proposal.png)

See the [remote backend page](../backends/remote.md) and the
[remote execution guide](../guides/remote-execution.md).
