# Platform limitations

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel, engine, runtime and cluster exploits are out of scope. The Windows
    Job Object backend is containment only, not a security boundary. The
    limitations below are known gaps, not a complete list.

## All backends

- **Shared kernel.** Every implemented backend runs workloads on the host kernel (or
  the Podman machine or node kernel). A kernel vulnerability defeats the boundary. Use
  a [VM or microVM](../comparisons/vm-and-microvm-vs-isolattice.md) when that matters.
- **Trusted infrastructure.** The engine, cluster, kernel and their configuration are
  trusted. A compromised engine can report a consistent lie.
- **No allowlisted networking.** `NetworkAccess.ALLOWLIST` is not supported anywhere.
- **Visible arguments.** Command arguments and explicit environment values are visible
  to the engine owner or namespace readers. Do not pass long-lived secrets this way.

## Podman

- Rootless (the default) runs the engine as your user. Memory, CPU and process limits
  need the `memory`, `cpu` and `pids` cgroup v2 controllers delegated on the
  connection; without them, policies that set those limits fail closed.
- Rootful needs `allow_rootful=True` and gives the engine host root. Instance budgets
  (pod cgroups) and CRIU checkpoints work only on a rootful engine. On Linux, Podman's
  client also refuses checkpoint and restore unless the calling process is root.
- `NETWORK_ISOLATION` is `LIMITED`, so requiring it fails closed.
- The image environment is cleared: `PATH` is unset, so `argv[0]` must be absolute.
- Workspace volumes and a writable container root have no disk quota.
- Container snapshots capture the writable layer only, not the workspace volume or
  `/tmp`. Checkpoint archives contain workload memory and are trusted restore inputs.

See the [Podman backend](../backends/podman.md).

## Docker

- The image environment is retained because the Docker API cannot unset it, so
  `ENVIRONMENT_ISOLATION` is `LIMITED`.
- No workspaces, snapshots, checkpoints or instance budgets.
- Engines may add writable tmpfs mounts (such as `/dev/shm`) that only the memory limit
  bounds.
- Live-tested through Podman's Docker-compatible API and a nested test engine, not on a
  native Docker host.

See the [Docker backend](../backends/docker.md).

## Kubernetes

- Pods always join the cluster network. Policies must use `network=NetworkAccess.ENABLED`;
  NetworkPolicy enforcement depends on the CNI plug-in and is not verified.
- No per-execution process limit (Kubernetes offers only a node-wide per-Pod PID limit).
- The image environment, `HOSTNAME` and `KUBERNETES_SERVICE_*` variables are always
  present. Output also stays in node container logs until the Pod is deleted.
- Termination is confirmed when the API server no longer reports the Pod; the API
  server, kubelet and container runtime are trusted.
- No workspaces, snapshots, checkpoints or instance budgets. Live-tested on a local kind
  cluster only.

See the [Kubernetes backend](../backends/kubernetes.md).

## Linux namespace

- Needs unprivileged user namespaces and a delegated cgroup v2 root. Landlock and
  seccomp are applied where the kernel provides them.
- Supports only `network=NetworkAccess.NONE`. Supplementary groups of the calling user
  are retained, and configured read-only host paths are visible to workloads.
- No checkpoints (CRIU needs privileges the backend does not have) and no workspace
  quota. Live-tested in WSL2 only.

See the [Linux backend](../backends/linux.md).

## Windows Job Object

- **Not a security boundary.** Workloads run as your user with your filesystem,
  registry, network and IPC access. The Job Object provides process-tree containment
  and limits only.
- `restricted_token=True` adds a low-integrity token that blocks writes to
  medium-integrity objects, but reads, network and system-service brokers remain.
- Cannot restrict networking: policies must use `network=NetworkAccess.ENABLED`.
- No workspaces, snapshots, scratch caps or instance budgets. Termination is forced.

## Windows AppContainer

- Shares the kernel, session and desktop; services that accept AppContainer clients
  stay reachable, and processes they broker are not contained.
- Executables must be readable by the AppContainer; grant others via `read_only_paths`.
  Interpreters inside user profiles may fail to load.
- No disk quota, no checkpoints, forced termination only. Profiles and ACL grants left
  by a crashed host process are not reconciled.

See the [Windows backends](../backends/windows.md).

## Not implemented

- **Native macOS backend:** none; Seatbelt is being evaluated. macOS hosts use the
  container backends. See [macOS](../backends/macos.md).
- **Isolattice remote service:** proposed only. Remote engines through Podman, Docker
  or Kubernetes connections are supported. See [remote](../backends/remote.md).
