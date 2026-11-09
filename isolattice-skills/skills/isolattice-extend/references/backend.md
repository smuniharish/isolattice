# Backend, BackendSession and TerminationDriver contracts

A new isolation technology needs three classes. Read the built-in backends in
`isolattice/backends/` before starting; `PodmanBackend` is the most complete.

## Backend

```python
class Backend(ABC):
    @abstractmethod
    async def discover(self) -> BackendReport: ...
    @abstractmethod
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession: ...
```

- `discover` probes the host and returns `BackendReport(name, available,
  capabilities, limitations, error)`. `capabilities` maps each `Capability`
  to `CapabilityEvidence(state, detail)` with state `SUPPORTED`, `LIMITED` or
  `UNSUPPORTED`. Report `SUPPORTED` only for a guarantee the code enforces and
  a test on a real host verified. Never infer it from the OS name.
- `prepare` refuses, with `SandboxCapabilityError`, every policy setting it
  cannot enforce and every required capability that is not `SUPPORTED`. It
  refuses before creating anything.

## BackendSession

Abstract: `create_boundary`, `start_boundary`, `workload_exited`, `exit_code`
and `terminate`. `close` releases per-instance resources.

- `create_boundary(request, sandbox_id, execution_id, register)` creates the
  containment boundary without starting the workload and calls `register`
  with its `Boundary` before any step that can fail, so the runtime can always
  clean it up.
- `start_boundary(boundary, request)` starts the workload inside the boundary
  and returns the client process whose pipes carry stdout and stderr.
- `terminate(boundary, policy, force_requested)` stops and verifies. Build a
  `TerminationDriver` and return `await terminate_boundary(driver, policy,
  force_requested)`.

Optional members raise `SandboxCapabilityError` unless overridden:
`export_workspace` and `import_workspace` (workspace snapshots),
`snapshot_boundary` and `import_root` (container snapshots), `checkpoint` and
`restore_boundary` (CRIU). Override only what the technology enforces.

## TerminationDriver

```python
class TerminationDriver(ABC):
    async def observe(self) -> Observation: ...
    async def signal(self, *, force: bool) -> None: ...
    async def remove(self) -> None: ...
    async def exists(self) -> bool: ...
```

`terminate_boundary` signals gracefully, then forcefully, observes twice and
removes the boundary within the `TerminationPolicy` deadlines. `observe`
returns `Observation(state, exit_code, detail)`; return
`BoundaryState.UNKNOWN` when the state cannot be read. `UNKNOWN` never counts
as terminated.

Identify the boundary by an immutable backend ID (a container ID, cgroup path
or Job Object handle), never by a PID that can be reused.

## Evidence a backend needs

- Unit tests with a fake engine for every refusal and every error path.
- Live tests on a real host for every capability reported as `SUPPORTED`:
  network blocked, root read-only, each limit enforced, every descendant
  process gone after termination, cleanup after cancellation.
- Each limitation documented in `BackendReport.limitations`.

Do not report a capability as `SUPPORTED` without that evidence, and document
the backend's threat model honestly: never claim it defends against kernel
exploits unless it adds a boundary beyond the shared kernel.
