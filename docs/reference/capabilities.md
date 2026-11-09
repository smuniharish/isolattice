# Capabilities reference

A `Capability` names a guarantee; a backend reports a `CapabilityState` for each one
in `BackendReport.capabilities`, with a `CapabilityEvidence.detail` string explaining
why. Read the live report with `await sandbox.discover()`; the tables below summarize
what each built-in backend can report on a correctly configured host.

Every capability state is bounded by the backend's threat model: container,
namespace and AppContainer backends share the host or node kernel, and the
Windows Job Object backend is not a security boundary. See the
[capability model](../concepts/capability-model.md)
and the [models API](../api/models.md).

## States

| State | Meaning | Can be required? |
| --- | --- | --- |
| `SUPPORTED` | Enforced on this host, with probed evidence | yes |
| `LIMITED` | Partly enforced; `detail` names the gap | no (except `CHECKPOINTS` on Podman) |
| `UNSUPPORTED` | Not enforced on this host | no |

Listing a capability in `SandboxPolicy.required_capabilities` makes `Sandbox.create()`
raise `SandboxCapabilityError` unless the backend reports it SUPPORTED. Podman accepts a
required `CHECKPOINTS` at LIMITED because it then runs a live CRIU probe.

## Capabilities

| Capability | Value | Guarantee | Policy setting it needs when required |
| --- | --- | --- | --- |
| `FILESYSTEM_ISOLATION` | `filesystem_isolation` | The workload sees only an intended filesystem view | - |
| `NETWORK_ISOLATION` | `network_isolation` | No external network | `network=NONE` |
| `PROCESS_ISOLATION` | `process_isolation` | Host processes are invisible and unreachable | - |
| `VERIFIED_TERMINATION` | `verified_termination` | Emptiness and release of the boundary are observed | - |
| `ENVIRONMENT_ISOLATION` | `environment_isolation` | The host environment is not inherited | - |
| `RESOURCE_LIMITS` | `resource_limits` | Resource budgets in general | - |
| `MEMORY_LIMIT` | `memory_limit` | Per-execution memory cap | `memory_bytes` |
| `CPU_LIMIT` | `cpu_limit` | Per-execution CPU quota | `cpu_cores` |
| `PROCESS_LIMIT` | `process_limit` | Per-execution process count | `process_limit` |
| `SNAPSHOTS` | `snapshots` | Workspace or container snapshots | `workspace=True` or `read_only_root=False` |
| `CHECKPOINTS` | `checkpoints` | CRIU checkpoint and restore | - |
| `INSTANCE_LIMITS` | `instance_limits` | One budget shared by an instance | `instance_budget` |
| `DISK_LIMIT` | `disk_limit` | Size cap on `/tmp` scratch | `scratch_bytes` |
| `WORKSPACE` | `workspace` | Persistent per-instance workspace | `workspace=True` |

Requiring `SNAPSHOTS`, `INSTANCE_LIMITS`, `DISK_LIMIT` or `WORKSPACE` without its
setting raises `ValueError` when the `SandboxPolicy` is constructed. Requiring
`MEMORY_LIMIT`, `CPU_LIMIT` or `PROCESS_LIMIT` without its budget is rejected by
`Sandbox.create()`.

## Backend matrix

S = SUPPORTED, L = LIMITED, U = UNSUPPORTED. Budget rows assume the controllers or
engine features listed in the notes.

| Capability | Linux namespaces | Windows Job | Windows AppContainer | Podman | Docker API | Kubernetes |
| --- | --- | --- | --- | --- | --- | --- |
| `FILESYSTEM_ISOLATION` | S | U (L with `restricted_token`) | L | L | L | L |
| `NETWORK_ISOLATION` | S | U | S | L | L | U |
| `PROCESS_ISOLATION` | S | U (L with `restricted_token`) | L | L | L | L |
| `VERIFIED_TERMINATION` | S | L | L | L | L | L |
| `ENVIRONMENT_ISOLATION` | S | L | L | L | L | L |
| `RESOURCE_LIMITS` | S | L | L | L | L | L |
| `MEMORY_LIMIT` | S | S | S | S | S | S |
| `CPU_LIMIT` | S | S | S | S | S | S |
| `PROCESS_LIMIT` | S | S | S | S | S | U |
| `SNAPSHOTS` | S | U | S | S | U | U |
| `CHECKPOINTS` | U | U | U | L (rootful) | U | U |
| `INSTANCE_LIMITS` | S | U | S | S (rootful) | U | U |
| `DISK_LIMIT` | S | U | U | S | L | L |
| `WORKSPACE` | S | U | S | S | U | U |

Notes:

- **Linux namespaces:** budget and instance-limit rows need delegated cgroup v2
  controllers below `cgroup_root`; `RESOURCE_LIMITS` is U without any.
- **Podman:** `MEMORY_LIMIT`, `CPU_LIMIT` and `PROCESS_LIMIT` need the matching
  delegated controller on cgroup v2. `CHECKPOINTS` and `INSTANCE_LIMITS` are U on
  rootless engines.
- **Docker API:** budget rows need cgroup v2. It has been exercised only against
  Podman's Docker-compatible API.
- An unavailable backend reports every capability as U.

Per-backend evidence details are on the [Linux](../backends/linux.md),
[Windows](../backends/windows.md), [Podman](../backends/podman.md),
[Docker](../backends/docker.md) and [Kubernetes](../backends/kubernetes.md) pages. On
[macOS](../backends/macos.md) and against [remote engines](../backends/remote.md), use
those container backends; a native macOS backend and an Isolattice remote service are
proposed and not implemented.

## Example

```python
from isolattice import Capability, CapabilityState, Sandbox


async def supported(sandbox: Sandbox) -> list[Capability]:
    (report,) = await sandbox.discover()
    return [
        capability
        for capability, evidence in report.capabilities.items()
        if evidence.state is CapabilityState.SUPPORTED
    ]
```
