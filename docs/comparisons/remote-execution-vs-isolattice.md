# Remote execution versus Isolattice

Remote execution covers anything that runs work on another machine: SSH, task queues
such as Celery, hosted code-execution sandboxes and cloud batch services.

!!! note "Remote engines today, remote service proposed"
    Podman, Docker and Kubernetes backends can drive an engine or cluster on another
    host today; CI tests Podman over `ssh://`. A dedicated Isolattice remote service and
    its [remote backend protocol](../architecture/15-remote-backend-protocol.md) are
    proposed, not implemented. See the [remote](../backends/remote.md) page.

## When to use which

- **Use an existing remote system** when you need scheduling, queues or distribution.
  Task queues, SSH and hosted sandboxes are the right choice for scaling and
  machine-level isolation.
- **Point a container backend at a remote engine** to run contained workloads on a
  bigger machine: a Podman `ssh://` connection, a remote Docker host or a kubeconfig
  context.
- **Use Isolattice on the remote worker** to contain each command the worker runs:
  install Isolattice on that machine and use a local backend there.

## Comparison

| | Remote execution systems | Isolattice today |
| --- | --- | --- |
| Runs work on another host | Yes | Through a remote Podman, Docker or Kubernetes engine, or by running Isolattice on that host |
| Scheduling, retries, queues | Usually | No |
| Per-command containment | Depends on the system | Yes, per backend and policy |
| Verified process-tree cleanup | Rarely exposed | Yes |
| Remote leases and orphan reconciliation | System-specific | Proposed |

A custom `CommandRunner` can wrap engine client commands (for example to audit or
route them), but it does not make Isolattice a remote execution system. See
[custom integrations](../guides/custom-integrations.md).
