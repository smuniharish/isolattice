# Examples

Nineteen runnable scripts exercise the built-in backends with benign workloads.
Each is run as a separate process by a live test against a real engine or platform,
checks its own results and cleanup, and raises on any mismatch. Shared-kernel
backends do not defend against kernel or engine exploits, and Windows Job Objects
are not a security boundary.

## Runnable scripts

| Script | What it verifies | Engine |
| --- | --- | --- |
| [`podman_hello.py`](01-getting-started/podman_hello.py) | Output, exit code and owned cleanup | Rootless Podman |
| [`podman_io.py`](02-execution/podman_io.py) | stdin, environment, cwd, concurrency, bounded captures | Rootless Podman |
| [`podman_limits.py`](04-resource-management/podman_limits.py) | Kernel memory, swap, CPU and task limits | Podman with delegated cgroup v2 controllers |
| [`podman_instance_budget.py`](04-resource-management/podman_instance_budget.py) | Limits shared by every execution in an instance | Rootful Podman |
| [`windows_job_limits.py`](04-resource-management/windows_job_limits.py) | Memory and process limits, timeout and descendant termination | Windows Job Object |
| [`podman_stream_cancel.py`](06-async-and-cancellation/podman_stream_cancel.py) | Fragmented readiness and verified cancellation | Rootless Podman |
| [`podman_capabilities.py`](07-capabilities-and-backends/podman_capabilities.py) | Supported, limited and unsupported capability report | Rootless Podman |
| [`windows_job_capabilities.py`](07-capabilities-and-backends/windows_job_capabilities.py) | Capability report and fail-closed policy rejection | Windows Job Object |
| [`linux_namespaces.py`](08-native-backends/linux_namespaces.py) | Namespaces, cgroup limits, Landlock, seccomp, blocked network | Linux with a delegated cgroup |
| [`windows_appcontainer.py`](08-native-backends/windows_appcontainer.py) | Denied profile, registry and network access | Windows AppContainer |
| [`windows_restricted_token.py`](08-native-backends/windows_restricted_token.py) | Low-integrity restricted token inside a Job Object | Windows Job Object |
| [`docker_isolated_run.py`](09-container-integrations/docker_isolated_run.py) | Fail-closed policy rejection, a container run and verified release | Docker API |
| [`kubernetes_isolated_run.py`](10-kubernetes/kubernetes_isolated_run.py) | Rejected unverifiable network isolation, a Pod run and verified release | Kubernetes |
| [`podman_workspace_snapshot.py`](12-snapshots/podman_workspace_snapshot.py) | Workspace snapshot and restore into a new instance | Rootless Podman |
| [`podman_container_snapshot.py`](12-snapshots/podman_container_snapshot.py) | Root filesystem snapshot of a running workload | Rootless Podman |
| [`podman_checkpoint.py`](13-checkpoints/podman_checkpoint.py) | CRIU checkpoint and restore of a running process | Rootful Podman with CRIU |
| [`podman_custom_extensions.py`](14-customization/podman_custom_extensions.py) | Custom `ArtifactStore` and `CommandRunner` | Rootless Podman |
| [`podman_event_sink.py`](15-observability/podman_event_sink.py) | Custom `EventSink` exporting JSON Lines | Rootless Podman |
| [`podman_failures.py`](18-failure-scenarios/podman_failures.py) | Policy rejection and timeout cleanup | Rootless Podman |

Run a script from the repository root, for example:

```shell
uv run python examples/01-getting-started/podman_hello.py --connection podman-machine-default --image docker.io/library/alpine:3.22
uv run python examples/07-capabilities-and-backends/windows_job_capabilities.py
```

Podman images must already be local and contain `/bin/sh`. Pass `--allow-rootful`
only for an explicitly chosen rootful engine; a missing capability is an error,
never a weaker fallback. Each category README lists the exact arguments, and
[live test configuration](../docs/development/testing.md) lists the environment
each live suite needs.

## Categories

| Category | Status | Covers |
| --- | --- | --- |
| [01 Getting started](01-getting-started/README.md) | runnable | First sandbox, shell commands, async ownership |
| [02 Execution](02-execution/README.md) | runnable | argv, cwd, environment, stdin, streaming, concurrency |
| [03 Security](03-security/README.md) | index | Security probes that live with each backend |
| [04 Resources](04-resource-management/README.md) | runnable | CPU, memory, scratch disk, process and time limits |
| [05 Lifecycle](05-process-lifecycle/README.md) | index | Start, wait, cancel, stop and kill with verified termination |
| [06 Async](06-async-and-cancellation/README.md) | runnable | Cancellation, timeouts and cleanup |
| [07 Capabilities](07-capabilities-and-backends/README.md) | runnable | Capability reports and fail-closed selection |
| [08 Native](08-native-backends/README.md) | runnable | Linux and Windows native enforcement |
| [09 Containers](09-container-integrations/README.md) | runnable | Docker API backend |
| [10 Kubernetes](10-kubernetes/README.md) | runnable | Kubernetes Pod backend |
| [11 Remote](11-remote-execution/README.md) | proposed | Multi-endpoint remote execution |
| [12 Snapshots](12-snapshots/README.md) | runnable | Workspace and container snapshots |
| [13 Checkpoints](13-checkpoints/README.md) | runnable | CRIU checkpoints |
| [14 Customization](14-customization/README.md) | runnable | Extension points |
| [15 Observability](15-observability/README.md) | runnable | Lifecycle events |
| [16 Frameworks](16-framework-integrations/README.md) | proposed | Web, notebook and worker integrations |
| [17 Production](17-production-workflows/README.md) | index | Building blocks shown in other categories |
| [18 Failure](18-failure-scenarios/README.md) | runnable | Structured failures and cleanup |
| [19 Platforms](19-platform-specific/README.md) | index | Platform-specific scripts by backend |

`runnable` categories have scripts, `index` categories point to scripts elsewhere,
and `proposed` categories describe features that are not implemented. Use
[the template](templates/example.md) for a new category README.

## Validation

[`scripts/validate_repository.py`](../scripts/validate_repository.py) checks every
category README: its front matter, required sections, links, and that its status
agrees with the scripts in the directory. Each backend's live suite runs its scripts
and has an inventory test that fails when a script has no live test case. A skipped
live run does not count as a passing example, and a passing one is evidence for the
tested configuration only, not proof of production security.

See [feature coverage](../docs/development/feature-coverage.md) and the
[examples guide](../docs/development/examples.md).
