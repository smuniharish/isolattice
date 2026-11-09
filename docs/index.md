---
hide:
  - navigation
---

# Isolattice

<div class="il-hero" markdown>

**Run workloads under security policies that are enforced, or refused.**

Isolattice is an async Python framework for running commands inside sandboxes.
You state which guarantees a workload needs: no network, a read-only root,
memory and process limits, verified termination. Isolattice finds a backend that
can enforce each of them, or it refuses to start.

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[Why Isolattice](#why-isolattice){ .md-button }

</div>

!!! warning "Know the boundary"

    Containers, namespaces and AppContainers share the host or node kernel, so
    kernel, engine, runtime and cluster exploits are outside the threat model.
    The Windows Job Object backend is containment only, not a security boundary.
    No backend is certified for hostile multi-tenant code. Read
    [platform limitations](security/platform-limitations.md) before you rely on a
    guarantee.

## Why Isolattice

Running a command is easy. Knowing what it could reach, and that it is really
gone afterwards, is not.

| Problem | What Isolattice does |
| --- | --- |
| `subprocess` gives a process, not a boundary. Children escape, keep running and keep your files, network and credentials. | Each execution gets its own containment boundary: a container, a Linux namespace set with a cgroup, or a Windows Job Object. |
| Security options are silently ignored when a platform cannot enforce them. | Every backend reports each capability as supported, limited or unsupported, with evidence. A required capability that is not supported stops creation with `SandboxCapabilityError`. |
| A dead parent process does not mean the workload has stopped. | Termination is verified against the boundary's identity, not a PID. An outcome that cannot be verified is reported as unknown, never as success. |
| Every sandbox technology has its own API. | One typed, async API across Podman, the Docker API, Kubernetes, Linux namespaces and Windows. |
| Snapshots, artifacts and telemetry are tied to one vendor. | Storage, events and engine invocation are abstract base classes you implement or replace. |

## Highlights

- **Fail-closed policies.** Required guarantees are enforced or rejected, never
  downgraded with a warning.
- **Verified termination.** Graceful stop, forced stop and re-observation until
  the boundary is empty, within a deadline you set.
- **Bounded streaming.** Output is streamed with backpressure and capped, with
  truncation flags on the result.
- **Resource limits.** Memory, CPU and process limits per execution, and
  budgets across a whole instance where the backend supports them.
- **Workspaces, snapshots and checkpoints.** Persistent workspaces, verified
  workspace and container snapshots, and CRIU checkpoints on rootful Podman.
- **Your infrastructure.** Local or S3 artifact storage, your own event sink,
  your own command runner, or a backend of your own.
- **No required dependencies.** The core uses only the standard library.

## Backends

| Backend | Boundary | Status |
| --- | --- | --- |
| [Podman](backends/podman.md) | One container per execution, rootless by default | Implemented; verified live on Podman 6.1.4, rootless and rootful |
| [Docker API](backends/docker.md) | One container per execution through the Docker Engine API | Implemented; verified live on Docker Engine 29.9.3 and Podman's Docker-compatible API |
| [Kubernetes](backends/kubernetes.md) | One Pod per execution | Implemented; verified live on kind |
| [Linux namespaces](backends/linux.md) | User, PID, mount, network, IPC and UTS namespaces with a delegated cgroup | Implemented; verified live in WSL2 Ubuntu 24.04 |
| [Windows](backends/windows.md) | AppContainer token with a Job Object, or a Job Object alone | Implemented; verified live on Windows 11 |
| [macOS](backends/macos.md) | Seatbelt evaluation only | Proposed |
| [Remote](backends/remote.md) | Remote execution service | Proposed |

## A first look

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox, await sandbox.create(policy=policy) as instance:
        result = await instance.run(ExecutionRequest(argv=("/bin/echo", "hello")))
    print(result.stdout.decode(), result.termination.state)


asyncio.run(main())
```

Continue with the [quickstart](getting-started/quickstart.md), learn the
[concepts](concepts/introduction.md), or read the
[architecture](architecture/01-project-overview.md).
