---
category: 4
status: runnable
---

# 04 Resource management

Per-execution limits, shared instance budgets and scratch caps, verified from inside
the workload.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_limits.py`](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_limits.py) | Per-execution memory (no swap), CPU and process limits read back from cgroup v2 | Podman with delegated cgroup v2 controllers |
| [`podman_instance_budget.py`](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/podman_instance_budget.py) | One memory/CPU budget shared by all executions of an instance, plus a `/tmp` scratch cap | **Rootful** Podman |
| [`windows_job_limits.py`](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/windows_job_limits.py) | Job Object memory and process limits, timeout and descendant termination | Windows host |

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- `podman_limits.py`: a Podman connection whose engine reports cgroup v2 with the
  `memory`, `cpu` and `pids` controllers delegated, and a local image containing
  `/bin/sh` and `/bin/cat`. A rootless engine needs controller delegation on the
  connection; missing delegation raises `SandboxCapabilityError`.
- `podman_instance_budget.py`: an explicitly selected **rootful** connection (for
  example `podman-machine-default-root`) and a local image with `/bin/sh` and `dd`.
  Instance budgets are rejected on rootless engines.
- `windows_job_limits.py`: Windows. The workload interpreter defaults to the base
  CPython (`sys.base_prefix\python.exe`); pass `--python` to choose another absolute
  interpreter path (not a virtual-environment redirector).

## Run

```shell
uv run python examples/04-resource-management/podman_limits.py --connection podman-machine-default --image docker.io/library/alpine:3.22
uv run python examples/04-resource-management/podman_instance_budget.py --connection podman-machine-default-root --image docker.io/library/alpine:3.22 --allow-rootful
uv run python examples/04-resource-management/windows_job_limits.py
```

`podman_limits.py` accepts `--allow-rootful` for an explicitly chosen rootful engine.
`podman_instance_budget.py` requires it; without the flag a rootful engine is rejected.

## Expected output

`podman_limits.py`:

```text
Kernel memory.max: 67108864
Kernel memory.swap.max: 0
Kernel cpu.max: 25000 100000
Kernel pids.max: 8
Owned container released: True
```

`podman_instance_budget.py`:

```text
Single 40 MiB fill: fits the 64 MiB instance budget
Two concurrent fills: refused by the shared budget
Owned containers released: True
Owned pod removed: True
```

`windows_job_limits.py`:

```text
Memory budget: allocation beyond 128 MiB failed
Process budget: 1 of 4 child processes admitted
Timeout cleanup: terminated
Descendant process terminated: True
```

## Acceptance

- `podman_limits.py` requires `MEMORY_LIMIT`, `CPU_LIMIT` and `PROCESS_LIMIT` with
  `memory_bytes=64 MiB`, `cpu_cores=0.25` and `process_limit=8`, and raises unless the
  workload reads exactly those values from `/sys/fs/cgroup`.
- `podman_instance_budget.py` requires `INSTANCE_LIMITS` with
  `ResourceBudget(memory_bytes=64 MiB, cpu_cores=0.5)` and `scratch_bytes=48 MiB`.
  One 40 MiB fill of `/tmp` must succeed; two concurrent fills must not both succeed
  (exit 3 from tmpfs `ENOMEM` or 137 from the OOM killer).
- `windows_job_limits.py` requires memory, CPU and process limits with 128 MiB,
  one core and two processes. A 512 MiB allocation must fail with `MemoryError`, only
  one of four children may start, and a timed-out tree must be terminated including
  its grandchild.

The Podman scripts are run by
[`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
(`ISOLATTICE_TEST_PODMAN_RESOURCE_*` and `ISOLATTICE_TEST_PODMAN_ROOTFUL_*`);
[`test_podman_resources.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_resources.py)
also covers OOM, denied forks and CPU throttling. The Windows script is run by
[`tests/integration/test_windows_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_backend.py).

## Cleanup

Each execution's container (or Job Object) is verified empty and released before its
result returns. Closing the budgeted instance removes its owned pod. The Windows
script works in a temporary directory that is deleted on exit.

## Security

- Podman containers share the host kernel; kernel or engine exploits are out of
  scope.
- Limits are enforced by cgroup v2 or Job Objects, not by watching the direct child.
- The Windows Job Object backend is **not a security boundary**: the workload keeps
  the user's filesystem, registry and network access.
- Workspace volumes and writable container layers have no disk quota, so requiring
  `DISK_LIMIT` with them is rejected.
- Fixtures allocate bounded amounts; do not adapt them into unbounded fork or memory
  bombs on a shared host.

See [resource limits](https://isolattice.readthedocs.io/en/latest/guides/resource-limits/).
