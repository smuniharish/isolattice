# Quickstart

This page uses the [Podman backend](../backends/podman.md) because it runs on
Linux, macOS and Windows through Podman Desktop. The code is the same for every
backend; only the backend constructor changes.

You need a Podman connection (`podman system connection list`) and an image
that is already present on it. Isolattice never pulls images:

```bash
podman --connection podman-machine-default pull docker.io/library/alpine:3.22
```

## Run a command

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(argv=("/bin/sh", "-c", "printf 'Hello from Isolattice\\n'"))
            )
    print(result.stdout.decode(), end="")
    print("exit code:", result.exit_code)
    print("termination:", result.termination.state)


asyncio.run(main())
```

```text
Hello from Isolattice
exit code: 0
termination: terminated
```

What happened:

- `Sandbox` owns the backend. `create` checks the policy against what the
  backend can enforce and returns a `SandboxInstance`.
- `run` starts the command in its own container, waits, and returns an
  `ExecutionResult` with bounded `stdout` and `stderr`. A nonzero exit code is
  data, not an exception.
- Leaving each `async with` block terminates and removes everything the
  instance owns, and verifies that it is gone.

!!! note "Use absolute paths"

    The Podman backend clears the image environment. The workload sees only
    `ExecutionRequest.environment`, so `PATH` is unset unless you pass it. Name
    executables by absolute path, such as `/bin/sh`.

## Require guarantees

A policy states what must hold. List a capability in `required_capabilities`
and creation fails unless the backend enforces it:

```python
from isolattice import Capability, SandboxPolicy

policy = SandboxPolicy(
    memory_bytes=128 * 1024 * 1024,
    process_limit=32,
    required_capabilities=(Capability.MEMORY_LIMIT, Capability.PROCESS_LIMIT),
)
```

On a connection without delegated cgroup controllers, nothing runs:

```text
isolattice.errors.SandboxCapabilityError: resource limits require delegated controllers: memory, pids
```

See [resource limits](../guides/resource-limits.md) and the
[capability model](../concepts/capability-model.md).

## Stream output

`start` returns an `ExecutionHandle`. Read its output while it runs, then wait
for the result:

```python
handle = await instance.start(
    ExecutionRequest(argv=("/bin/sh", "-c", "for i in 1 2 3; do echo tick $i; sleep 1; done"))
)
async for chunk in handle.output():
    print(chunk.stream, chunk.data.decode().strip())
result = await handle.wait()
```

```text
stdout tick 1
stdout tick 2
stdout tick 3
```

The stream has one consumer and applies backpressure. If you never read it, a
chatty workload stalls until its deadline.

## Time out and cancel

```python
from isolattice import SandboxTimeoutError

try:
    await instance.run(ExecutionRequest(argv=("/bin/sleep", "300"), timeout=2))
except SandboxTimeoutError as error:
    print("timed out:", error)

sleeper = await instance.start(ExecutionRequest(argv=("/bin/sleep", "300")))
report = await sleeper.cancel()
print("cancelled:", report.state if report else "before start")
```

```text
timed out: execution exceeded its deadline
cancelled: terminated
```

A timeout or cancellation terminates the execution's whole boundary and
verifies it before returning. `cancel` returns `None` when the boundary had not
been created yet, so there was nothing to terminate. Cancelling the task that
awaits `run` has the same effect.

## Next steps

- Read the [execution model](../concepts/execution-model.md) and
  [security model](../concepts/security-model.md).
- Choose and configure a [backend](index.md).
- Add [snapshots](../guides/snapshots.md), [checkpoints](../guides/checkpoints.md)
  or your own [integrations](../guides/custom-integrations.md).
- Run the [examples](https://github.com/smuniharish/isolattice/tree/main/examples).
