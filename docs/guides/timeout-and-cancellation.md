# Timeouts and cancellation

Every execution has a deadline, and every way of ending it early (timeout, task
cancellation, `cancel()`, `stop()`, `kill()`) goes through the same verified
termination path. This guide shows how to control each.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope. The Windows Job Object backend
    is containment only, not a security boundary.

## Execution timeouts

`SandboxPolicy.execution_timeout` (default 30 seconds) applies to every execution
of an instance. `ExecutionRequest.timeout` overrides it for one execution.

```python
from isolattice import ExecutionRequest, SandboxInstance, SandboxTimeoutError


async def run_with_deadline(instance: SandboxInstance) -> None:
    request = ExecutionRequest(argv=("/bin/sleep", "60"), timeout=2.0)
    try:
        await instance.run(request)
    except SandboxTimeoutError as error:
        report = error.report
        print("timed out; boundary state:", report.state if report else "not reported")
```

When the deadline expires, Isolattice terminates the boundary and then raises
`SandboxTimeoutError` from `wait()` or `run()`. The error is also a `TimeoutError`.
Its `report` attribute holds the `TerminationReport` of the cleanup.

When a request sets `timeout`, the Kubernetes backend also gives the Pod an
`activeDeadlineSeconds` backstop, so the cluster ends it if the client disappears.

## Cancel one execution

`ExecutionHandle.cancel()` stops a started execution and waits for verified cleanup.
It returns the `TerminationReport`, or `None` if no boundary was created yet.

```python
import asyncio

from isolattice import ExecutionRequest, SandboxInstance


async def cancel_after(instance: SandboxInstance, seconds: float) -> None:
    handle = await instance.start(ExecutionRequest(argv=("/bin/sleep", "60")))
    await asyncio.sleep(seconds)
    report = await handle.cancel()
    if report is not None:
        print(report.state, report.resources_released)
```

`cancel(force=True)` skips the graceful phase and forces termination immediately.

## Cancel the awaiting task

Cancelling a task that awaits `run()` or `wait()` also stops the execution. The
`asyncio.CancelledError` propagates after cleanup finishes; a cleanup failure is
raised together with it.

```python
import asyncio

from isolattice import ExecutionRequest, SandboxInstance


async def bounded(instance: SandboxInstance) -> None:
    async with asyncio.timeout(5):
        await instance.run(ExecutionRequest(argv=("/bin/sleep", "60")))
```

Here `asyncio.timeout` cancels `run()` after five seconds; Isolattice terminates the
boundary before `TimeoutError` reaches your code.

## Stop or kill an instance

| Call | Graceful phase | Scope |
| --- | --- | --- |
| `handle.cancel()` | yes | one execution |
| `handle.cancel(force=True)` | no | one execution |
| `instance.stop()` / `instance.close()` | yes | every execution; the instance accepts no more |
| `instance.kill()` | no | every execution; the instance accepts no more |

The graceful phase lasts `TerminationPolicy.graceful_timeout` seconds. The Windows
backends always skip it, because Job Objects have no graceful stop.

## Bound cleanup time

`TerminationPolicy` bounds how long termination may take:

```python
from isolattice import SandboxPolicy, TerminationPolicy

policy = SandboxPolicy(
    termination=TerminationPolicy(
        graceful_timeout=2.0,
        force_timeout=2.0,
        total_timeout=20.0,
        cleanup_deadline=30.0,
    ),
)
```

`total_timeout` bounds the termination coordinator. `cleanup_deadline` bounds how long
the caller waits from the moment cleanup begins. If it expires, `wait()`, `cancel()`
or `close()` raises `TerminationVerificationError` with state `unknown` and
`resources_released=False`. The unfinished cleanup stays owned by the instance and
is counted by `SandboxInstance.pending_late_cleanups`; the outcome is reported as a
`late-cleanup.finished` or `late-cleanup.failed` [event](../reference/events.md).

!!! warning "Deadlines are not hard real time"
    A stalled kernel, engine or event loop can delay the caller. Abrupt termination
    of your Python process does not guarantee cleanup. Container backends label
    their resources with `io.isolattice.sandbox` and `io.isolattice.execution` so an
    operator can reconcile leftovers.

## Related pages

- [Process-tree termination](process-tree-termination.md)
- [Policy reference](../reference/policies.md#terminationpolicy)
- [Streaming and cancellation example](https://github.com/smuniharish/isolattice/blob/main/examples/06-async-and-cancellation/podman_stream_cancel.py)
