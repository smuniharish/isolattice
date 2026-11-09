# Process management

This guide covers running commands in a sandbox instance: one-shot runs, streaming
executions, input, environment, working directory and concurrency.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope. The Windows Job Object backend
    is containment only, not a security boundary.

## Lifecycle at a glance

1. `Sandbox(backend=...)` owns one backend and its artifact store and event sink.
2. `await sandbox.create(policy=...)` validates the policy with the backend and
   returns a `SandboxInstance`.
3. Each `instance.run(...)` or `instance.start(...)` creates a **new** containment
   boundary for that execution. Only the optional workspace persists between
   executions.
4. Closing the instance terminates every execution and verifies release.

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(connection="podman-machine-default", image="localhost/tools:latest")
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/echo", "hello")))
            print(result.exit_code, result.stdout)


asyncio.run(main())
```

## Describe the command

`ExecutionRequest` is an argv tuple; strings are never passed to a host shell.

| Field | Default | Rules |
| --- | --- | --- |
| `argv` | required | Non-empty; `argv[0]` must not start with `-`; no NUL bytes |
| `stdin` | `None` | `bytes` written to the workload, then closed |
| `environment` | `{}` | Identifier names; values without NUL, CR or LF; 64 KiB total |
| `cwd` | `"/"` | Absolute POSIX path without `..` |
| `timeout` | `None` | Seconds; overrides `SandboxPolicy.execution_timeout` |

```python
from isolattice import ExecutionRequest

request = ExecutionRequest(
    argv=("/bin/sh", "-c", 'read name; echo "hello $name from $GREETING_SOURCE"'),
    stdin=b"world\n",
    environment={"GREETING_SOURCE": "isolattice"},
    cwd="/tmp",
    timeout=10.0,
)
```

The workload environment contains only what you pass, plus variables that the
engine or platform adds. Container backends write explicit values to a temporary
file read by the engine, and argv appears in engine metadata: neither is a secret
delivery mechanism.

On Windows, `argv[0]` must be an absolute path such as
`C:\Python312\python.exe`; no search-path lookup happens. The Job Object backend
maps `cwd` below its `working_directory`.

## One-shot runs

`instance.run(request)` waits for completion and returns an `ExecutionResult`:

| Field | Meaning |
| --- | --- |
| `exit_code` | Authoritative exit code reported by the backend |
| `stdout`, `stderr` | Captured bytes, bounded by `stdout_limit` and `stderr_limit` |
| `stdout_truncated`, `stderr_truncated` | Whether capture hit its limit |
| `duration` | Wall-clock seconds |
| `termination` | `TerminationReport` proving the boundary was emptied and released |
| `sandbox_id`, `execution_id` | Identifiers that also appear in events |

A non-zero exit code is a normal result, not an exception.

## Stream output

`instance.start(request)` returns an `ExecutionHandle` as soon as the workload starts.
Consume `handle.output()` while it runs; the output queue holds
`output_queue_chunks` chunks of up to 64 KiB and then back-pressures the workload.

```python
from isolattice import ExecutionRequest, SandboxInstance


async def stream(instance: SandboxInstance) -> int:
    handle = await instance.start(
        ExecutionRequest(argv=("/bin/sh", "-c", "for i in 1 2 3; do echo $i; sleep 1; done"))
    )
    async for chunk in handle.output():
        print(chunk.stream, chunk.data)
    result = await handle.wait()
    return result.exit_code
```

Only one consumer may iterate `output()`; a second call raises `SandboxStateError`.
The stream receives all output, while `ExecutionResult.stdout` keeps only the first
`stdout_limit` bytes. If you never read the stream, a chatty workload stalls until its
timeout.

## Concurrency

An instance runs up to `max_concurrent_executions` (default 4) executions at once;
starting another raises `SandboxStateError`. Each execution has its own boundary and
budgets, unless you set an [instance budget](resource-limits.md#instance-budgets).

```python
import asyncio

from isolattice import ExecutionRequest, SandboxInstance


async def fan_out(instance: SandboxInstance) -> list[int]:
    requests = [ExecutionRequest(argv=("/bin/echo", str(n))) for n in range(4)]
    results = await asyncio.gather(*(instance.run(request) for request in requests))
    return [result.exit_code for result in results]
```

## Stop everything

`instance.stop()` terminates every running execution gracefully then forcibly;
`instance.kill()` skips the graceful phase. Both return one `TerminationReport` per
boundary and freeze admissions, so the instance accepts no new executions. Leaving
the `async with` block calls `close()`, which stops executions, releases the
instance's resources and raises a `BaseExceptionGroup` if any cleanup failed. See [process-tree termination](process-tree-termination.md).

## Related pages

- [Timeouts and cancellation](timeout-and-cancellation.md)
- [Execution model](../concepts/execution-model.md)
- [Sandbox API](../api/sandbox.md) and [models](../api/models.md)
- [Streaming example](https://github.com/smuniharish/isolattice/blob/main/examples/02-execution/podman_io.py)
