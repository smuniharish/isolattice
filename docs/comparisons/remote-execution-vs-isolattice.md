# Remote execution versus Isolattice

Remote execution covers anything that runs work on another machine: SSH, task queues
such as Celery, hosted code-execution sandboxes and cloud batch services.

!!! note "Proposed in Isolattice"
    A remote backend and its remote backend protocol are proposed, not implemented.
    There is no remote API to call today. See the [remote backend](../backends/remote.md)
    page and the [remote backend protocol](../architecture/15-remote-backend-protocol.md).

## When to use which

- **Use an existing remote system** whenever you need work to run elsewhere today. Task
  queues, SSH and hosted sandboxes are the right choice for distribution, scaling and
  machine-level isolation.
- **Use Isolattice on the remote worker** to contain each command the worker runs:
  install Isolattice on that machine and use a local backend there.
- **Use `KubernetesBackend`** if "remote" for you means a cluster: it runs Pods through
  the Kubernetes API from wherever your application runs.

## Comparison

| | Remote execution systems | Isolattice today |
| --- | --- | --- |
| Runs work on another host | Yes | Only via `KubernetesBackend` (cluster Pods) or by running Isolattice on that host |
| Scheduling, retries, queues | Usually | No |
| Per-command containment | Depends on the system | Yes, per backend and policy |
| Verified process-tree cleanup | Rarely exposed | Yes |
| Remote leases and orphan reconciliation | System-specific | Proposed |

A custom `CommandRunner` can wrap engine client commands (for example to audit or
route them), but it does not make Isolattice a remote execution system. See
[custom integrations](../guides/custom-integrations.md).
