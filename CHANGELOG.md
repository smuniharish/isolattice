# Changelog

## 0.1.0

- Architecture and repository foundation for Isolattice.
- Documentation, example specifications, coding-agent workflows and validation.
- Async runtime and a rootless Podman backend.
- Bounded output, cancellation, owned container cleanup and Hypothesis properties.
- Enforced per-execution cgroup v2 memory/swap, exact CPU quota and task limits,
  gated on delegated controllers, inspected settings and pre-workload kernel checks.
- Explicit rootful-engine opt-in; no automatic engine fallback.
- Cancellation interrupts cooperative allocation/startup; control-process spawn
  and I/O share one deadline.
- `TerminationPolicy.cleanup_deadline` bounds when `wait()`, `cancel()` and
  `close()` return after a stop, timeout or completion. Expiry raises an
  `unknown`-state `TerminationVerificationError`; unfinished cleanup stays owned
  and logged rather than being abandoned silently.
- `WindowsJobBackend`: suspended Job Object assignment, verified
  committed-memory, CPU-rate and active-process limits, denied breakaway,
  descendant termination and kill-on-close. Not a security boundary.
- Backend sessions receive a `workload_exited` hook so descendants holding
  inherited output pipes cannot stall completion.
- Eight CLI examples (six Podman, two Windows) with exhaustive live coverage and
  bounded live OOM, task-denial, CPU-throttling and cgroup-write-denial tests.
- Provisioned Linux live-test CI configuration; hosted execution is not yet verified.
- Instance budgets (`SandboxConfig` aggregate CPU, memory and process limits),
  capped scratch space, persistent workspaces and validated workspace snapshot
  archives with SHA-256 manifests and safe extraction.
- Podman: rootful pod-based instance budgets, scratch tmpfs, workspace volumes
  and snapshots, and CRIU checkpoints restored into a new instance (rootful
  only, LIMITED). Includes a delegated rootless Podman machine service script.
- `LinuxNamespaceBackend`: user, mount, PID, network, IPC, UTS and
  cgroup namespaces; read-only pivoted root; uid 65534 without capabilities;
  `no_new_privs`, seccomp and Landlock; delegated cgroup v2 budgets; verified
  `cgroup.kill` termination. Verified live on WSL 2.
- `WindowsAppContainerBackend`: a per-instance LPAC profile, a
  private ACL-protected directory, nested instance and execution Job Objects,
  optional read-only grants and workspace snapshots. Verified live denial of
  network, user files, registry writes, host processes, scheduled tasks and WMI.
- `DockerBackend` for one explicit Docker API endpoint: read-only
  root, no network, UID 65534, no capabilities, verified cgroup limits and
  owned removal. Verified through Podman's Docker-compatible API and Docker
  Engine 29.9.3 running in `docker:dind`.
- `KubernetesBackend`: owned Pods in an explicit namespace,
  restricted security contexts, kernel-verified memory/CPU limits and
  UID-preconditioned deletion. Requires `network=ENABLED`; verified on kind.
- `WindowsJobBackend(restricted_token=True)`: low-integrity restricted token
  with Administrators deny-only and no privileges; hardening, not isolation.
- Podman container root snapshots: pause-consistent export of a running
  execution's writable root, verified import into a new instance and removal
  of the imported image on close.
- CI jobs for Windows native backends, a kind cluster, the Docker API and
  rootful Podman (not yet verified on hosted runners).
- Every backend is selected by constructing it and passing it to
  `Sandbox(backend=...)`. Containers, Pods, namespaces and AppContainers share
  the host or node kernel, so kernel, engine, runtime or cluster exploits are
  out of scope; Windows Job Objects are containment only, not a security
  boundary.
- macOS and remote backends, Kubernetes Jobs, RBAC and volumes, workspace disk
  quotas and rootless instance budgets or checkpoints are not implemented.
- Node-based Markdown tooling is installed globally rather than in the repository.
