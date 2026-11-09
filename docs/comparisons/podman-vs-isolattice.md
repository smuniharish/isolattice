# Podman versus Isolattice

Podman runs OCI containers, rootless by default. Isolattice's
[`PodmanBackend`](../backends/podman.md) drives one named Podman connection: it creates
a restricted container per execution, clears the image environment, verifies cgroup
limits inside the container before exec, and verifies removal. It adds workspaces,
workspace and container snapshots and, on rootful engines, CRIU checkpoints, all behind
the same API as the other backends.

## When to use which

- **Use Podman directly** for services, pods, builds, volumes, ports, networks and
  anything long-running. Isolattice does not manage those.
- **Use Isolattice with `PodmanBackend`** for policy-checked command execution from
  Python, with fail-closed required capabilities, bounded output, verified cleanup and
  snapshot artifacts in a local or S3 store.

## Comparison

| | Podman directly | Isolattice `PodmanBackend` |
| --- | --- | --- |
| Configuration | Anything Podman supports | Fixed restricted profile, policy-driven |
| Network | Any mode | `NetworkAccess.NONE` only (`NETWORK_ISOLATION` is `LIMITED`) |
| Environment | Image environment plus `--env` | Only `ExecutionRequest.environment`; `PATH` is unset |
| Limits | Flags you choose | Verified inside the cgroup; rootless needs delegated controllers |
| Persistence | Volumes, images | Optional owned workspace volume per instance |
| Snapshots and checkpoints | `podman commit`, `export`, `checkpoint` | Verified artifacts; CRIU only on rootful engines |
| Status | Mature product | Implemented; shares the host kernel, so kernel or engine exploits are out of scope |

## Example

```bash
podman run --rm --network none --read-only --memory 64m --pids-limit 32 \
  docker.io/library/alpine:3.22 /bin/sh -c 'echo hi'
```

```python
from isolattice import Capability, PodmanBackend, SandboxPolicy

backend = PodmanBackend(connection="podman-machine-default", image="docker.io/library/alpine:3.22")
policy = SandboxPolicy(
    memory_bytes=64 * 1024 * 1024,
    process_limit=32,
    required_capabilities=(Capability.MEMORY_LIMIT, Capability.PROCESS_LIMIT),
)
```

Always use an absolute `argv[0]`, such as `/bin/sh`. On a rootless connection without
delegated `memory` and `pids` controllers, this policy fails closed with
`SandboxCapabilityError`. See the [Podman Desktop guide](../guides/podman-desktop.md).
