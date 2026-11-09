# Installation

Isolattice needs Python 3.12 or later. The core has no dependencies.

=== "pip"

    ```bash
    pip install isolattice
    pip install "isolattice[s3]"   # adds S3ArtifactStore
    ```

=== "uv"

    ```bash
    uv add isolattice
    uv add "isolattice[s3]"        # adds S3ArtifactStore
    ```

| Extra | Adds | Dependency |
| --- | --- | --- |
| `s3` | [`S3ArtifactStore`](../api/storage.md) for snapshots and checkpoints | aiobotocore |
| `all` | Every extra | All of the above |

## Backend prerequisites

Importing Isolattice never needs a container engine. Each backend needs its own
tooling only when you use it:

| Backend | Needs |
| --- | --- |
| [Podman](../backends/podman.md) | The `podman` client and a named connection. The image must already be present; Isolattice never pulls. |
| [Docker API](../backends/docker.md) | The `docker` client and an explicit API endpoint such as `unix:///run/user/1000/podman/podman.sock` or `npipe:////./pipe/docker_engine`. |
| [Kubernetes](../backends/kubernetes.md) | `kubectl`, an explicit kubeconfig, context and namespace, and an image available to the nodes. |
| [Linux namespaces](../backends/linux.md) | Linux with unprivileged user namespaces and a delegated cgroup v2 subtree. |
| [Windows](../backends/windows.md) | Windows 10 or 11. Nothing else. |

## Check the installation

`discover()` asks a backend what it can enforce on this machine, without
running a workload:

```python
import asyncio

from isolattice import PodmanBackend, Sandbox


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
    print(report.name, "available:", report.available)
    for capability, evidence in report.capabilities.items():
        print(f"  {capability.value}: {evidence.state.value}")


asyncio.run(main())
```

An unavailable backend reports `available: False` with the reason in
`report.error`; it does not raise.

## Network trust

If your network intercepts TLS, add its certificate to the system trust store,
or use `uv --system-certs`. Never disable certificate verification.
