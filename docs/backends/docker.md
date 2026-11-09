# Docker backend

`DockerBackend` runs each execution in its own hardened container through the Docker
CLI and one explicit Docker Engine API endpoint. It applies per-execution memory, CPU
and process limits and a size-capped `/tmp`, and verifies them from inside the
container before the workload starts. It has no workspaces, snapshots, checkpoints or
instance budgets.

!!! warning "Shared host kernel"
    Containers share the host kernel; kernel or engine exploits are out of scope,
    and the engine, image and host are trusted. Status: implemented; verified live
    against Podman's Docker-compatible API (reported as "Docker API via Podman")
    and Docker Engine 29.9.3 running in `docker:dind`, but not on a native Docker
    host.

## Requirements

- The `docker` CLI on `PATH`, or passed as `executable=`.
- An endpoint whose engine reports seccomp. It must be rootless unless you pass
  `allow_rootful=True`.
- An image already on the engine that provides a POSIX `/bin/sh` for the verifying
  prelude. Isolattice never pulls images.
- cgroup v2 on the engine for memory, CPU and process limits.

## Configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `host` | required | Endpoint passed as `--host`, for example `unix:///run/user/1000/docker.sock` or `npipe:////./pipe/docker_engine` |
| `image` | required | Local image name or ID |
| `executable` | `None` | Path to `docker`; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds allowed for each control command |
| `allow_rootful` | `False` | Accept a rootful engine; otherwise rootful engines are refused |
| `runner` | `None` | [`CommandRunner`](../guides/custom-integrations.md#custom-command-runner) that starts CLI processes |

`DOCKER_HOST` and CLI contexts are never consulted, and a missing endpoint never falls
back to another engine. Discovery records the server identity: a Podman service is
reported as Podman, not as Docker Engine.

## Example

```python
import asyncio

from isolattice import DockerBackend, ExecutionRequest, Sandbox, SandboxPolicy


async def main() -> None:
    backend = DockerBackend(
        host="npipe:////./pipe/docker_engine",
        image="docker.io/library/alpine:3.22",
    )
    policy = SandboxPolicy(memory_bytes=64 * 1024**2)
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "id -u")))
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

## Enforced configuration

Each container runs with a read-only root, `--network none`, UID 65534, every
capability dropped, `no-new-privileges`, the engine's default seccomp filter, a
size-capped `/tmp` tmpfs and the requested cgroup limits. After creation the container
is inspected and must match the plan. A shell prelude then reads capabilities,
`NoNewPrivs`, seccomp mode, UID, cgroup limits and the `/tmp` size from inside the
container before executing argv. Any mismatch fails closed and removes the container.

`prepare` rejects `network` other than `NONE`, `read_only_root=False`,
`workspace=True` and `instance_budget`.

## Capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | SUPPORTED with cgroup v2 | Read from the workload cgroup before exec |
| `RESOURCE_LIMITS` | LIMITED with cgroup v2 | Per-execution limits only |
| `FILESYSTEM_ISOLATION` | LIMITED | Engines may add writable tmpfs mounts such as `/dev/shm` |
| `NETWORK_ISOLATION` | LIMITED | Network mode `none`; only loopback exists |
| `PROCESS_ISOLATION` | LIMITED | Private PID namespace; the engine and kernel are trusted |
| `VERIFIED_TERMINATION` | LIMITED | Engine-reported state and owned container removal |
| `ENVIRONMENT_ISOLATION` | LIMITED | The Docker API cannot unset the image environment |
| `DISK_LIMIT` | LIMITED | `/tmp` is capped; other engine tmpfs mounts are bounded only by memory |
| `WORKSPACE`, `SNAPSHOTS`, `CHECKPOINTS` | UNSUPPORTED | Not implemented for Docker |
| `INSTANCE_LIMITS` | UNSUPPORTED | No aggregate cgroup shared by several containers |

## Limitations

- Each execution is a separate container; nothing persists between executions.
- The image environment (including `PATH`) is retained. Still use absolute `argv[0]`.
- The engine, image, host kernel and endpoint are trusted. Never pass the API socket
  into workloads.
- Environment values and command arguments are visible to the engine owner.
- Only containers carrying both `io.isolattice.sandbox` and `io.isolattice.execution`
  labels are stopped and removed.

## Live testing

[test_docker_backend.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_docker_backend.py)
uses the `docker` marker and skips unless `ISOLATTICE_TEST_DOCKER_HOST` and
`ISOLATTICE_TEST_DOCKER_IMAGE` are set. Optional: `ISOLATTICE_TEST_DOCKER_EXECUTABLE`
and `ISOLATTICE_TEST_DOCKER_ROOTFUL` (`0` or `1`). See
[conftest.py](https://github.com/smuniharish/isolattice/blob/main/tests/conftest.py).

```powershell
$env:ISOLATTICE_TEST_DOCKER_HOST = "npipe:////./pipe/docker_engine"
$env:ISOLATTICE_TEST_DOCKER_IMAGE = "docker.io/library/alpine:3.22"
uv run pytest -m docker
```

The suite and the
[example](https://github.com/smuniharish/isolattice/blob/main/examples/09-container-integrations/docker_isolated_run.py)
passed against Podman Desktop's Docker-compatible API.

## Related

- [Podman backend](podman.md)
- [Docker compared with Isolattice](../comparisons/docker-vs-isolattice.md)
- [Container backend architecture](../architecture/16-container-backend-architecture.md)
- [Resource limits](../guides/resource-limits.md)
