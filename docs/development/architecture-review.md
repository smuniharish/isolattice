# Architecture review

This is the current assessment of what Isolattice implements, what it does not,
and the risks that remain. The numbered [architecture documents](../architecture/01-project-overview.md)
hold the full design; the
[decision records](../architecture/22-architecture-decision-records.md) hold the
decisions.

## Qualification status

!!! warning "Shared-kernel boundaries"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel, engine, runtime and cluster exploits are outside the threat model.
    The Windows Job Object backend is containment only, not a security boundary.
    No backend is certified for hostile multi-tenant workloads. Each backend is
    selected explicitly by constructing it. Live tests ran on the maintainer's
    machines (Podman 6.1.4 rootless and rootful, Docker Engine 29.9.3 in
    `docker:dind`, kind, WSL2 Ubuntu 24.04 and Windows 11) and are evidence only
    for those configurations. Hosted CI runs, independent security review and
    adversarial testing are not done.

## Implemented scope

**Core runtime.** Async-only `Sandbox` and `SandboxInstance` ownership,
`run`/`start`/`stop`/`kill`/`close`, execution handles with bounded
stdout/stderr streaming and truncation flags, timeouts and cancellation.
Capability discovery reports SUPPORTED, LIMITED or UNSUPPORTED with evidence,
and `required_capabilities` fail closed.

**Termination.** Verified, identity-based termination of a whole boundary
(container, cgroup, Job Object, Pod) with graceful, forced, verification and
release phases. `cleanup_deadline` bounds how long the caller waits; on expiry
`TerminationVerificationError` reports an unknown state, the instance keeps
ownership of unfinished cleanup and reports it through `LATE_CLEANUP_*` events.
UNKNOWN is never reported as terminated.

**Backends.**

| Backend | Boundary | Notes |
| --- | --- | --- |
| `PodmanBackend` | Container (pod for instance budgets) | Rootless by default; rootful needs `allow_rootful=True`. Clears the image environment. Network isolation is LIMITED |
| `DockerBackend` | Container | Docker Engine API client; live-tested against Podman's Docker-compatible API and Docker Engine in `docker:dind`. Keeps the image environment, so environment isolation is LIMITED |
| `KubernetesBackend` | One Pod per execution | Restricted security context, UID-preconditioned deletion; live-tested on kind. Cannot restrict networking |
| `LinuxNamespaceBackend` | User/PID/mount/network/IPC/UTS namespaces and a delegated cgroup v2 leaf | Read-only pivoted root, Landlock and seccomp where available; live-tested in WSL 2 |
| `WindowsJobBackend` | Job Object | Containment and limits only; not a security boundary. Optional low-integrity restricted token. Cannot restrict networking |
| `WindowsAppContainerBackend` | AppContainer (LPAC by default) plus Job Object | Per-instance profile and ACL grants; no network capability |

**Resources.** Per-execution memory, CPU and process limits on Podman, Linux,
Windows Job Object and AppContainer; instance budgets on rootful Podman (pod),
Linux (cgroup) and Windows; scratch-space limits.

**Artifacts.** Persistent workspaces and workspace snapshots on Podman,
`LinuxNamespaceBackend` and `WindowsAppContainerBackend`; whole-container
snapshots on Podman; CRIU execution checkpoints and restore on rootful Podman
only. Isolattice writes manifests and re-verifies size and SHA-256 after every
fetch, so stores never vouch for integrity. `LocalArtifactStore` and
`S3ArtifactStore` (conditional writes that never overwrite a key) are
implemented.

**Extension points.** `Backend`, `BackendSession`, `TerminationDriver`,
`CommandRunner`, `ArtifactStore` and `EventSink` are abstract base classes.
Instances are passed explicitly; nothing is discovered automatically.
Lifecycle events never carry workload output, environment values or secrets.

## Proposed

These do not exist in code:

- macOS backend. Seatbelt is under evaluation; robust descendant identity on
  macOS is unresolved.
- Remote backend and remote execution protocol: authenticated owner-scoped
  sessions, leases, idempotency, scheduling, failover and orphan reconciliation.
- Framework integrations (FastAPI, Starlette, Jupyter, workers).
- Metrics, tracing and OpenTelemetry exporters. An `EventSink` can forward
  events today.
- Kubernetes Jobs, RBAC, volumes and node selection; `NetworkAccess.ALLOWLIST`
  on any backend; arbitrary mounts; snapshot signing; checkpoint runtimes other
  than CRIU on rootful Podman.

New subsystems need architecture review before implementation.

## Open risks

- **Shared kernel.** Container and namespace backends share the host kernel; a
  kernel exploit defeats them. Isolattice does not claim protection against
  kernel compromise.
- **Configuration variance.** Rootless Podman, cgroup delegation, Landlock and
  seccomp availability, CNI behavior and Windows policies vary by host. Capability
  reports reflect what was probed, not every host.
- **Network.** Kubernetes and Windows Job Object backends cannot restrict
  networking. Podman reports network isolation as LIMITED. No allowlist exists.
- **Windows Job Object.** Filesystem, registry, network and system services stay
  reachable; it contains processes but does not isolate them.
- **AppContainer.** Shares the session and desktop with the caller; broker and
  WMI escape paths need ongoing review.
- **Stalled operations.** `cleanup_deadline` bounds the caller, not a stalled
  kernel, engine or uncooperative backend; such cleanup stays owned and is
  reported late.
- **Kubernetes verification.** API deletion is not proof that the node runtime
  is empty; node loss leaves the outcome unknown.
- **Artifacts.** Archives are extracted with validation, but snapshot capture is
  not atomic with respect to a running workload, and checkpoints contain process
  memory that may hold secrets.
- **Docker API.** Live-tested against Docker Engine in `docker:dind` and through
  Podman, not on a native Docker host.

## Open decisions

Strict profile defaults, instance reuse and concurrency semantics, Linux helper
and delegation model, macOS identity, Windows broker restrictions, Kubernetes
verification authority, remote trust and authentication, output and audit
semantics, snapshot authenticity and checkpoint compatibility.

## Evidence

- Unit and Hypothesis tests cover contracts and failure handling with test
  doubles; they prove no isolation.
- Live suites exercise Podman (rootless, delegated rootless and rootful), the
  Docker API via Podman and Docker Engine in `docker:dind`, Kubernetes on kind,
  Linux namespaces in WSL 2, Windows
  Job Object and AppContainer, and S3 storage against RustFS. Every runnable
  example is executed by its backend's live suite.
- CI defines lint, type checking, tests and live jobs; hosted runs are not yet
  verified.

See [testing](testing.md) and [feature coverage](feature-coverage.md).
