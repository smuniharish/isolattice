# multiprocessing versus Isolattice

[`multiprocessing`](https://docs.python.org/3/library/multiprocessing.html) and
[`concurrent.futures.ProcessPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html)
run Python functions in worker processes for parallelism. Workers share your user,
filesystem, network and usually your code and data. Isolattice runs commands (argv),
not Python callables, inside an isolated boundary.

## When to use which

- **Use multiprocessing** to spread trusted CPU-bound Python work across cores. It
  passes objects by pickling, which Isolattice does not do, and is the better tool for
  parallelism.
- **Use Isolattice** to run a separate program, possibly generated or third-party code,
  with limits, no network and verified cleanup. Pass data through stdin, the workspace
  or arguments.

## Comparison

| | multiprocessing | Isolattice |
| --- | --- | --- |
| Unit of work | Python callable | Command `argv` |
| Data exchange | Pickled objects, queues, shared memory | Bytes over stdin/stdout, workspace files |
| Isolation | None (same user and host access) | Backend boundary per policy |
| Limits and cleanup | None built in; workers may leave children | Per-execution limits; verified boundary termination |
| Purpose | Parallelism | Containment |

## Example

Run untrusted Python as a separate interpreter rather than in a pool worker:

```python
from isolattice import ExecutionRequest, SandboxInstance


async def evaluate(instance: SandboxInstance, source: bytes) -> bytes:
    result = await instance.run(
        ExecutionRequest(argv=("/usr/local/bin/python3", "-"), stdin=source, timeout=5)
    )
    return result.stdout
```

The interpreter path must exist in the backend's image or host paths. Unpickling data
from an untrusted worker is itself unsafe, so do not mix the two approaches.
