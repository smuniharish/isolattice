---
category: 7
status: runnable
---

# 07 Capabilities and backends

Prints a backend's capability evidence and shows that a policy the backend cannot
enforce fails closed.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_capabilities.py`](https://github.com/smuniharish/isolattice/blob/main/examples/07-capabilities-and-backends/podman_capabilities.py) | `PodmanBackend.discover()` report: every capability with its state and detail; no workload container is created | Rootless Podman |
| [`windows_job_capabilities.py`](https://github.com/smuniharish/isolattice/blob/main/examples/07-capabilities-and-backends/windows_job_capabilities.py) | `WindowsJobBackend` report and limitations, plus two policies rejected with `SandboxCapabilityError` | Windows host |

Backends are never selected automatically: the caller constructs one backend and
passes it to `Sandbox(backend=...)`.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- `podman_capabilities.py`: an explicit **rootless** Podman connection and an
  already-local image.
- `windows_job_capabilities.py`: Windows. It takes no arguments.

## Run

```shell
uv run python examples/07-capabilities-and-backends/podman_capabilities.py --connection podman-machine-default --image docker.io/library/alpine:3.22
uv run python examples/07-capabilities-and-backends/windows_job_capabilities.py
```

## Expected output

`podman_capabilities.py` prints one line per `Capability`, for example:

```text
Backend: podman
filesystem_isolation: limited (read-only image root unless the policy requests ...)
network_isolation: limited (only an engine-created network namespace with networking disabled)
...
memory_limit: supported (cgroup memory.max with swap disabled; inspected and verified ...)
...
Qualification: Containers share the host kernel; kernel or engine exploits are out of scope.
```

States depend on the engine: `memory_limit`, `cpu_limit` and `process_limit` are
`unsupported` without the delegated cgroup v2 controller, and `checkpoints` and
`instance_limits` are `unsupported` on rootless engines. `network_isolation` is
`limited`, so requiring `Capability.NETWORK_ISOLATION` fails closed.

`windows_job_capabilities.py` prints every capability and limitation, then:

```text
Backend: windows-job (available: True)
filesystem_isolation: unsupported - ...
memory_limit: supported - ...
Limitation: ... not a security boundary ...
Default restricted policy: rejected
Required filesystem isolation: rejected
```

## Acceptance

- Both scripts raise if the backend reports itself unavailable.
- `windows_job_capabilities.py` raises unless the default policy (which requires
  `NetworkAccess.NONE` and a read-only root) and a policy requiring
  `FILESYSTEM_ISOLATION` are both rejected with `SandboxCapabilityError`.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  and
  [`tests/integration/test_windows_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_backend.py)
  run the scripts and check the lines above.

## Cleanup

Discovery creates no workload. Rejected policies fail before any boundary is
allocated. The Windows script uses a temporary working directory that is deleted on
exit.

## Security

- Capability states are evidence from probing the configured engine or OS, not
  inferred from the platform name.
- `SUPPORTED` describes what the backend enforces within its threat model; it does
  not extend that model to kernel or engine exploits.
- The Windows Job Object backend is not a security boundary: filesystem, registry,
  network and system services remain reachable.

See the [capability model](https://isolattice.readthedocs.io/en/latest/concepts/capability-model/)
and [capability reference](https://isolattice.readthedocs.io/en/latest/reference/capabilities/).
