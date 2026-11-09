# Docker versus Isolattice

Docker (the CLI, the Engine API or the `docker` Python SDK) gives you full control over
containers. Isolattice's [`DockerBackend`](../backends/docker.md) talks to
a Docker Engine API endpoint and adds a policy layer: it creates one locked-down
container per execution, inspects what the engine created, verifies cgroup limits
inside the container before exec and verifies removal afterwards.

## When to use which

- **Use Docker directly** to build images, run services, mount volumes, publish ports
  or configure networks. Isolattice deliberately exposes none of that.
- **Use Isolattice with `DockerBackend`** to run short commands in a fixed image with no
  network, a read-only root, verified limits and the same API as the other backends.
- **Prefer `PodmanBackend`** if you can: it clears the image environment and supports
  workspaces, snapshots and checkpoints, which `DockerBackend` does not.

## Comparison

| | Docker directly | Isolattice `DockerBackend` |
| --- | --- | --- |
| Configuration | Anything the engine supports | Fixed restricted profile, policy-driven |
| Network | Any mode | `NetworkAccess.NONE` only |
| Image environment | Present | Present (the API cannot unset it); `ENVIRONMENT_ISOLATION` is `LIMITED` |
| Limits | Flags you choose, not re-verified | Memory, CPU, PIDs read back inside the cgroup before exec |
| Persistence | Volumes, long-running containers | None between executions |
| Cleanup | `docker rm` | Verified removal with a `TerminationReport` |
| Status | Mature product | Implemented; shares the host kernel, so kernel or engine exploits are out of scope |

## Example

```bash
docker run --rm --network none --read-only --memory 128m --pids-limit 32 \
  alpine:3.22 /bin/sh -c 'echo hi'
```

```python
from isolattice import DockerBackend, SandboxPolicy

backend = DockerBackend(host="unix:///var/run/docker.sock", image="docker.io/library/alpine:3.22")
policy = SandboxPolicy(
    memory_bytes=128 * 1024 * 1024,
    process_limit=32,
)
```

Pass `backend` to `Sandbox(backend=...)` as in the
[quickstart](../getting-started/quickstart.md). Docker containers share the host kernel;
see [platform limitations](../security/platform-limitations.md).
