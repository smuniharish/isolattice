# Process-tree termination

Killing the first process is not enough: descendants can outlive it, hold pipes
open and keep using resources. Isolattice therefore terminates a **boundary** (a
cgroup, Job Object, container or Pod) and only reports success after it has
observed the boundary empty and released.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope. Termination evidence comes
    from the kernel or engine, which are trusted.

## What you get back

Every execution ends with a `TerminationReport`, available as
`ExecutionResult.termination`, from `handle.cancel()`, from `instance.stop()` or
`instance.kill()`, and on `SandboxTimeoutError.report`.

| Field | Meaning |
| --- | --- |
| `boundary_id` | Backend identifier of the boundary (cgroup, Job, container or Pod ID) |
| `attempts` | Termination attempts used |
| `observations` | Boundary state observations made |
| `state` | `"terminated"`, `"non_empty"` or `"unknown"` |
| `resources_released` | `True` only after the backend confirmed the boundary is gone |
| `notes` | Human-readable evidence, such as unread output that was discarded |

Only `state == "terminated"` with `resources_released=True` is success. `unknown`
never means terminated.

```python
from isolattice import ExecutionRequest, SandboxInstance, TerminationVerificationError


async def run_and_verify(instance: SandboxInstance) -> None:
    try:
        result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "sleep 30 &")))
    except TerminationVerificationError as error:
        print("cleanup unverified:", error.report)
        raise
    report = result.termination
    assert report.state == "terminated" and report.resources_released
```

The background `sleep` above outlives the shell. Backends end the boundary when the
root workload exits, so the result still arrives promptly and the descendant is gone.

## The algorithm

The shared `terminate_boundary()` coordinator runs the same steps for every backend:

1. Observe the boundary. A boundary that was created but never started is removed
   directly, which prevents a delayed start.
2. If it is running, request a graceful stop and wait up to
   `graceful_timeout`, then force it and wait up to `force_timeout`.
3. An exited or never-started boundary must be observed in the same state twice,
   `verification_interval` apart.
4. Ask the backend to remove it, then confirm it is absent.
5. Repeat up to `attempts` times, `retry_delay` apart, within `total_timeout`.

If emptiness or release cannot be verified, it raises
`TerminationVerificationError` with a report whose state is `non_empty` (still
running) or `unknown`. A failed observation counts as `unknown`, never as success.
See the [termination engine design](../architecture/11-termination-engine.md).

## How each backend terminates

| Backend | Boundary | Graceful stop | Forced stop | Release evidence |
| --- | --- | --- | --- | --- |
| Linux namespaces | Delegated cgroup v2 | `SIGTERM` to each member through a pidfd | `cgroup.kill` | Empty cgroup removed |
| Windows Job Object | Job Object | none (skipped) | `TerminateJobObject` | No active processes, Job closed |
| Windows AppContainer | Job Object | none (skipped) | `TerminateJobObject` | No active processes, Job closed |
| Podman | Container | `podman kill --signal TERM` | `podman kill --signal KILL` | Removal acknowledged and ID absent |
| Docker API | Container | `docker kill --signal TERM` | `docker kill --signal KILL` | Removal acknowledged and ID absent |
| Kubernetes | Pod | Pod deletion with UID precondition | Deletion with a one-second grace period | API server reports the UID as absent |

`Capability.VERIFIED_TERMINATION` is SUPPORTED by the Linux namespace backend and
LIMITED elsewhere. On Kubernetes, API absence is control-plane evidence; it does not
prove that a lost node stopped the process.

## Tune the policy

```python
from isolattice import SandboxPolicy, TerminationPolicy

policy = SandboxPolicy(
    termination=TerminationPolicy(
        attempts=5,
        graceful_timeout=10.0,
        force_timeout=5.0,
        verification_interval=0.25,
        retry_delay=0.5,
        total_timeout=60.0,
        cleanup_deadline=90.0,
    ),
)
```

See the [policy reference](../reference/policies.md#terminationpolicy) for defaults.

## When cleanup outlives the caller

If `cleanup_deadline` expires before termination finishes, the caller gets
`TerminationVerificationError` (state `unknown`) and the instance keeps owning the
cleanup as a late cleanup. Watch for these warning [events](../reference/events.md):

- `termination.unknown`: the caller stopped waiting.
- `late-cleanup.finished`: the late cleanup ended; `released` says whether it worked.
- `late-cleanup.failed`: the late cleanup raised; reconcile manually.

## Related pages

- [Timeouts and cancellation](timeout-and-cancellation.md)
- [Process containment design](../architecture/10-process-containment.md)
- [Process escape prevention](../security/process-escape-prevention.md)
- [Failure scenarios example](https://github.com/smuniharish/isolattice/blob/main/examples/18-failure-scenarios/podman_failures.py)
