# Process escape prevention

A workload can fork, detach, start a new session or daemonize. Killing the direct
child does not stop those descendants, and PIDs can be reused. Isolattice therefore
owns and terminates a **boundary**, never a PID. The design is in
[process containment](../architecture/10-process-containment.md) and the
[termination engine](../architecture/11-termination-engine.md); the user guide is
[process-tree termination](../guides/process-tree-termination.md).

## Boundary identity

| Backend | Boundary | Emptiness evidence |
| --- | --- | --- |
| Podman, Docker | Container ID (one container per execution) | Engine-reported state, then owned removal |
| Kubernetes | Pod UID (one Pod per execution) | UID-preconditioned deletion, Pod absent from the API server |
| Linux namespace | Owned cgroup v2 leaf in a PID namespace | `cgroup.kill`, then `populated 0` before the leaf is removed |
| Windows Job, AppContainer | Job Object that descendants cannot break away from | Job accounting reports zero active processes |

Process groups, sessions and recursive PID scans are not used as containment.

## Verified termination

Every timeout, cancellation, `stop()`, `kill()` and close runs the same coordinator:
graceful stop, force, repeated observation, release, confirmation of absence and retry
within `TerminationPolicy` limits. The result is a `TerminationReport`:

- Success is only `state == "terminated"` with `resources_released=True`.
- `non_empty` and `unknown` are failures and raise `SandboxTerminationError` or
  `TerminationVerificationError`, carrying the report in `error.report`.
- **`unknown` never means terminated.**

## Cleanup deadline and late cleanup

`TerminationPolicy.cleanup_deadline` (default 90 seconds) is a hard limit on how long a
caller waits for cleanup after a stop is requested. When it expires:

1. The caller gets `TerminationVerificationError` with a report in state `unknown` and
   notes that name the backend resource to reconcile if it persists.
2. Cleanup keeps running in the background, owned by the `SandboxInstance`.
3. `instance.pending_late_cleanups` counts those tasks. Each one ends with a
   `LATE_CLEANUP_FINISHED` or `LATE_CLEANUP_FAILED` event.
4. While late cleanups are pending, `instance.close()` retains instance-wide resources
   and raises `TerminationVerificationError` instead of reporting success.

```python
from isolattice import ExecutionRequest, SandboxInstance, TerminationVerificationError


async def run_checked(instance: SandboxInstance) -> None:
    try:
        result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "sleep 600 &")))
    except TerminationVerificationError as error:
        print("cleanup unverified:", error.report)
        print("still cleaning up:", instance.pending_late_cleanups)
        raise
    assert result.termination.state == "terminated"
    assert result.termination.resources_released
```

## Residual risks

- Processes created on the workload's behalf by system services (for example Windows
  brokers) are outside the Job Object.
- If the host process is killed, nothing can run cleanup. Container and Pod resources
  carry Isolattice labels for manual reconciliation; Windows AppContainer profiles are
  not reconciled.
- Kernel and engine defects are out of scope. See the
  [threat model](threat-model.md).
