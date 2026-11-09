# Filesystem isolation

This guide shows how to control what a workload can read and write: the root
filesystem, scratch space under `/tmp`, and a persistent per-instance workspace.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope. The Windows Job Object backend
    leaves the caller's filesystem access in place. Read the
    [threat model](../security/threat-model.md) before relying
    on any filesystem restriction.

## The policy fields

Three `SandboxPolicy` fields shape the filesystem view:

| Field | Default | Effect |
| --- | --- | --- |
| `read_only_root` | `True` | Mount the image or host root read-only. |
| `scratch_bytes` | `None` | Size cap for the per-execution `/tmp`; required for `Capability.DISK_LIMIT`. |
| `workspace` | `False` | Give the instance one writable directory shared by its executions. |

Each backend accepts only the combinations it can enforce. Anything else raises
`SandboxCapabilityError` from `Sandbox.create()` before a workload starts.

| Backend | `read_only_root` | `scratch_bytes` | `workspace` | Workspace path |
| --- | --- | --- | --- | --- |
| Linux namespaces | must be `True` | yes | yes | `/workspace` |
| Windows Job Object | must be `False` | rejected | rejected | - |
| Windows AppContainer | must be `True` | rejected | yes | `/workspace` (a host directory) |
| Podman | `True` or `False` | yes | yes | `/workspace` (a named volume) |
| Docker API | must be `True` | yes | rejected | - |
| Kubernetes | must be `True` | yes (memory-backed `emptyDir`) | rejected | - |

`Capability.FILESYSTEM_ISOLATION` is SUPPORTED by the Linux namespace backend and
LIMITED by AppContainer, Podman, Docker and Kubernetes. The Windows Job Object
backend reports it UNSUPPORTED (LIMITED with `restricted_token=True`): workloads there
see the host filesystem with the caller's rights. See
[capabilities](../reference/capabilities.md) for the full matrix.

## Read-only root with bounded scratch space

The default policy already mounts the root read-only. Add `scratch_bytes` to cap
`/tmp`, and require `DISK_LIMIT` so a backend that cannot cap it fails closed:

```python
import asyncio

from isolattice import (
    Capability,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxPolicy,
)


async def main() -> None:
    backend = PodmanBackend(connection="podman-machine-default", image="localhost/tools:latest")
    policy = SandboxPolicy(
        scratch_bytes=16 * 1024 * 1024,
        required_capabilities=(Capability.DISK_LIMIT,),
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(argv=("/bin/sh", "-c", "touch /etc/x || echo read-only"))
            )
            print(result.stdout.decode())


asyncio.run(main())
```

`scratch_bytes` must be a positive integer. Podman, Docker and Kubernetes mount
`/tmp` as a size-capped tmpfs whose pages count against the execution's memory
limit. Kubernetes additionally requires a multiple of 4096 bytes.

`scratch_inodes` caps the number of files and directories in that tmpfs (the
tmpfs `nr_inodes` option, which also counts `/tmp` itself), so a workload cannot
exhaust kernel memory with many empty files. It requires `scratch_bytes`. The Linux
namespace backend and Docker Engine enforce it; Docker verifies the option on the
inspected container before the workload starts. Podman (including its
Docker-compatible API) and Kubernetes cannot apply it and reject the policy.

## A persistent workspace

`workspace=True` creates one writable directory owned by the instance. Every
execution of the instance sees it at `SandboxInstance.workspace_path`; it is removed
when the instance closes.

```python
from isolattice import ExecutionRequest, SandboxPolicy

policy = SandboxPolicy(workspace=True)


async def build(sandbox) -> None:
    async with await sandbox.create(policy=policy) as instance:
        path = instance.workspace_path
        await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", f"echo data > {path}/x")))
        result = await instance.run(ExecutionRequest(argv=("/bin/cat", f"{path}/x"), cwd=path))
        print(result.stdout)
```

Workspaces have no disk quota, which is why Podman reports broad
`RESOURCE_LIMITS` as LIMITED. Podman refuses to combine a workspace with a required
`DISK_LIMIT`. To keep a workspace beyond the instance, publish a
[workspace snapshot](snapshots.md).

## A writable container root (Podman only)

On Podman, `read_only_root=False` gives each execution a writable container layer.
That layer has no disk quota, so Podman rejects it together with a required
`DISK_LIMIT` or `CHECKPOINTS`. It enables [container snapshots](snapshots.md).

## Host paths visible to native backends

The native backends build a filesystem view from host paths you choose:

- `LinuxNamespaceBackend(read_only_paths=...)` lists the host directories bound
  read-only into the new mount namespace. The default is
  `/usr`, `/bin`, `/sbin`, `/lib`, `/lib32`, `/lib64`, `/libx32` and `/etc`.
- `WindowsAppContainerBackend(read_only_paths=...)` grants the AppContainer
  read access to the listed host paths; the default grants none. Executables must
  be readable by the AppContainer. A grant rewrites the access control list of every
  file in the tree, which is slow for large trees.
- `WindowsJobBackend(working_directory=...)` runs workloads under that directory;
  `ExecutionRequest.cwd` maps below it. Nothing else is restricted unless
  `restricted_token=True`; see [restricted tokens](../backends/windows.md#restricted-low-integrity-tokens).

Container backends never accept host bind mounts or devices. The image is the
only source of files besides `/tmp` and the workspace.

## Related pages

- [Network isolation](network-isolation.md) and [resource limits](resource-limits.md)
- [Policy reference](../reference/policies.md)
- [Linux](../backends/linux.md), [Windows](../backends/windows.md) and
  [Podman](../backends/podman.md) backend pages
