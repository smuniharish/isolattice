# 03. Public API specification

## Principles

- Everything importable from `isolattice` is listed in `isolattice.__all__`; names not
  listed there are private.
- Execution and lifecycle operations are `async`; there is no synchronous execution
  API. Models are frozen dataclasses validated on construction (`TypeError` or
  `ValueError`).
- Owners are async context managers. Leaving the context stops and verifies every
  owned boundary.
- Optional features that a backend cannot provide raise `SandboxCapabilityError`;
  they never succeed as no-ops.

The generated reference is in the [API section](../api/index.md); this page is the
architectural contract.

## Exports

| Group | Names |
| --- | --- |
| Runtime | `Sandbox`, `SandboxInstance`, `ExecutionHandle` |
| Policy and requests | `SandboxPolicy`, `TerminationPolicy`, `ResourceBudget`, `NetworkAccess`, `ExecutionRequest` |
| Results and reports | `ExecutionResult`, `OutputChunk`, `TerminationReport`, `BackendReport`, `Capability`, `CapabilityState`, `CapabilityEvidence` |
| Backends | `PodmanBackend`, `DockerBackend`, `KubernetesBackend`, `LinuxNamespaceBackend`, `WindowsJobBackend`, `WindowsAppContainerBackend` |
| Backend extension | `Backend`, `BackendSession`, `Boundary` |
| Termination extension | `TerminationDriver`, `Observation`, `BoundaryState`, `terminate_boundary` |
| Command extension | `CommandRunner`, `SubprocessRunner` |
| Artifacts | `ArtifactStore`, `LocalArtifactStore`, `S3ArtifactStore`, `S3Credentials`, `ArtifactManifest`, `ArtifactKind`, `validate_artifact_key` |
| Events | `EventSink`, `LoggingEventSink`, `SandboxEvent`, `EventKind` |
| Errors | `SandboxError`, `SandboxCapabilityError`, `SandboxStateError`, `SandboxBackendError`, `SandboxExecutionError`, `SandboxTimeoutError`, `SandboxTerminationError`, `TerminationVerificationError`, `SandboxArtifactError` |
| Metadata | `__version__` |

`isolattice.testing` additionally provides `check_artifact_store()` and
`ConformanceError`.

## Runtime

```text
Sandbox(*, backend: Backend, artifact_store: ArtifactStore | None = None,
        event_sink: EventSink | None = None, staging_directory: PathLike | None = None)
  async __aenter__() / __aexit__()
  async discover() -> tuple[BackendReport, ...]
  async create(*, policy: SandboxPolicy | None = None,
               workspace_snapshot: str | None = None,
               container_snapshot: str | None = None) -> SandboxInstance
  async close() -> None

SandboxInstance
  sandbox_id: str
  policy: SandboxPolicy
  workspace_path: str | None              # property
  pending_late_cleanups: int              # property
  async __aenter__() / __aexit__()
  async start(request: ExecutionRequest) -> ExecutionHandle
  async run(request: ExecutionRequest) -> ExecutionResult
  async snapshot(key: str) -> ArtifactManifest
  async restore(key: str, *, timeout: float | None = None) -> ExecutionHandle
  async stop() -> tuple[TerminationReport, ...]
  async kill() -> tuple[TerminationReport, ...]
  async close() -> None

ExecutionHandle
  execution_id: str
  output() -> AsyncIterator[OutputChunk]  # one consumer
  async wait() -> ExecutionResult
  async cancel(*, force: bool = False) -> TerminationReport | None
  async checkpoint(key: str) -> ArtifactManifest
  async snapshot_container(key: str) -> ArtifactManifest
```

`Sandbox` defaults `event_sink` to `LoggingEventSink()` and `policy` to
`SandboxPolicy()`. Artifact operations fail with `SandboxCapabilityError` when no
`artifact_store` was given.

## Models

```text
SandboxPolicy(network=NetworkAccess.NONE,
              read_only_root=True, memory_bytes=None, cpu_cores=None,
              process_limit=None, instance_budget: ResourceBudget | None = None,
              scratch_bytes=None, workspace=False,
              required_capabilities: tuple[Capability, ...] = (),
              execution_timeout=30.0, max_concurrent_executions=4,
              output_queue_chunks=16, stdout_limit=1048576, stderr_limit=1048576,
              termination=TerminationPolicy())
TerminationPolicy(attempts=3, graceful_timeout=5.0, force_timeout=2.0,
                  verification_interval=0.5, retry_delay=1.0,
                  total_timeout=60.0, cleanup_deadline=90.0)
ResourceBudget(memory_bytes=None, cpu_cores=None, process_limit=None)
ExecutionRequest(argv: tuple[str, ...], stdin: bytes | None = None,
                 environment: Mapping[str, str] = {}, cwd="/", timeout: float | None = None)
ExecutionResult(sandbox_id, execution_id, exit_code, stdout, stderr,
                stdout_truncated, stderr_truncated, duration, termination)
OutputChunk(stream: "stdout" | "stderr", data: bytes)
TerminationReport(boundary_id, attempts, observations,
                  state: "terminated" | "non_empty" | "unknown",
                  resources_released, notes=())
BackendReport(name, available, capabilities: Mapping[Capability, CapabilityEvidence],
              limitations=(), error=None)
CapabilityEvidence(state: CapabilityState, detail: str)
ArtifactManifest(kind: ArtifactKind, backend, format, archive, sha256, size, created_at)
```

## Execution semantics

- `ExecutionRequest.argv` is an argv vector; no host shell interprets it. The
  environment starts empty: only the given variables are passed, with portable names
  and at most 64 KiB in total. `cwd` is an absolute path inside the boundary.
- `run()` drains both streams into captures bounded by `stdout_limit` and
  `stderr_limit` and sets the truncation flags. `start()` returns a handle whose
  `output()` yields chunks with backpressure from a queue of `output_queue_chunks`;
  consume it while awaiting `wait()`.
- A nonzero exit code is a result, not an error. A deadline raises
  `SandboxTimeoutError` carrying the cleanup report.
- An instance admits up to `max_concurrent_executions` concurrent executions, each in
  its own boundary. Cancelling one execution terminates only its boundary.
- `stop()` terminates gracefully, `kill()` skips the grace period but not
  verification, and `close()` equals `stop()`. Concurrent calls share one owned stop
  task; repeated calls return its outcome.
- `snapshot()` requires `workspace=True` and no active execution.
  `checkpoint()` and `snapshot_container()` require a running execution.

## Example

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(connection="isolattice-rootless", image="docker.io/library/alpine:3.22")
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("echo", "hello")))
            print(result.exit_code, result.stdout, result.termination.state)


asyncio.run(main())
```

## Extension points

All extension points are abstract base classes, passed explicitly to their owner. Their
contracts are in [backend contracts](07-backend-contracts.md), the
[termination engine](11-termination-engine.md), [snapshots](13-snapshot-architecture.md)
and [observability](18-observability.md). Errors are specified in the
[exception model](12-exception-model.md).
