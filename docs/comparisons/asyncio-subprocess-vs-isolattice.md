# asyncio subprocess versus Isolattice

[`asyncio.create_subprocess_exec`](https://docs.python.org/3/library/asyncio-subprocess.html)
gives you async process control with streams. Isolattice is also async, but each
workload runs inside a backend boundary under a policy, with bounded output and
verified cleanup. Several Isolattice backends use asyncio subprocesses internally to
drive engine clients and helpers; the isolation comes from the backend, not from
asyncio.

## When to use which

- **Use asyncio subprocess** for trusted commands in an async application, when you
  are happy to manage buffering, timeouts and child cleanup yourself.
- **Use Isolattice** when the command needs limits, no network, an explicit
  environment or verified cleanup of everything it started.

## Comparison

| | asyncio subprocess | Isolattice |
| --- | --- | --- |
| Streaming | `StreamReader` pipes; buffering is yours | `handle.output()` with backpressure and size caps |
| Timeout and cancel | `wait_for` and `proc.kill()` on the child | Deadline or `cancel()` terminates the whole boundary |
| Cleanup evidence | Child exit code | `TerminationReport` (`terminated`, `non_empty`, `unknown`) |
| Isolation and limits | None | Per backend and policy |

## Example

```python
import asyncio


async def run_plain() -> bytes:
    process = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", "echo hi", stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    return stdout
```

```python
from isolattice import ExecutionRequest, SandboxInstance


async def run_isolated(instance: SandboxInstance) -> None:
    handle = await instance.start(ExecutionRequest(argv=("/bin/sh", "-c", "echo hi")))
    async for chunk in handle.output():
        print(chunk.stream, chunk.data)
    result = await handle.wait()
    print(result.exit_code, result.termination.state)
```

If `wait_for` times out in the first example, the child keeps running until you kill
it, and its descendants may survive that. See
[timeouts and cancellation](../guides/timeout-and-cancellation.md).
