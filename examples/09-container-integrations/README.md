---
category: 9
status: runnable
---

# 09 Container integrations

Runs one limited workload through an explicit Docker API endpoint with
`DockerBackend`. Podman examples are in categories
[01](../01-getting-started/README.md), [02](../02-execution/README.md),
[04](../04-resource-management/README.md), [06](../06-async-and-cancellation/README.md),
[07](../07-capabilities-and-backends/README.md), [12](../12-snapshots/README.md),
[13](../13-checkpoints/README.md) and [18](../18-failure-scenarios/README.md).

| Script | What it shows | Engine |
| --- | --- | --- |
| [`docker_isolated_run.py`](https://github.com/smuniharish/isolattice/blob/main/examples/09-container-integrations/docker_isolated_run.py) | Engine identity, rejected workspace, uid, capabilities, cgroup limits, read-only root, stdin and environment | Docker API endpoint |

`DockerBackend` drives the `docker` CLI against the endpoint given by `--host`. It is
live-tested against Podman's Docker-compatible API ("Docker API via Podman") and
Docker Engine 29.9.3 in `docker:dind`, not on a native Docker host.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- The `docker` CLI on `PATH`, or its path passed with `--executable`.
- An explicit Docker API endpoint. Rootless engines are expected; pass
  `--allow-rootful` only for an engine you deliberately chose to run rootful.
- An image already present on that engine containing `/bin/sh` and `awk`.

## Run

```shell
uv run python examples/09-container-integrations/docker_isolated_run.py --host unix:///run/user/1000/podman/podman.sock --image docker.io/library/alpine:3.22
```

Replace the `--host` value with your endpoint (here, a rootless Podman API socket on
Linux). Optional: `--executable PATH`, `--allow-rootful`.

## Expected output

```text
Backend: docker
Engine: Docker API served by <server>; rootless engine.
Qualification: Containers share the host kernel; kernel or engine exploits are out of scope.
Unsupported workspace: rejected
uid=65534
caps=0000000000000000
memory.max=67108864
pids.max=16
root=read-only
stdin=from the host
greeting=hello
Owned container released: True
```

## Acceptance

- A policy requiring `Capability.WORKSPACE` must raise `SandboxCapabilityError`.
- With `memory_bytes=64 MiB`, `process_limit=16` and both limits required, the
  workload must report uid 65534, an empty effective capability set,
  `memory.max=67108864`, `pids.max=16`, a read-only root, the stdin line and
  `GREETING=hello`, and the result must report `terminated` with resources released.
- [`tests/integration/test_docker_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_docker_backend.py)
  runs the script (`ISOLATTICE_TEST_DOCKER_HOST`, `ISOLATTICE_TEST_DOCKER_IMAGE`,
  optional `ISOLATTICE_TEST_DOCKER_EXECUTABLE` and `ISOLATTICE_TEST_DOCKER_ROOTFUL`)
  and also covers CPU and scratch limits, timeout cleanup and a missing endpoint.

## Cleanup

Only containers labelled as owned by this sandbox are created or removed; each is
verified terminated and removed before its result returns. Unrelated containers on
the engine are never touched.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope, and
  the engine and image are trusted.
- Networking must be `NetworkAccess.NONE`; workspaces and snapshots are not
  available on this backend.
- The Docker API cannot unset the image environment, so the workload sees the image's
  `ENV` values plus `ExecutionRequest.environment`. `ENVIRONMENT_ISOLATION` is
  limited.
- Anyone with access to the endpoint controls the engine. Do not expose an
  unauthenticated TCP socket.

See the [Docker backend](https://isolattice.readthedocs.io/en/latest/backends/docker/)
and [Podman Desktop](https://isolattice.readthedocs.io/en/latest/guides/podman-desktop/) pages.
