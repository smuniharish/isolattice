# Choosing an isolattice backend

Construct a backend and pass it to `Sandbox(backend=...)`. Containers, Pods,
namespaces and AppContainers share the host (or node) kernel, so kernel,
engine, runtime or cluster exploits are out of scope; Windows Job Objects are
not a security boundary. Choose by host and by the guarantees the
application requires; never pick a weaker one silently.

| Host | Backend | Main guarantees | Notes |
| --- | --- | --- | --- |
| Linux, or WSL2 | `LinuxNamespaceBackend(cgroup_root=...)` | user, PID, mount, network, IPC and UTS namespaces; read-only root; Landlock; seccomp; cgroup v2 limits | Needs Landlock, `libseccomp.so.2` and a delegated, empty cgroup v2 subtree. Shares the host kernel. |
| Any host with Podman | `PodmanBackend(connection=..., image=...)` | container isolation, no network, read-only root, verified cgroup limits, workspaces, snapshots | Rootless by default. Limits need delegated cgroup controllers. |
| Rootful Podman | `PodmanBackend(..., allow_rootful=True)` | adds CRIU checkpoints and pod instance budgets | The engine runs as root; opt in deliberately. |
| Docker Engine API | `DockerBackend(host=..., image=...)` | container isolation, no network, read-only root, verified limits | Verified against Podman's Docker-compatible API and Docker Engine in `docker:dind`. No workspaces or instance budgets. |
| Kubernetes | `KubernetesBackend(kubeconfig=..., context=..., namespace=..., image=...)` | one Pod per execution, restricted security context, verified limits | Needs `network=NetworkAccess.ENABLED`; no per-Pod process limit. |
| Windows | `WindowsAppContainerBackend()` | AppContainer token (less privileged by default), no network capability, Job Object limits, workspaces | Workloads write only to their instance directory. |
| Windows | `WindowsJobBackend(working_directory=...)` | process-tree containment, memory, CPU-rate and process limits, kill on close | Not a security boundary. Needs `network=ENABLED` and `read_only_root=False`. |

macOS and remote execution have no backend.

## Rootless and rootful Podman

A rootless engine runs as your user, so an escape lands as that user rather
than as root. Its connection enforces memory, CPU and process limits only
when the user's cgroup has delegated `memory`, `cpu` and `pids` controllers;
otherwise a policy with limits is refused. A rootful engine runs as root:
pass `allow_rootful=True` only when you need CRIU checkpoints or pod-level
instance budgets.

```text
podman system connection list     # pick the connection name
```

## Check before relying on a backend

```python
reports = await sandbox.discover()
for capability, evidence in reports[0].capabilities.items():
    print(capability.value, evidence.state.value, evidence.detail)
```

`state` is `supported`, `limited` or `unsupported`; only `supported`
satisfies a required capability. `limitations` lists what the backend never
enforces on this host.

Full guides: <https://isolattice.readthedocs.io/en/latest/backends/linux/>.
