# Deployment

This page covers running Isolattice inside a long-lived service, such as a job
runner, an agent tool server or a grading service.

!!! warning "Not for hostile code"

    No built-in backend is qualified for hostile or multi-tenant workloads. If
    one tenant must be protected from another, put each tenant behind a
    stronger boundary of its own, such as a separate VM.

## Choose a backend

| Situation | Backend | Configure |
| --- | --- | --- |
| Linux host, no daemon | [Linux namespaces](../backends/linux.md) | A delegated cgroup v2 subtree. |
| Linux, macOS or Windows host with Podman | [Podman](../backends/podman.md) | A rootless connection with delegated controllers; rootful only for checkpoints and pod budgets. |
| Existing Docker API endpoint | [Docker API](../backends/docker.md) | An explicit `host`; rootful engines need `allow_rootful=True`. |
| Kubernetes cluster | [Kubernetes](../backends/kubernetes.md) | A dedicated namespace and a least-privilege service account. |
| Windows host | [Windows](../backends/windows.md) | AppContainer for isolation; the Job Object backend for containment only. |

## Run the service with least privilege

- Run the service as a dedicated, unprivileged user.
- Prefer rootless engines. Rootful Podman or Docker means the engine runs with
  host root.
- Use a dedicated Podman connection, Docker endpoint or Kubernetes namespace for
  sandboxes. Backends touch only resources they labelled for their own
  sandboxes. Sharing still lets other users of the engine see those resources.
- Keep images local and pinned by digest. Isolattice never pulls, so updating an
  image is an explicit operation.
- Give the service only the credentials it needs: an engine socket, a
  kubeconfig or S3 keys. Never pass them into `ExecutionRequest.environment`.

### Rootless Podman with delegated controllers

Rootless Podman can enforce memory, CPU and process limits only when systemd
delegates those cgroup controllers to the user. On Podman machines running in
WSL, the repository's
[`scripts/podman-machine`](https://github.com/smuniharish/isolattice/tree/main/scripts/podman-machine)
installs a rootless API service inside a delegated subtree, as a systemd
template unit `isolattice-rootless-podman@UID.service`. Run
`Sandbox.discover()` afterwards and check that `MEMORY_LIMIT` is `SUPPORTED`.

### Linux namespaces under systemd

Start the service with `Delegate=yes` and pass the delegated subtree as
`cgroup_root`, or use
[`isolattice-delegated-run.sh`](https://github.com/smuniharish/isolattice/blob/main/scripts/linux/isolattice-delegated-run.sh)
to get one from the user manager.

## Own the lifecycle

Create one `Sandbox` per backend at startup and close it at shutdown:

```python
import asyncio
import signal

from isolattice import Sandbox


async def serve(sandbox: Sandbox) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    async with sandbox:
        await stop.wait()  # handle requests until asked to stop
```

On shutdown, Isolattice stops every active execution and verifies each
boundary. Give the service's stop timeout, such as systemd's
`TimeoutStopSec` or a Pod's `terminationGracePeriodSeconds`, more than the
policy's `TerminationPolicy.cleanup_deadline`, so verification can finish.

When cleanup fails, `close` raises a `BaseExceptionGroup`. A boundary that is
still being cleaned up after its deadline stays owned: its instance's resources
are kept, and `SandboxInstance.pending_late_cleanups` counts it. Alert on that
condition. Never delete resources by hand while the service is running.

## Monitor

Install an [event sink](observability.md) and alert on:

- `TERMINATION_UNKNOWN`: a boundary could not be verified within its deadline;
- `LATE_CLEANUP_FAILED`: cleanup after the deadline also failed;
- `SANDBOX_CLEANUP_FAILED`: closing an instance failed.

## Store artifacts

Snapshots and checkpoints can contain workload memory and files. Store them in
a private location:

- `LocalArtifactStore` on a directory only the service can read;
- `S3ArtifactStore` with a dedicated bucket or prefix, encryption at rest and a
  lifecycle rule.

See [snapshots](../guides/snapshots.md).
