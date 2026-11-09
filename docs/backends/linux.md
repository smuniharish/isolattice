# Linux backend

`LinuxNamespaceBackend` runs each execution in unprivileged Linux user, PID, mount,
network, IPC, UTS and cgroup namespaces with a read-only pivoted root, no network,
Landlock and seccomp, inside an owned leaf of a delegated cgroup v2 subtree. It needs
no root privileges and no daemon. It supports per-execution and instance budgets, a
size-capped `/tmp`, persistent workspaces and workspace snapshots.

!!! warning "Shared host kernel"
    Namespaces share the host kernel; kernel exploits are out of scope, and the
    host is trusted. Status: implemented; verified live in WSL2 Ubuntu 24.04 with a
    delegated cgroup.

## Requirements

- Linux on x86_64 or aarch64 with unprivileged user namespaces enabled.
- Landlock (any ABI; ABI 4 or later also denies TCP bind and connect).
- `libseccomp.so.2`.
- A unified cgroup v2 hierarchy and a **delegated, process-free cgroup directory**
  owned by the calling user. Budgets need the `memory`, `cpu` and `pids` controllers
  enabled in it.

The bundled
[wrapper](https://github.com/smuniharish/isolattice/blob/main/scripts/linux/isolattice-delegated-run.sh)
starts a systemd user scope with `Delegate=yes`, moves itself into a `supervisor` child
and exports `ISOLATTICE_LINUX_CGROUP_ROOT` pointing at a `sandboxes` child. systemd
removes the scope and every sandbox cgroup when the command exits.

```sh
scripts/linux/isolattice-delegated-run.sh python3 app.py
```

## Configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `cgroup_root` | required | Absolute delegated cgroup directory below `/sys/fs/cgroup` |
| `read_only_paths` | `/usr`, `/bin`, `/sbin`, `/lib`, `/lib32`, `/lib64`, `/libx32`, `/etc` | Absolute host paths exposed read-only; missing paths are skipped |
| `state_directory` | `None` | Absolute parent for instance state; `None` uses the system temporary directory |
| `startup_timeout` | `15.0` | Seconds allowed for the helper to build the boundary |
| `helper_interpreter` | `None` | Python used for the helper; `None` uses `sys.executable` |

## Example

```python
import asyncio
import os

from isolattice import ExecutionRequest, LinuxNamespaceBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = LinuxNamespaceBackend(cgroup_root=os.environ["ISOLATTICE_LINUX_CGROUP_ROOT"])
    policy = SandboxPolicy(
        memory_bytes=64 * 1024**2,
        process_limit=16,
        scratch_bytes=8 * 1024**2,
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "id -u")))
            print(result.stdout.decode(), end="")  # 65534


asyncio.run(main())
```

## Enforced configuration

A helper builds the boundary before the workload starts:

- a pivoted read-only root with only `read_only_paths`, a private `/proc`, minimal
  `/dev`, a private `/tmp` tmpfs and the optional `/workspace`;
- UID/GID 65534 in a nested user namespace, every capability dropped, `no_new_privs`,
  a seccomp deny list (namespace creation, mount, BPF, perf, io_uring, keyring, kernel
  modules, clocks and similar) and Landlock writes limited to `/tmp` and `/workspace`;
- an empty network namespace with only loopback;
- no inherited host environment, only explicit values and a default `PATH`;
- a parent-death signal so the tree dies if the launcher dies.

Each execution has its own cgroup leaf below a per-instance parent. `memory_bytes`,
`cpu_cores` and `process_limit` set `memory.max` (swap disabled), `cpu.max` and
`pids.max`; `instance_budget` applies to the instance parent. `scratch_bytes` caps
`/tmp`, charged to the memory cgroup, and `scratch_inodes` caps its inode count
through the tmpfs `nr_inodes` option. Termination writes `cgroup.kill` and waits for
`populated 0` before removing the leaf.

`SandboxInstance.snapshot(key)` and `Sandbox.create(workspace_snapshot=key)` save and
restore the workspace; see [snapshots](../guides/snapshots.md).

`prepare` rejects `network` other than `NONE`, `read_only_root=False`, a required
budget without a value, `DISK_LIMIT` with a workspace and any controller that is not
delegated. Discovery and preparation run the whole hardening chain once as a probe.

## Capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `FILESYSTEM_ISOLATION` | SUPPORTED | Read-only pivoted root; Landlock limits writes to `/tmp` and `/workspace` |
| `NETWORK_ISOLATION` | SUPPORTED | Empty network namespace with only loopback |
| `PROCESS_ISOLATION` | SUPPORTED | Private namespaces, UID 65534, no capabilities, seccomp |
| `VERIFIED_TERMINATION` | SUPPORTED | `cgroup.kill` and `populated 0` observed before release |
| `ENVIRONMENT_ISOLATION` | SUPPORTED | Only explicit values and a default `PATH` |
| `RESOURCE_LIMITS`, `INSTANCE_LIMITS` | SUPPORTED with delegated controllers | Per-execution leaves under a per-instance parent cgroup |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | SUPPORTED per controller | UNSUPPORTED when the matching controller is not delegated |
| `SNAPSHOTS`, `WORKSPACE` | SUPPORTED | Cold tar snapshots of a private host directory at `/workspace` |
| `DISK_LIMIT` | SUPPORTED | Size-capped `/tmp` only; workspaces have no quota |
| `CHECKPOINTS` | UNSUPPORTED | CRIU needs privileges this backend does not have |

SUPPORTED here is evidence from the probe on this host, not a qualification for hostile
code.

## Limitations

- The host kernel is shared; seccomp reduces but does not remove its attack surface.
- The caller's supplementary groups are retained because `setgroups` is denied in
  unprivileged user namespaces.
- Configured read-only host paths are visible to workloads.
- Only `network=NetworkAccess.NONE`; workspaces have no disk quota; no checkpoints.
- If the host Python process dies, the parent-death signal kills the tree; empty leaves
  remain until the systemd scope exits.

## Live testing

[test_linux_backend.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_linux_backend.py)
runs on Linux when `ISOLATTICE_TEST_LINUX_CGROUP_ROOT` names a delegated cgroup (see
[conftest.py](https://github.com/smuniharish/isolattice/blob/main/tests/conftest.py)):

```sh
scripts/linux/isolattice-delegated-run.sh sh -c \
  'ISOLATTICE_TEST_LINUX_CGROUP_ROOT=$ISOLATTICE_LINUX_CGROUP_ROOT python3 -m pytest tests/integration/test_linux_backend.py'
```

It passed in WSL2 (Ubuntu 24.04), covering identity, seccomp and Landlock denials,
filesystem and network isolation, budgets, workspace snapshots, descendant cleanup and
caller crash. See also the
[example](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/linux_namespaces.py).

## Related

- [Native sandboxing compared with Isolattice](../comparisons/native-sandboxing-vs-isolattice.md)
- [Process containment](../architecture/10-process-containment.md)
- [Resource limits](../guides/resource-limits.md)
- [Development testing](../development/testing.md)
- [cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
