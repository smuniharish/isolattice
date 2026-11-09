# Snapshots

Snapshots save files so a later instance can start from them. Isolattice has two
kinds, both published to an [artifact store](#choose-an-artifact-store) with a SHA-256
manifest:

| Kind | Captures | Create | Restore into | Backends |
| --- | --- | --- | --- | --- |
| Workspace snapshot | The instance workspace | `SandboxInstance.snapshot(key)` | `Sandbox.create(workspace_snapshot=key)` | Linux namespaces, Windows AppContainer, Podman |
| Container snapshot | The root filesystem of a running execution | `ExecutionHandle.snapshot_container(key)` | `Sandbox.create(container_snapshot=key)` | Podman |

Running processes are not saved; that is what [checkpoints](checkpoints.md) do. The
Windows Job Object, Docker API and Kubernetes backends support neither kind.

!!! warning "Protect snapshot data"
    Snapshotting backends share the host kernel, so kernel and engine exploits are
    out of scope. A snapshot holds whatever the workload wrote, so protect it like
    the workload's data.

## Choose an artifact store

Snapshots need `Sandbox(artifact_store=...)`; without one, `snapshot()` and
`create(workspace_snapshot=...)` raise `SandboxCapabilityError`.

```python
from isolattice import LocalArtifactStore, PodmanBackend, Sandbox

store = LocalArtifactStore("/srv/isolattice/artifacts")
backend = PodmanBackend(connection="podman-machine-default", image="localhost/tools:latest")
sandbox = Sandbox(backend=backend, artifact_store=store)
```

| Store | Use |
| --- | --- |
| `LocalArtifactStore(root)` | A directory you own; links the archive when it is on the same filesystem as the staging directory |
| `S3ArtifactStore(bucket=...)` | Any S3-compatible bucket; install `isolattice[s3]` |
| Your own `ArtifactStore` | See [custom integrations](custom-integrations.md#custom-artifact-store) |

Archives are staged in `Sandbox(staging_directory=...)`, which defaults to the system
temporary directory. Put it on the same filesystem as a `LocalArtifactStore` root to
avoid a copy.

Artifact keys are up to 512 characters of `/`-separated segments using
`A-Z a-z 0-9 . _ -`. Validate one up front with `validate_artifact_key(key)`. A store
never overwrites a key, so publishing an existing key raises `SandboxArtifactError`.
Keys appear in events; do not put secrets in them.

## Workspace snapshots

A workspace snapshot is **cold**: no execution may be active, and new executions wait
until it finishes.

```python
from isolattice import ExecutionRequest, Sandbox, SandboxPolicy

policy = SandboxPolicy(workspace=True)


async def save_and_restore(sandbox: Sandbox) -> bytes:
    async with await sandbox.create(policy=policy) as instance:
        path = instance.workspace_path
        await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", f"echo ready > {path}/state")))
        manifest = await instance.snapshot("projects/demo/workspace-1")
        print(manifest.sha256, manifest.size)

    async with await sandbox.create(
        policy=policy, workspace_snapshot="projects/demo/workspace-1"
    ) as restored:
        result = await restored.run(ExecutionRequest(argv=("/bin/cat", f"{path}/state")))
        return result.stdout
```

Rules:

- The policy must set `workspace=True`, both when snapshotting and when restoring.
- The snapshot is an uncompressed tar. Before import, Isolattice re-verifies size and
  SHA-256 and rejects unsafe members (absolute paths, `..`, links escaping the root,
  devices) and archives over 1,000,000 members or 16 GiB.
- Restoring a workspace snapshot from another backend fails: the manifest records the
  backend name.

## Container snapshots (Podman)

A container snapshot pauses a **running** execution, exports its root filesystem with
`podman export`, and always resumes it. A new instance created from it runs every
execution from an owned image built from the archive; the image is removed when the
instance closes.

```python
import asyncio

from isolattice import ExecutionRequest, Sandbox, SandboxPolicy

policy = SandboxPolicy(read_only_root=False)


async def capture_root(sandbox: Sandbox) -> None:
    async with await sandbox.create(policy=policy) as instance:
        handle = await instance.start(
            ExecutionRequest(argv=("/bin/sh", "-c", "echo tuned > /etc/tool.conf; sleep 60"))
        )
        await asyncio.sleep(1)
        await handle.snapshot_container("images/tuned-root")
        await handle.cancel()

    async with await sandbox.create(
        policy=policy, container_snapshot="images/tuned-root"
    ) as derived:
        result = await derived.run(ExecutionRequest(argv=("/bin/cat", "/etc/tool.conf")))
        print(result.stdout)
```

Rules:

- The execution must be running; otherwise `snapshot_container()` raises
  `SandboxStateError`.
- Only the container layer is captured. The `/workspace` volume and the `/tmp` tmpfs
  are not included.
- `read_only_root=False` is what makes root changes possible, so it is the useful
  setting here. That writable layer has no disk quota.
- You can combine both kinds:
  `create(container_snapshot=..., workspace_snapshot=...)` imports the root first,
  then the workspace.

## Manage artifacts

The store API manages artifacts outside a sandbox:

```python
from isolattice import LocalArtifactStore


async def prune(store: LocalArtifactStore, key: str) -> None:
    if await store.exists(key):
        await store.delete(key)
```

Each publish and restore emits an `artifact.published` or `artifact.restored`
[event](../reference/events.md) with the key, kind and size.

## Examples and design

- [Workspace snapshot example](https://github.com/smuniharish/isolattice/blob/main/examples/12-snapshots/podman_workspace_snapshot.py)
- [Container snapshot example](https://github.com/smuniharish/isolattice/blob/main/examples/12-snapshots/podman_container_snapshot.py)
- [Snapshot architecture](../architecture/13-snapshot-architecture.md)
- [Storage API](../api/storage.md)
