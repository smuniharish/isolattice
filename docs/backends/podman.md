# Podman backend

`PodmanBackend` runs each execution in its own hardened container through the `podman`
CLI and one explicitly named connection. It never switches connections, pulls images or
starts a daemon. It supports per-execution limits, `/tmp` scratch caps, persistent
workspaces, workspace and whole-container snapshots and, on a rootful engine, pod
instance budgets and CRIU checkpoints.

!!! warning "Shared host kernel"
    Containers share the host kernel; kernel or engine exploits are out of scope,
    and the engine, image and host are trusted. Status: implemented; verified live
    on Podman 6.1.4, rootless and rootful.

## Requirements

- The `podman` CLI on `PATH`, or passed as `executable=`.
- A named connection whose engine reports seccomp enabled. The engine must be rootless
  unless you pass `allow_rootful=True`.
- An image already present on that connection and providing `/bin/sh`. Isolattice never
  pulls images.
- cgroup v2 with delegated `memory`, `cpu` and `pids` controllers for resource limits.
- A rootful engine with CRIU installed for checkpoints.

## Configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `connection` | required | Podman connection name passed to every CLI call |
| `image` | required | Local image name or ID; resolved to an immutable ID at discovery |
| `executable` | `None` | Path to `podman`; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds allowed for each control command |
| `allow_rootful` | `False` | Accept a rootful engine; otherwise rootful engines are refused |
| `transfer_timeout` | `300.0` | Seconds allowed for snapshot and checkpoint transfers |
| `runner` | `None` | [`CommandRunner`](../guides/custom-integrations.md#custom-command-runner) that starts CLI processes |
| `helper_interpreter` | `None` | Interpreter for internal helpers; `None` uses `sys.executable` |

## Example

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default",
        image="docker.io/library/alpine:3.22",
    )
    policy = SandboxPolicy(scratch_bytes=16 * 1024**2)
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        if not report.available:
            raise RuntimeError(report.error)
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/echo", "hello")))
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

Podman removes the image environment (`--unsetenv-all`). The workload sees only
`ExecutionRequest.environment`, so `PATH` is unset and `argv[0]` must be absolute
(`/bin/sh`, not `sh`). A bare name fails with this error:

```text
Podman attach ended without an exited container; if the workload never started,
check that argv[0] is an absolute path...
```

## Rootful and rootless engines

A **rootless** engine runs as your user: container root maps to an unprivileged host
UID, so an escape lands as that user. A **rootful** engine runs as host root, which
makes an escape far more damaging. Isolattice refuses rootful engines unless you opt in.

```python
from isolattice import PodmanBackend, SandboxPolicy

image = "docker.io/library/alpine:3.22"

# Rootless connection with delegated controllers: per-execution limits.
rootless = PodmanBackend(connection="podman-machine-default-isolattice", image=image)
limited = SandboxPolicy(memory_bytes=64 * 1024**2, process_limit=32)

# Rootful connection: pod instance budgets and CRIU checkpoints.
rootful = PodmanBackend(connection="podman-machine-default-root", image=image, allow_rootful=True)
```

| Feature | Rootless | Rootless, delegated controllers | Rootful |
| --- | --- | --- | --- |
| Execution, `/tmp` scratch, workspace, snapshots | yes | yes | yes |
| `memory_bytes`, `cpu_cores`, `process_limit` | no | yes | yes, with cgroup v2 controllers |
| `instance_budget` (pod) | no | no | yes |
| CRIU checkpoints | no | no | yes, with CRIU |

### Delegated controllers

Per-execution limits need the `memory`, `cpu` and `pids` cgroup v2 controllers
delegated to the engine. Missing controllers make `create()` fail with
`SandboxCapabilityError` before any container exists; there is no unlimited fallback.
On WSL-based Podman machines systemd cannot delegate them to the rootless user. The
[installer script](https://github.com/smuniharish/isolattice/blob/main/scripts/podman-machine/Install-IsolatticeRootless.ps1)
adds a rootless API service in a delegated cgroup subtree and registers a
`<machine>-isolattice` connection. See [Podman Desktop](../guides/podman-desktop.md).

Rootless pods were observed not to apply pod cgroup limits, so instance budgets require
a rootful engine and fail closed on rootless connections.

## Enforced configuration

Before the workload starts, Isolattice inspects each container and rejects any
mismatch: resolved image ID, private PID/IPC/UTS/cgroup namespaces, `network=none`,
read-only root (unless `read_only_root=False`), non-root UID/GID, dropped baseline
capabilities, `no-new-privileges`, no restart, healthcheck or logging, no binds,
devices or unexpected mounts, and the explicit environment. For limited executions a
`/bin/sh` prelude reads `memory.max`, `memory.swap.max`, `cpu.max` and `pids.max` inside
the container before executing argv.

- Memory is at least 6 MiB with swap disabled. CPU is a hard quota over 100 ms
  periods. `process_limit` counts processes and threads.
- `scratch_bytes` mounts a size-capped `/tmp` tmpfs charged to the memory cgroup.
  Podman's tmpfs options do not accept `nr_inodes`, so `scratch_inodes` is rejected.
- `workspace=True` creates an owned named volume at `/workspace`, removed when the
  instance closes. Volumes have no quota.
- `instance_budget` creates one owned pod per instance; its memory and CPU budgets are
  shared by all executions. Aggregate process budgets are rejected.

## Snapshots

**Workspace snapshots.** `SandboxInstance.snapshot(key)` exports the workspace volume
while no execution runs; `Sandbox.create(workspace_snapshot=key)` imports it into a new
instance. Archives are checked for unsafe members before import.

**Container snapshots.** `ExecutionHandle.snapshot_container(key)` pauses a running
execution, exports its root filesystem with `podman export` and always resumes it.
`Sandbox.create(container_snapshot=key)` imports the archive as an owned image used by
every execution of the new instance and removed on close. The workspace volume and
`/tmp` are excluded. Use `read_only_root=False` so the workload can change its root;
that writable layer has no quota.

```python
import asyncio

from isolattice import (
    ExecutionRequest,
    LocalArtifactStore,
    PodmanBackend,
    Sandbox,
    SandboxPolicy,
)

policy = SandboxPolicy(read_only_root=False)


async def capture(backend: PodmanBackend) -> None:
    store = LocalArtifactStore("/var/lib/myapp/artifacts")
    async with Sandbox(backend=backend, artifact_store=store) as sandbox:
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

See [snapshots](../guides/snapshots.md).

## Checkpoints (rootful only)

Requiring `Capability.CHECKPOINTS` on a rootful engine probes CRIU with a throwaway
container during `create()`. `ExecutionHandle.checkpoint(key)` then dumps a running
execution (it keeps running) and `SandboxInstance.restore(key)` resumes it in another
instance. Checkpoints cannot be combined with workspaces, instance budgets or a writable
root. Archives contain workload memory; treat them as sensitive, trusted restore inputs.

```python
from isolattice import Capability, ExecutionRequest, Sandbox, SandboxPolicy

policy = SandboxPolicy(required_capabilities=(Capability.CHECKPOINTS,))


async def move(sandbox: Sandbox) -> None:
    # sandbox uses a rootful PodmanBackend(allow_rootful=True) and an artifact store.
    async with await sandbox.create(policy=policy) as source:
        handle = await source.start(ExecutionRequest(argv=("/bin/sleep", "60")))
        await handle.checkpoint("jobs/sleeper.ckpt")
        await handle.cancel()
    async with await sandbox.create(policy=policy) as target:
        resumed = await target.restore("jobs/sleeper.ckpt")
        await resumed.cancel()
```

See [checkpoints](../guides/checkpoints.md).

## Capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `FILESYSTEM_ISOLATION` | LIMITED | Read-only image root; the only host-backed mount is the owned workspace volume |
| `NETWORK_ISOLATION` | LIMITED | Engine network namespace with networking disabled |
| `PROCESS_ISOLATION` | LIMITED | Private PID namespace; hostile-workload qualification incomplete |
| `VERIFIED_TERMINATION` | LIMITED | Engine-reported state and owned container removal |
| `ENVIRONMENT_ISOLATION` | LIMITED | Image environment removed; explicit values are visible to the engine |
| `RESOURCE_LIMITS` | LIMITED | Needs a delegated controller; workspace volumes have no quota |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | SUPPORTED / UNSUPPORTED | SUPPORTED only when the matching controller is delegated on cgroup v2 |
| `SNAPSHOTS` | SUPPORTED | Workspace volume export/import and root-filesystem exports |
| `WORKSPACE` | SUPPORTED | Owned named volume at `/workspace` |
| `DISK_LIMIT` | SUPPORTED | Size-capped `/tmp` only; cannot be required with a workspace or writable root |
| `INSTANCE_LIMITS` | SUPPORTED on rootful | Pod `memory.max` and `cpu.max`; UNSUPPORTED rootless |
| `CHECKPOINTS` | LIMITED on rootful | CRIU, enabled only when required; UNSUPPORTED rootless |

`NETWORK_ISOLATION` is LIMITED, so requiring it fails closed. Policies must use
`network=NetworkAccess.NONE`.

## Limitations

- Each execution is a separate container; only the optional workspace persists.
- The engine, image, host kernel and connection are trusted. The API socket grants code
  execution authority; never pass it into workloads.
- Environment values and command arguments are visible to the engine owner. This is not
  a secret-delivery mechanism.
- No network allowlists, host bind mounts or secrets.
- Container snapshots exclude the workspace volume and `/tmp`.
- On Windows and macOS the engine runs in a Linux VM, so its paths and networking are
  those of the VM.
- Termination and cleanup follow the
  [termination policy](../guides/process-tree-termination.md). If `cleanup_deadline`
  expires, `TerminationVerificationError` reports state `unknown`; reconcile containers
  labelled `io.isolattice.sandbox` and `io.isolattice.execution`.

## Live testing

Podman tests use the `podman` marker and skip unless their variables are set
(see [conftest.py](https://github.com/smuniharish/isolattice/blob/main/tests/conftest.py)):

| Variables | Engine | Test files |
| --- | --- | --- |
| `ISOLATTICE_TEST_PODMAN_CONNECTION`, `ISOLATTICE_TEST_PODMAN_IMAGE` | Rootless | [test_podman_backend.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py), [test_podman_instance.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_instance.py), [test_podman_container_snapshot.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_container_snapshot.py) |
| `ISOLATTICE_TEST_PODMAN_RESOURCE_CONNECTION`, `_RESOURCE_IMAGE`, `_RESOURCE_ROOTFUL` (`0`/`1`) | Delegated controllers | [test_podman_resources.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_resources.py) |
| `ISOLATTICE_TEST_PODMAN_ROOTFUL_CONNECTION`, `_ROOTFUL_IMAGE` | Rootful | `test_podman_instance.py` (pod budgets, CRIU), `test_podman_container_snapshot.py` |

```powershell
$env:ISOLATTICE_TEST_PODMAN_CONNECTION = "podman-machine-default"
$env:ISOLATTICE_TEST_PODMAN_IMAGE = "docker.io/library/alpine:3.22"
uv run pytest -m podman
```

These runs exercised one Podman machine on Windows/WSL. They do not qualify other
versions, hosts or adversarial workloads. Examples:
[hello](https://github.com/smuniharish/isolattice/blob/main/examples/01-getting-started/podman_hello.py),
[limits](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_limits.py),
[instance budget](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_instance_budget.py),
[container snapshot](https://github.com/smuniharish/isolattice/blob/main/examples/12-snapshots/podman_container_snapshot.py),
[checkpoint](https://github.com/smuniharish/isolattice/blob/main/examples/13-checkpoints/podman_checkpoint.py).

## Related

- [Podman Desktop](../guides/podman-desktop.md)
- [Resource limits](../guides/resource-limits.md)
- [Container backend architecture](../architecture/16-container-backend-architecture.md)
- [Development testing](../development/testing.md)
