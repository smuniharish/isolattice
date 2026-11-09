# Policies reference

Policies are frozen dataclasses validated when constructed: a wrong type raises
`TypeError` and an out-of-range value raises `ValueError`. The backend then checks the
policy in `Sandbox.create()` and raises `SandboxCapabilityError` for anything it cannot
enforce. See the [models API](../api/models.md) for docstrings and the
[policy model](../architecture/05-policy-model.md) for the design.

## SandboxPolicy

Passed to `Sandbox.create(policy=...)`; `None` means `SandboxPolicy()`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `network` | `NetworkAccess` | `NONE` | `NONE`, `ENABLED` or `ALLOWLIST` (always rejected); see [network isolation](../guides/network-isolation.md) |
| `read_only_root` | `bool` | `True` | Mount the root filesystem read-only |
| `memory_bytes` | `int` or `None` | `None` | Per-execution memory cap in bytes |
| `cpu_cores` | `float` or `None` | `None` | Per-execution hard CPU quota in cores |
| `process_limit` | `int` or `None` | `None` | Per-execution process (or task) limit |
| `instance_budget` | `ResourceBudget` or `None` | `None` | One budget shared by all executions of the instance |
| `scratch_bytes` | `int` or `None` | `None` | Size cap for the per-execution `/tmp` |
| `workspace` | `bool` | `False` | Create a persistent per-instance workspace |
| `required_capabilities` | `tuple[Capability, ...]` | `()` | Capabilities that must be SUPPORTED, or creation fails |
| `execution_timeout` | `float` | `30.0` | Default seconds per execution; `ExecutionRequest.timeout` overrides it |
| `max_concurrent_executions` | `int` | `4` | Active executions allowed per instance |
| `output_queue_chunks` | `int` | `16` | Streamed chunks (up to 64 KiB each) buffered before backpressure |
| `stdout_limit` | `int` | `1048576` | Bytes of stdout kept in `ExecutionResult`; `0` keeps none |
| `stderr_limit` | `int` | `1048576` | Bytes of stderr kept in `ExecutionResult`; `0` keeps none |
| `termination` | `TerminationPolicy` | `TerminationPolicy()` | Termination attempts and deadlines |

Validation at construction:

- Numbers must be positive (`stdout_limit` and `stderr_limit` may be `0`).
- Requiring `INSTANCE_LIMITS`, `DISK_LIMIT` or `WORKSPACE` needs `instance_budget`,
  `scratch_bytes` or `workspace=True` respectively; requiring `SNAPSHOTS` needs
  `workspace=True` or `read_only_root=False`.

Backend-specific acceptance is summarized in the
[capability matrix](capabilities.md#backend-matrix) and the guides for
[filesystems](../guides/filesystem-isolation.md) and
[resource limits](../guides/resource-limits.md).

## ResourceBudget

Used for `SandboxPolicy.instance_budget`. At least one field must be set.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `memory_bytes` | `int` or `None` | `None` | Shared memory cap in bytes |
| `cpu_cores` | `float` or `None` | `None` | Shared hard CPU quota in cores |
| `process_limit` | `int` or `None` | `None` | Shared process limit (rejected by Podman) |

## TerminationPolicy

Used for `SandboxPolicy.termination`. See
[process-tree termination](../guides/process-tree-termination.md).

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `attempts` | `int` | `3` | Termination attempts before reporting failure |
| `graceful_timeout` | `float` | `5.0` | Seconds to wait after a graceful stop; `0` allowed. Windows backends always use `0` |
| `force_timeout` | `float` | `2.0` | Seconds to wait after a forced stop |
| `verification_interval` | `float` | `0.5` | Seconds between the two matching observations, and between polls |
| `retry_delay` | `float` | `1.0` | Seconds between attempts; `0` allowed |
| `total_timeout` | `float` | `60.0` | Bound on the whole termination coordinator |
| `cleanup_deadline` | `float` | `90.0` | Bound on how long the caller waits for cleanup before a late cleanup takes over |

## ExecutionRequest

Passed to `SandboxInstance.run()` and `start()`.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `argv` | `tuple[str, ...]` | required | Command and arguments, never shell-interpreted; `argv[0]` non-empty, not starting with `-`; no NUL |
| `stdin` | `bytes` or `None` | `None` | Input written to the workload, then closed |
| `environment` | `Mapping[str, str]` | `{}` | Explicit variables; identifier names, values without NUL/CR/LF, 64 KiB total |
| `cwd` | `str` | `"/"` | Absolute POSIX working directory without `..` |
| `timeout` | `float` or `None` | `None` | Per-execution timeout overriding `execution_timeout` |

On Windows backends `argv[0]` must be an absolute path with a drive letter.

## NetworkAccess

| Member | Value |
| --- | --- |
| `NONE` | `none` |
| `ENABLED` | `enabled` |
| `ALLOWLIST` | `allowlist` |

## Example

```python
from isolattice import (
    Capability,
    NetworkAccess,
    ResourceBudget,
    SandboxPolicy,
    TerminationPolicy,
)

policy = SandboxPolicy(
    network=NetworkAccess.NONE,
    memory_bytes=256 * 1024 * 1024,
    cpu_cores=0.5,
    process_limit=64,
    instance_budget=ResourceBudget(memory_bytes=1024 * 1024 * 1024, cpu_cores=2.0),
    scratch_bytes=32 * 1024 * 1024,
    required_capabilities=(Capability.MEMORY_LIMIT, Capability.DISK_LIMIT),
    execution_timeout=60.0,
    max_concurrent_executions=2,
    termination=TerminationPolicy(graceful_timeout=2.0, cleanup_deadline=30.0),
)
```
