# Checkpoints

A checkpoint saves a **running** execution, including its memory and process state,
so it can resume later in a new instance. Isolattice implements checkpoints only with
CRIU on a **rootful** Podman engine.

!!! warning "Privileged engine required"
    Podman containers share the host kernel; kernel or engine exploits are out of
    scope. Checkpoints need a rootful engine (`allow_rootful=True`), which grants far
    more authority than rootless Podman; see
    [rootful and rootless engines](../backends/podman.md#rootful-and-rootless-engines).
    A checkpoint archive contains workload memory, including any secrets the workload
    held. Store it as carefully as the workload itself and restore only archives you
    produced.

## Support

| Backend | Checkpoints |
| --- | --- |
| Podman, rootful engine | LIMITED: CRIU through `podman container checkpoint/restore` |
| Podman, rootless engine | rejected |
| Linux namespaces, Windows Job Object, Windows AppContainer, Docker API, Kubernetes | rejected |

For files only, use [snapshots](snapshots.md) instead; they work on more backends.

## Prerequisites

- A rootful Podman connection with CRIU installed and working on the engine host.
- `PodmanBackend(..., allow_rootful=True)`.
- An artifact store: `Sandbox(artifact_store=...)`.
- A policy that requires `Capability.CHECKPOINTS`. At `Sandbox.create()` the backend
  then checkpoints a throwaway hardened probe container to prove CRIU works, and fails
  closed if it does not.
- The policy must not set `workspace=True`, an `instance_budget` or
  `read_only_root=False`; Podman rejects those together with checkpoints.

## Checkpoint and restore

```python
from isolattice import (
    Capability,
    ExecutionRequest,
    LocalArtifactStore,
    PodmanBackend,
    Sandbox,
    SandboxPolicy,
)

COUNTER = ExecutionRequest(
    argv=("/bin/sh", "-c", "i=0; while [ $i -le 99 ]; do echo $i; i=$((i+1)); sleep 0.1; done")
)
policy = SandboxPolicy(
    execution_timeout=120,
    required_capabilities=(Capability.CHECKPOINTS,),
)


async def checkpoint_and_resume() -> bytes:
    backend = PodmanBackend(
        connection="podman-machine-default-root",
        image="localhost/tools:latest",
        allow_rootful=True,
    )
    store = LocalArtifactStore("/srv/isolattice/artifacts")
    async with Sandbox(backend=backend, artifact_store=store) as sandbox:
        async with await sandbox.create(policy=policy) as source:
            handle = await source.start(COUNTER)
            stream = handle.output()
            async for chunk in stream:
                if b"5\n" in chunk.data:
                    break
            await handle.checkpoint("checkpoints/counter-1")
            await handle.cancel()

        async with await sandbox.create(policy=policy) as target:
            resumed = await target.restore("checkpoints/counter-1", timeout=60)
            async for _ in resumed.output():
                pass
            result = await resumed.wait()
            return result.stdout
```

What happens:

1. `handle.checkpoint(key)` runs `podman container checkpoint --leave-running
   --export`. The source keeps running; cancel it when you are done with it. The call
   raises `SandboxStateError` unless the execution is running.
2. The archive and its SHA-256 manifest are published under `key`, with an
   `artifact.published` event.
3. `instance.restore(key, timeout=...)` fetches the archive, re-verifies size and
   digest, and checks that the checkpointed container spec still has the hardened
   shape (unprivileged user, no capabilities, read-only root, private namespaces, no
   devices, seccomp). It then restores the container and returns an
   `ExecutionHandle`.
4. Output from the resumed workload starts after the checkpoint; earlier log lines are
   filtered by a helper that runs with `PodmanBackend(helper_interpreter=...)`.

The resumed execution counts against `max_concurrent_executions` and ends through the
normal [termination path](process-tree-termination.md). A checkpoint from another
backend, or an artifact of another kind, is rejected with `SandboxArtifactError`.

## Sizing and time

Checkpoints of large processes take time and space. `PodmanBackend(transfer_timeout=...)`
(default 300 seconds) bounds checkpoint export and restore import. Archives are
staged in `Sandbox(staging_directory=...)`: put it on storage that is private to the
service, because staged checkpoints hold workload memory.

## Examples and design

- [Checkpoint example](https://github.com/smuniharish/isolattice/blob/main/examples/13-checkpoints/podman_checkpoint.py)
- [Checkpoint architecture](../architecture/14-checkpoint-architecture.md)
- [Podman backend](../backends/podman.md)
