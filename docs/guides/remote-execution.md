# Remote execution

!!! warning "Proposed: not implemented"
    Remote execution is a **proposed** design. Isolattice has no remote backend, no
    remote protocol client or server, and no scheduler. Nothing on this page can be
    used today.

## What exists today

Every implemented backend runs workloads through an engine or operating system that
the Python process reaches directly:

- The Linux namespace and Windows backends run on the same host as your code.
- `PodmanBackend`, `DockerBackend` and `KubernetesBackend` drive an engine client
  (`podman`, `docker`, `kubectl`). That client can already talk to an engine on
  another machine through its own connection mechanism: a Podman connection, a Docker
  host URL or a kubeconfig context. The Isolattice runtime, its termination
  verification and its artifact staging stay on the client host.

These backends share the host or node kernel, so kernel, engine and cluster
exploits are out of scope wherever the engine runs.

Using an engine connection to a remote host is not "remote execution" in the proposed
sense. There are no leases, no endpoint failover and no orphan reconciliation across
hosts; if the client host disappears, cleanup depends on the engine (for example the
Kubernetes `activeDeadlineSeconds` backstop) and operator reconciliation by label.

## The proposed design

The [remote backend protocol](../architecture/15-remote-backend-protocol.md) describes
a possible future backend that would delegate execution to an Isolattice service on
another host. In outline, it covers:

- an authenticated service that owns enforcement, with operator-configured trusted
  endpoints, credentials and accepted capability evidence;
- idempotent creation and execution requests, where a lost response is UNKNOWN until
  reconciled rather than a reason to run again elsewhere;
- server-owned leases, so a client that disappears cannot orphan workloads
  indefinitely, and server-side reconciliation of orphaned boundaries;
- endpoint scheduling and failover filtered by required guarantees, health and
  capacity;
- bounded, resumable event and output streams with explicit sequence gaps.

The design is subject to architecture review and approval before any implementation.
See also the [remote backend page](../backends/remote.md) and the
[comparison with remote execution services](../comparisons/remote-execution-vs-isolattice.md).

## What to do instead

| Goal | Option today |
| --- | --- |
| Run workloads on a bigger machine | Point `PodmanBackend` or `DockerBackend` at a remote engine connection |
| Run workloads in a cluster | Use `KubernetesBackend`; see [Kubernetes integration](kubernetes-integration.md) |
| Share snapshots between hosts | Use `S3ArtifactStore`; see [custom integrations](custom-integrations.md#s3-client-options) |
| Audit engine commands | Wrap the engine client with a [custom command runner](custom-integrations.md#custom-command-runner) |
