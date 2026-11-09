# Sandbox

`Sandbox` binds one backend to optional artifact storage and an event sink.
`create()` resolves the policy against the backend's discovered capabilities
and returns a `SandboxInstance`; required capabilities that the backend cannot
enforce raise `SandboxCapabilityError` before any workload starts.

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(connection="podman-machine-default", image="python:3.14-slim")
    policy = SandboxPolicy()
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        result = await instance.run(ExecutionRequest(argv=("python", "-c", "print(42)")))
        print(result.exit_code, result.stdout)


asyncio.run(main())
```

::: isolattice.Sandbox

::: isolattice.SandboxInstance

::: isolattice.ExecutionHandle
