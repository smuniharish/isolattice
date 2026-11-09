# isolattice API for applications

Everything is importable from `isolattice`. Every execution and lifecycle
operation is a coroutine; policies, requests and results are frozen
dataclasses validated on construction.

## Contents

- [Owners and instances](#owners-and-instances)
- [Policies](#policies)
- [Requests and results](#requests-and-results)
- [Streaming and cancellation](#streaming-and-cancellation)
- [Termination and cleanup](#termination-and-cleanup)
- [Snapshots and checkpoints](#snapshots-and-checkpoints)
- [Events](#events)
- [Errors](#errors)

## Owners and instances

```python
Sandbox(*, backend, artifact_store=None, event_sink=None, staging_directory=None)
await sandbox.discover() -> tuple[BackendReport, ...]
await sandbox.create(*, policy=None, workspace_snapshot=None, container_snapshot=None)
    -> SandboxInstance
await instance.run(request) -> ExecutionResult
await instance.start(request) -> ExecutionHandle
await instance.stop() / await instance.kill() -> tuple[TerminationReport, ...]
instance.workspace_path -> str | None
instance.pending_late_cleanups -> int
```

`Sandbox` and `SandboxInstance` are async context managers. Leaving the
`Sandbox` block closes every instance it created. One instance runs several
executions; each execution gets its own boundary, up to
`policy.max_concurrent_executions` at once.

## Policies

```python
SandboxPolicy(
    network=NetworkAccess.NONE,  # NONE, ENABLED, ALLOWLIST
    read_only_root=True,
    memory_bytes=None,
    cpu_cores=None,
    process_limit=None,  # per execution
    instance_budget=None,  # ResourceBudget across the instance
    scratch_bytes=None,  # capped writable scratch
    scratch_inodes=None,  # inode cap for the scratch (Linux, Docker Engine)
    workspace=False,  # persistent instance workspace
    required_capabilities=(),  # tuple[Capability, ...], fail closed
    execution_timeout=30.0,
    max_concurrent_executions=4,
    output_queue_chunks=16,
    stdout_limit=1048576,
    stderr_limit=1048576,
    termination=TerminationPolicy(),
)
ResourceBudget(memory_bytes=None, cpu_cores=None, process_limit=None)
TerminationPolicy(
    attempts=3,
    graceful_timeout=5.0,
    force_timeout=2.0,
    verification_interval=0.5,
    retry_delay=1.0,
    total_timeout=60.0,
    cleanup_deadline=90.0,
)
```

A backend enforces every limit you set or refuses the policy. List a
capability in `required_capabilities` when the application depends on it:
the backend then refuses unless its evidence for that capability is
`CapabilityState.SUPPORTED`. `INSTANCE_LIMITS`, `DISK_LIMIT`, `WORKSPACE` and
`SNAPSHOTS` also need their explicit setting, or construction raises
`ValueError`.

## Requests and results

```python
ExecutionRequest(argv, stdin=None, environment={}, cwd="/", timeout=None)
ExecutionResult(
    sandbox_id,
    execution_id,
    exit_code,
    stdout,
    stderr,
    stdout_truncated,
    stderr_truncated,
    duration,
    termination,
)
```

`argv` is a tuple of strings and is never passed to a host shell. The
workload sees only the environment you pass. `timeout` overrides
`policy.execution_timeout`; expiry raises `SandboxTimeoutError` after the
boundary is terminated.

## Streaming and cancellation

```python
handle = await instance.start(ExecutionRequest(argv=("python", "worker.py")))
async for chunk in handle.output():  # OutputChunk(stream, data)
    print(chunk.stream, chunk.data)
result = await handle.wait()
await handle.cancel(force=False)  # stop this execution and verify cleanup
```

`output()` allows exactly one consumer and applies backpressure: consume it
while you wait, or the workload blocks once `output_queue_chunks` fills.
Cancelling the task that awaits `wait()` stops the execution and still
verifies cleanup.

## Termination and cleanup

Termination stops the boundary, observes it twice and releases it, within
`TerminationPolicy` bounds. `ExecutionResult.termination` is a
`TerminationReport` with `state`, `attempts`, `observations`,
`resources_released` and `notes`.

When cleanup cannot be verified by `cleanup_deadline`, the call raises
`TerminationVerificationError` with an unknown state. The runtime keeps
owning the unfinished cleanup, `instance.pending_late_cleanups` counts it,
and a `LATE_CLEANUP_FINISHED` or `LATE_CLEANUP_FAILED` event reports the
outcome. Never treat an unknown state as success.

## Snapshots and checkpoints

All three need `Sandbox(artifact_store=...)`. Isolattice writes the manifest,
verifies size and SHA-256 after every fetch, and extracts archives safely.

| Operation | Call | Restore with | Backends |
| --- | --- | --- | --- |
| Workspace snapshot (cold, no active execution) | `await instance.snapshot(key)` with `workspace=True` | `sandbox.create(policy=..., workspace_snapshot=key)` | Podman, Linux namespaces, Windows AppContainer |
| Container root snapshot (paused, running execution) | `await handle.snapshot_container(key)` | `sandbox.create(container_snapshot=key)` | Podman |
| CRIU execution checkpoint | `await handle.checkpoint(key)` with `Capability.CHECKPOINTS` required | `await instance.restore(key, timeout=...)` | rootful Podman |

```python
from isolattice import LocalArtifactStore, Sandbox, SandboxPolicy

store = LocalArtifactStore("artifacts")  # the directory must exist
policy = SandboxPolicy(workspace=True)
async with Sandbox(backend=backend, artifact_store=store) as sandbox:
    async with await sandbox.create(policy=policy) as instance:
        await instance.run(request)
        manifest = await instance.snapshot("runs/42/workspace.tar")
    async with await sandbox.create(
        policy=policy, workspace_snapshot="runs/42/workspace.tar"
    ) as restored:
        ...
```

Keys are `/`-separated relative paths checked by `validate_artifact_key`; a
store never overwrites an existing key. Checkpoints contain workload memory;
protect them like the workload. Use `S3ArtifactStore` (extra `isolattice[s3]`)
for object storage.

## Events

`Sandbox(event_sink=...)` receives `SandboxEvent(kind, sandbox_id, backend,
execution_id, detail, timestamp)`. The default `LoggingEventSink` writes to
the `isolattice` logger, at WARNING for `SANDBOX_CLEANUP_FAILED`,
`TERMINATION_UNKNOWN` and late-cleanup events. Events never contain argv,
environment, stdin or output.

## Errors

| Exception | Raised when |
| --- | --- |
| `SandboxCapabilityError` | The backend cannot enforce the policy, or a feature is unsupported. |
| `SandboxBackendError` | The engine or host failed or returned unexpected state. |
| `SandboxStateError` | An operation does not fit the current lifecycle state. |
| `SandboxExecutionError` | The execution could not be started or completed. |
| `SandboxTimeoutError` | The execution exceeded its timeout. |
| `SandboxTerminationError` | Termination failed. |
| `TerminationVerificationError` | Cleanup could not be verified in time; state is unknown. |
| `SandboxArtifactError` | An artifact is missing, oversized, malformed or failed verification. |

All derive from `SandboxError`.
