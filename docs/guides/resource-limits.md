# Resource limits

Isolattice applies memory, CPU and process budgets per execution, and optionally one
aggregate budget per instance. A requested budget is either enforced or the policy
is rejected; there is no best-effort mode.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope. Budgets limit accidental
    exhaustion; they are not a qualified denial-of-service defence.

## Per-execution budgets

| Field | Unit | Meaning |
| --- | --- | --- |
| `memory_bytes` | bytes | Memory cap for one execution and its descendants |
| `cpu_cores` | cores (float) | Hard CPU quota, for example `0.5` for half a core |
| `process_limit` | count | Maximum processes (and threads on Linux cgroups) |

```python
from isolattice import Capability, SandboxPolicy

policy = SandboxPolicy(
    memory_bytes=256 * 1024 * 1024,
    cpu_cores=0.5,
    process_limit=64,
    required_capabilities=(
        Capability.MEMORY_LIMIT,
        Capability.CPU_LIMIT,
        Capability.PROCESS_LIMIT,
    ),
)
```

Requiring `MEMORY_LIMIT`, `CPU_LIMIT` or `PROCESS_LIMIT` makes the backend check that
the controller is available on this host and that you set the matching budget. A
requirement without its budget raises `SandboxCapabilityError`.

## Per-backend support

| Backend | Memory | CPU | Processes | Mechanism |
| --- | --- | --- | --- | --- |
| Linux namespaces | yes | yes | yes (tasks) | Delegated cgroup v2 `memory.max`, `cpu.max`, `pids.max` |
| Windows Job Object | yes | yes | yes | Job committed-memory limit, CPU rate hard cap, active-process limit |
| Windows AppContainer | yes | yes | yes | Same Job Object limits |
| Podman | yes | yes | yes (tasks) | cgroup v2 with delegated controllers, verified inside the container |
| Docker API | yes | yes | yes (tasks) | cgroup v2, verified inside the container |
| Kubernetes | yes | yes | rejected | Container `limits`, read back from the workload cgroup |

Backend-specific rules:

- **Podman and Docker:** memory is at least 6 MiB with swap disabled. The CPU quota
  uses a 100,000 µs period and must be an exact integer of at least 1,000 µs, so a
  value that would need rounding is rejected. Rootless Podman needs the `cpu`,
  `memory` and `pids` controllers delegated; see [Podman Desktop](podman-desktop.md).
- **Kubernetes:** CPU must be a whole number of millicores, at least `10m`. Kubernetes
  only has a node-wide per-Pod PID limit, so `process_limit` is rejected.
- **Linux namespaces:** the `cgroup_root` passed to `LinuxNamespaceBackend` must be a
  delegated cgroup v2 directory you own. See [Linux](../backends/linux.md).
- **Windows:** the CPU rate is a share of all host processors, rounded up to the
  nearest 0.01%. See [Windows](../backends/windows.md).

## Instance budgets

`instance_budget` takes a `ResourceBudget` that all concurrent executions of one
instance share:

```python
from isolattice import Capability, ResourceBudget, SandboxPolicy

policy = SandboxPolicy(
    instance_budget=ResourceBudget(memory_bytes=512 * 1024 * 1024, cpu_cores=1.0),
    max_concurrent_executions=4,
    required_capabilities=(Capability.INSTANCE_LIMITS,),
)
```

| Backend | Instance budget |
| --- | --- |
| Linux namespaces | memory, CPU and processes (a parent cgroup) |
| Windows AppContainer | memory, CPU and processes (a parent Job Object) |
| Podman, rootful engine | memory and CPU (an owned pod); process budgets are rejected |
| Podman, rootless engine | rejected |
| Windows Job Object, Docker API, Kubernetes | rejected |

Requiring `INSTANCE_LIMITS` without an `instance_budget` raises `ValueError` when the
policy is constructed. See the
[instance budget example](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_instance_budget.py).

## Disk and scratch space

`scratch_bytes` caps `/tmp`; require `Capability.DISK_LIMIT` to make it mandatory.
`scratch_inodes` additionally caps how many files and directories `/tmp` can hold;
the Linux namespace backend and Docker Engine enforce it, while Podman, Docker API via
Podman and Kubernetes reject it.
Workspaces and Podman's writable container layer have no quota. See
[filesystem isolation](filesystem-isolation.md).

## Output limits

Captured output is bounded separately from the workload's resources:

| Field | Default | Meaning |
| --- | --- | --- |
| `stdout_limit` | 1 MiB | Bytes kept in `ExecutionResult.stdout`; extra bytes set `stdout_truncated` |
| `stderr_limit` | 1 MiB | Bytes kept in `ExecutionResult.stderr`; extra bytes set `stderr_truncated` |
| `output_queue_chunks` | 16 | Streamed chunks buffered before the workload is back-pressured |

## What happens when a limit is hit

The kernel or engine enforces the limit, not Isolattice. Memory exhaustion usually
kills the workload (a non-zero `exit_code`), CPU quotas throttle it, and process
limits make process creation fail inside the workload. Check `exit_code` and stderr;
Isolattice still terminates and verifies the boundary afterwards.

## Examples

- [Podman per-execution limits](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_limits.py)
- [Windows Job Object limits](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/windows_job_limits.py)
- [Policy reference](../reference/policies.md)
