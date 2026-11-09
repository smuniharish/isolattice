# Podman Desktop

This guide sets up a Podman machine on Windows or macOS (usually managed with Podman
Desktop) and connects `PodmanBackend` to it. Podman Desktop is optional; Isolattice only
talks to the `podman` CLI and one named connection.

!!! warning "Shared host kernel"
    Podman containers share the host kernel (here, the machine's VM kernel);
    kernel or engine exploits are out of scope. See the
    [Podman backend](../backends/podman.md) page for the enforced
    configuration and its limits.

## 1. Create and start a machine

Create a machine in Podman Desktop (**Settings > Resources > Podman**) or with the CLI:

```powershell
podman machine init
podman machine start
podman system connection list
```

Note the connection names. A default machine usually provides
`podman-machine-default` (rootless) and `podman-machine-default-root` (rootful).

## 2. Load an image

Isolattice never pulls images. Pull or build one on the engine first:

```powershell
podman --connection podman-machine-default pull docker.io/library/alpine:3.22
```

## 3. Run a command

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default",
        image="docker.io/library/alpine:3.22",
    )
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        if not report.available:
            raise RuntimeError(report.error)
        policy = SandboxPolicy()
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/echo", "hello")))
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

`PodmanBackend` requires a rootless engine with seccomp enabled. Pass
`allow_rootful=True` only when you deliberately use the rootful connection.

## 4. Enable rootless resource limits

On WSL-based machines systemd cannot delegate cgroup controllers to the rootless
user, so `memory_bytes`, `cpu_cores` and `process_limit` are rejected. The
[installer script](https://github.com/smuniharish/isolattice/blob/main/scripts/podman-machine/Install-IsolatticeRootless.ps1)
adds a rootless API service inside a delegated cgroup subtree with the `cpu`, `memory`
and `pids` controllers, and registers a new connection:

```powershell
.\scripts\podman-machine\Install-IsolatticeRootless.ps1 -Machine podman-machine-default
```

Use the new `podman-machine-default-isolattice` connection for per-execution budgets.
Instance budgets and checkpoints still need the rootful connection.

## Which connection for which feature

| Feature | Rootless | Rootless with delegated controllers | Rootful (`allow_rootful=True`) |
| --- | --- | --- | --- |
| Basic execution, `/tmp` scratch, workspace, snapshots | yes | yes | yes |
| Per-execution memory, CPU, process limits | no | yes | yes |
| Instance budgets | no | no | yes |
| CRIU checkpoints | no | no | yes, with CRIU on the machine |

## Paths and networking

The engine runs in a Linux VM, so engine-side paths and networking are those of the
VM, not your host. Isolattice transfers snapshot and checkpoint archives through the
CLI, so staging and artifact store paths stay on the client host.

The Podman API socket grants code-execution authority over the machine. Never pass it
into workloads.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `report.available` is `False` | Machine stopped, wrong connection name, or `podman` not on `PATH` (pass `executable=`) |
| `SandboxCapabilityError` mentioning rootful | Connection is rootful without `allow_rootful=True` |
| `SandboxCapabilityError` for a limit | Controller not delegated; use the installer connection |
| Image not found | Image not loaded on that connection; Isolattice never pulls |

See also [troubleshooting](../operations/troubleshooting.md) and the
[getting-started example](https://github.com/smuniharish/isolattice/blob/main/examples/01-getting-started/podman_hello.py).
