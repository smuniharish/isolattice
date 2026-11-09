# Remote execution

There are two kinds of remote execution: remote engines, which are supported, and an
Isolattice remote service, which is proposed.

## Remote engines: supported

The container backends drive an engine client, and that client can reach an engine
on another machine:

| Backend | Remote target |
| --- | --- |
| [Podman](podman.md) | A named `ssh://` Podman connection |
| [Docker API](docker.md) | A remote Docker host |
| [Kubernetes](kubernetes.md) | Any cluster in a kubeconfig context |

CI runs the Podman suite through an `ssh://` connection on every push (see
[tested platforms](../reference/tested-platforms.md)). The same transport is used
daily with Podman machines on Windows and macOS. The policy, capability checks and
verified termination work the same as for a local engine, and Isolattice removes
only resources labelled as its own.

```bash
podman system connection add --identity ~/.ssh/id_ed25519 build-host \
    ssh://builder@build-host.example:22/run/user/1000/podman/podman.sock
```

```python
from isolattice import PodmanBackend

backend = PodmanBackend(connection="build-host", image="docker.io/library/alpine:3.22")
```

The engine enforces the boundary on the remote host. If the client host disappears,
cleanup depends on the engine (for example the Kubernetes `activeDeadlineSeconds`
backstop) and on operator reconciliation by label.

## Isolattice remote service: proposed

**Proposed, not implemented.** No Isolattice server, wire protocol, endpoint
registry or scheduler exists.

!!! note "Status"
    The [remote backend protocol](../architecture/15-remote-backend-protocol.md) is a
    design document only.

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
