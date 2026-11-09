---
category: 5
status: index
---

# 05 Process lifecycle

This category has no dedicated script yet. The lifecycle API it covers is
implemented: `SandboxInstance.start()`, `ExecutionHandle.wait()`,
`ExecutionHandle.cancel()`, `SandboxInstance.stop()`, `SandboxInstance.kill()` and
`close()`, all backed by verified, identity-based termination.

Process-tree termination is already demonstrated by these scripts:

| Script | Lifecycle behavior |
| --- | --- |
| [`04-resource-management/windows_job_limits.py`](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/windows_job_limits.py) | Timed-out child and grandchild terminated through the Job Object |
| [`06-async-and-cancellation/podman_stream_cancel.py`](https://github.com/smuniharish/isolattice/blob/main/examples/06-async-and-cancellation/podman_stream_cancel.py) | `start()`, streamed readiness, forced `cancel()` |
| [`08-native-backends/linux_namespaces.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/linux_namespaces.py) | Background `sleep` tree cleaned up through the cgroup on timeout |
| [`08-native-backends/windows_restricted_token.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_restricted_token.py) | Descendant process verified exited after timeout |
| [`18-failure-scenarios/podman_failures.py`](https://github.com/smuniharish/isolattice/blob/main/examples/18-failure-scenarios/podman_failures.py) | Timeout with verified container release |

## Prerequisites

Those of the backend used; see the linked categories.

## Acceptance

A dedicated lifecycle example should show:

- graceful `stop()` followed by forced `kill()` on a workload that ignores the first
  signal, with the `TerminationPolicy` attempts and timeouts it used;
- children, grandchildren and background processes all gone afterwards, checked
  through the boundary (cgroup, Job Object or container), not by PID;
- `TerminationReport.state` and `resources_released` in every result;
- `TerminationVerificationError` when `cleanup_deadline` expires, with the state
  reported as unknown and the late cleanup reported by `LATE_CLEANUP_*` events.

## Cleanup

Success means the boundary is verified empty and its resources are released. A direct
child exiting is not enough. An unknown state is a failure, never a success.

## Security

- Shared-kernel backends exclude kernel or engine exploits; Windows Job Objects are
  not a security boundary.
- Identity is the boundary (container ID, cgroup, Job Object), never a reusable PID or
  a process group.

See [process management](https://isolattice.readthedocs.io/en/latest/guides/process-management/)
and [process-tree termination](https://isolattice.readthedocs.io/en/latest/guides/process-tree-termination/).
