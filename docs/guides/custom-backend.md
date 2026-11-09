# Custom backends

A backend adapts one isolation mechanism (a container engine, a VM manager, an OS
primitive) to Isolattice's lifecycle and termination guarantees. This guide builds a
complete skeleton around a hypothetical engine client.

!!! warning "You own the security claims"
    A custom backend is trusted code. Isolattice cannot check that your isolation is
    real: it relies on the evidence you report and on your `prepare()` refusing what
    you cannot enforce. Report a capability as SUPPORTED only with tested evidence,
    and do not describe the backend as qualified for hostile code without an
    independent security review. Read the
    [backend contracts](../architecture/07-backend-contracts.md) first.

## The extension points

All four are abstract base classes exported from `isolattice`:

| Class | You implement | Responsibility |
| --- | --- | --- |
| `Backend` | `discover()`, `prepare()` | Report evidence; validate a policy and open a session |
| `BackendSession` | `create_boundary()`, `start_boundary()`, `workload_exited()`, `exit_code()`, `terminate()` | Own one instance's boundaries and instance-wide resources |
| `TerminationDriver` | `observe()`, `signal()`, `remove()`, `exists()` | Control one boundary for the shared termination coordinator |
| `Boundary` | (a dataclass you create) | Identify one execution's containment unit |

`BackendSession` also has optional methods that raise `SandboxCapabilityError` unless
you override them: `close()` (default does nothing), `export_workspace()`,
`import_workspace()`, `checkpoint()`, `restore_boundary()`, `snapshot_boundary()` and
`import_root()`. Override `workspace_path` when you provide a workspace. See the
[extension API](../api/extension.md) for the full signatures.

## The engine the skeleton adapts

The skeleton talks to an engine through a small client interface that you would
implement for your platform. Every operation addresses one immutable identifier:

```python
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping

from isolattice import (
    Backend,
    BackendReport,
    BackendSession,
    Boundary,
    BoundaryState,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    ExecutionRequest,
    NetworkAccess,
    Observation,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxPolicy,
    TerminationDriver,
    TerminationPolicy,
    TerminationReport,
    terminate_boundary,
)


class Engine(ABC):
    """Client for the isolation engine this backend adapts."""

    @abstractmethod
    async def probe(self) -> Mapping[Capability, CapabilityEvidence]:
        """Inspect the live engine and return tested evidence."""

    @abstractmethod
    async def create(self, name: str, request: ExecutionRequest, labels: Mapping[str, str]) -> str:
        """Create an unstarted unit and return its immutable identifier."""

    @abstractmethod
    async def attach(self, identifier: str, *, stdin: bool) -> asyncio.subprocess.Process:
        """Start the unit and return a client process wired to its stdio."""

    @abstractmethod
    async def observe(self, identifier: str) -> Observation:
        """Report CREATED, RUNNING, EXITED (with exit code), ABSENT or UNKNOWN."""

    @abstractmethod
    async def stop(self, identifier: str, *, force: bool) -> None:
        """Signal every process in the unit."""

    @abstractmethod
    async def remove(self, identifier: str) -> None:
        """Delete a stopped unit; never force-delete a running one."""
```

## The termination driver

`terminate_boundary()` implements the verified algorithm (graceful stop, forced stop,
two matching observations, removal, absence check, retries and deadlines). Your driver
only reports facts and performs single actions:

```python
class ExampleDriver(TerminationDriver):
    signal_releases = False

    def __init__(self, engine: Engine, boundary: Boundary) -> None:
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested
        self._engine = engine

    async def observe(self) -> Observation:
        return await self._engine.observe(self.boundary_id)

    async def signal(self, *, force: bool) -> None:
        await self._engine.stop(self.boundary_id, force=force)

    async def remove(self) -> None:
        await self._engine.remove(self.boundary_id)

    async def exists(self) -> bool:
        observation = await self._engine.observe(self.boundary_id)
        return observation.state is not BoundaryState.ABSENT
```

Driver rules:

- Raise `SandboxBackendError` when you cannot tell. The coordinator records it as
  UNKNOWN, which never counts as terminated.
- `exists()` treats UNKNOWN as present, so an unreachable engine fails closed.
- Set `signal_releases = True` only if a forced signal also deletes the unit (as
  Kubernetes Pod deletion does).
- Address the immutable identifier, never a reusable name or PID.

## The session

```python
class ExampleSession(BackendSession):
    backend_name = "example"

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        name = f"example-{sandbox_id}-{execution_id}"
        labels = {"io.example.sandbox": sandbox_id, "io.example.execution": execution_id}
        identifier = await self._engine.create(name, request, labels)
        boundary = Boundary(identifier, name, sandbox_id, execution_id)
        register(boundary)
        return boundary

    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        boundary.start_requested = True
        return await self._engine.attach(boundary.boundary_id, stdin=request.stdin is not None)

    async def workload_exited(self, boundary: Boundary) -> None:
        observation = await self._engine.observe(boundary.boundary_id)
        if observation.state is BoundaryState.RUNNING:
            await self._engine.stop(boundary.boundary_id, force=True)

    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        observation = await self._engine.observe(boundary.boundary_id)
        if observation.state is not BoundaryState.EXITED or observation.exit_code is None:
            raise SandboxBackendError("the engine did not report the workload's exit code")
        return observation.exit_code

    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        driver = ExampleDriver(self._engine, boundary)
        return await terminate_boundary(driver, policy, force_requested)
```

Session rules:

- **Register early.** Call `register(boundary)` as soon as the engine may have accepted
  the allocation, before any further await. If creation fails in a way that may have
  created the unit, register it anyway or raise with enough detail (name and labels)
  for an operator to reconcile it.
- **Mark the start.** Set `boundary.start_requested = True` before asking the engine to
  start, so termination removes a created-but-unstarted unit instead of waiting.
- **Return a real client.** The process from `start_boundary()` must have `stdout` and
  `stderr` pipes, and a `stdin` pipe when `request.stdin` is set. If starting fails
  after a local client was spawned, kill and reap it before raising.
- **End the boundary with its root.** Descendants can hold output pipes open; end them
  in `workload_exited()` so draining reaches end of file.
- **Prefer engine evidence.** `exit_code()` should return what the engine reports, not
  what an attach client returned, and fail when it cannot tell.
- **Release instance resources in `close()`.** The runtime calls it exactly once after
  every boundary was terminated.

## The backend

```python
class ExampleBackend(Backend):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def discover(self) -> BackendReport:
        capabilities = {
            capability: CapabilityEvidence(CapabilityState.UNSUPPORTED, "not implemented")
            for capability in Capability
        }
        try:
            capabilities.update(await self._engine.probe())
        except (OSError, SandboxBackendError) as error:
            return BackendReport(
                name="example",
                available=False,
                capabilities=capabilities,
                error=type(error).__name__,
            )
        return BackendReport(
            name="example",
            available=True,
            capabilities=capabilities,
            limitations=(
                "Containers share the host kernel; kernel or engine exploits are out of scope.",
            ),
        )

    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        if policy.network is not NetworkAccess.NONE:
            raise SandboxCapabilityError("only network=NONE is implemented")
        unsupported = {
            "memory_bytes": policy.memory_bytes,
            "cpu_cores": policy.cpu_cores,
            "process_limit": policy.process_limit,
            "instance_budget": policy.instance_budget,
            "scratch_bytes": policy.scratch_bytes,
        }
        for field, value in unsupported.items():
            if value is not None:
                raise SandboxCapabilityError(f"{field} is not implemented")
        if policy.workspace or not policy.read_only_root:
            raise SandboxCapabilityError("workspaces and writable roots are not implemented")
        report = await self.discover()
        if not report.available:
            raise SandboxCapabilityError(f"the example engine is unavailable: {report.error}")
        for capability in policy.required_capabilities:
            evidence = report.capabilities[capability]
            if evidence.state is not CapabilityState.SUPPORTED:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} is {evidence.state.value}"
                )
        return ExampleSession(self._engine)
```

Backend rules:

- **Report every capability.** Start from UNSUPPORTED and raise a state only with
  evidence probed on this host. LIMITED means "partly enforced"; say what is missing
  in `detail`.
- **Fail closed in `prepare()`.** Reject every policy field you do not enforce,
  including budgets, network modes and filesystem options. Never silently drop one.
  For example, a backend that caps `scratch_bytes` but cannot cap inodes must still
  reject `scratch_inodes`.
- **State your threat model** in `limitations`: what the boundary shares with the
  host and which exploits are out of scope.
- **Check required capabilities** against fresh evidence, not a cached report.
- **Keep errors free of workload data.** Do not put argv, environment or output in
  exception messages; include identifiers instead.

## Use it

```python
from isolattice import ExecutionRequest, Sandbox


async def run_on_example(engine: Engine) -> int:
    policy = SandboxPolicy()
    async with Sandbox(backend=ExampleBackend(engine)) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/true",)))
            return result.exit_code
```

## Optional features

| Feature | Override | Contract |
| --- | --- | --- |
| Workspace | `workspace_path`, `close()` | Create in `prepare()`, mount in every boundary, remove in `close()` |
| Workspace snapshots | `export_workspace()`, `import_workspace()` | Write or read an uncompressed tar; the runtime validates and seals it |
| Container snapshots | `snapshot_boundary()`, `import_root()` | Quiesce a running boundary, always resume it |
| Checkpoints | `checkpoint()`, `restore_boundary()` | Leave the source running; restore registers a new boundary |

Report `SNAPSHOTS`, `CHECKPOINTS` or `WORKSPACE` as SUPPORTED or LIMITED only when you
override the matching methods.

## Test it

- Unit-test the driver against a fake engine with scripted observations, including
  UNKNOWN and failing `remove()`.
- Run real-engine tests that start a process tree, cancel it and verify the engine
  stops reporting the unit. See [testing](../development/testing.md).
- Compare with the built-in implementations, for example
  [the Windows Job Object backend](https://github.com/smuniharish/isolattice/blob/main/src/isolattice/backends/windows.py).

## Related pages

- [Backend contracts](../architecture/07-backend-contracts.md)
- [Termination engine](../architecture/11-termination-engine.md)
- [Capability model](../architecture/06-capability-model.md)
- [Custom integrations](custom-integrations.md)
