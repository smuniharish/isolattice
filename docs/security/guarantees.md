# Security guarantees

!!! warning "Within each backend's threat model"
    The table describes what each backend reports and enforces on a correctly configured
    host. Shared-kernel backends do not defend against kernel exploits, and the
    Windows Job Object backend is not a security boundary. Read your host's actual
    report with `await sandbox.discover()`.

A capability is a guarantee only when the backend reports it `SUPPORTED` and you list it
in `SandboxPolicy.required_capabilities`. `LIMITED` means partial enforcement with known
gaps, described in the evidence text. See the
[capability model](../concepts/capability-model.md) and the
[capability reference](../reference/capabilities.md).

## Per-backend capabilities

States when the backend is available. **S** = `SUPPORTED`, **L** = `LIMITED`,
**U** = `UNSUPPORTED`. A footnote marks a host-dependent state.

| Capability | Podman | Docker | Kubernetes | Linux namespace | Windows Job | Windows AppContainer |
| --- | --- | --- | --- | --- | --- | --- |
| `FILESYSTEM_ISOLATION` | L | L | L | S | U (L with restricted token) | L |
| `NETWORK_ISOLATION` | L | L | U | S | U | S |
| `PROCESS_ISOLATION` | L | L | L | S | U (L with restricted token) | L |
| `VERIFIED_TERMINATION` | L | L | L | S | L | L |
| `ENVIRONMENT_ISOLATION` | L | L | L | S | L | L |
| `RESOURCE_LIMITS` | L [^1] | L [^2] | L | S [^1] | L | L |
| `MEMORY_LIMIT` | S [^1] | S [^2] | S | S [^1] | S | S |
| `CPU_LIMIT` | S [^1] | S [^2] | S | S [^1] | S | S |
| `PROCESS_LIMIT` | S [^1] | S [^2] | U | S [^1] | S | S |
| `INSTANCE_LIMITS` | S [^3] | U | U | S [^1] | U | S |
| `DISK_LIMIT` | S [^4] | L | L | S [^4] | U | U |
| `WORKSPACE` | S | U | U | S | U | S |
| `SNAPSHOTS` | S | U | U | S | U | S |
| `CHECKPOINTS` | L [^5] | U | U | U | U | U |

[^1]: Requires the matching cgroup v2 controller (`memory`, `cpu`, `pids`) to be
    delegated; otherwise `UNSUPPORTED`.
[^2]: Requires an engine that reports cgroup v2.
[^3]: Rootful Podman only, memory and CPU (no aggregate process budget).
[^4]: Size-capped `/tmp` tmpfs. Workspaces and a writable container root have no quota.
[^5]: Rootful Podman with CRIU only; cannot be combined with workspaces or instance budgets.

## Network settings each backend accepts

| Backend | Accepted `network` |
| --- | --- |
| Podman, Docker, Linux namespace, Windows AppContainer | `NetworkAccess.NONE` only |
| Kubernetes, Windows Job | `NetworkAccess.ENABLED` only (they cannot restrict networking) |

No backend supports `NetworkAccess.ALLOWLIST`.

## What holds for every backend

- The backend is used only because you constructed it; nothing is selected or
  substituted implicitly.
- Policy settings the backend cannot enforce raise `SandboxCapabilityError`.
- A required capability that is not `SUPPORTED` raises `SandboxCapabilityError` before
  anything runs.
- Success of termination means the boundary was observed empty and its resources were
  released. `unknown` is never success.
- Output is bounded by `stdout_limit` and `stderr_limit`, with truncation flags.

## Where the evidence comes from

| Backend | Enforcement source |
| --- | --- |
| [Podman](../backends/podman.md) | Container configuration inspected after creation; cgroup values checked inside the container before exec |
| [Docker](../backends/docker.md) | Same as Podman, plus capabilities, `no_new_privs`, seccomp mode and UID read from `/proc/self/status` |
| [Kubernetes](../backends/kubernetes.md) | Pod spec re-read from the API server; kernel state checked inside the container |
| [Linux namespace](../backends/linux.md) | User, PID, mount, network, IPC and UTS namespaces, delegated cgroup v2, Landlock and seccomp |
| [Windows Job](../backends/windows.md) | Job Object accounting and limits; optional low-integrity restricted token |
| [Windows AppContainer](../backends/windows.md) | Less-privileged AppContainer token plus nested Job Objects |

See [platform limitations](platform-limitations.md) for the gaps behind each `LIMITED`.
