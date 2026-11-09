---
category: 19
status: index
---

# 19 Platform specific

This directory has no scripts. Platform-specific runnable examples live with their
backend:

| Platform | Backend | Scripts |
| --- | --- | --- |
| Linux | `LinuxNamespaceBackend` | [08 `linux_namespaces.py`](../08-native-backends/README.md) |
| Windows | `WindowsJobBackend` | [04 `windows_job_limits.py`](../04-resource-management/README.md), [07 `windows_job_capabilities.py`](../07-capabilities-and-backends/README.md), [08 `windows_restricted_token.py`](../08-native-backends/README.md) |
| Windows | `WindowsAppContainerBackend` | [08 `windows_appcontainer.py`](../08-native-backends/README.md) |
| Any host with Podman | `PodmanBackend` | [01](../01-getting-started/README.md), [02](../02-execution/README.md), [04](../04-resource-management/README.md), [06](../06-async-and-cancellation/README.md), [07](../07-capabilities-and-backends/README.md), [12](../12-snapshots/README.md), [13](../13-checkpoints/README.md), [18](../18-failure-scenarios/README.md) |
| macOS | None | A macOS backend is proposed (Seatbelt evaluation only) |

## Prerequisites

- Linux: unprivileged user namespaces, Landlock, libseccomp and a delegated cgroup v2
  subtree, for example through
  [`scripts/linux/isolattice-delegated-run.sh`](https://github.com/smuniharish/isolattice/blob/main/scripts/linux/isolattice-delegated-run.sh).
  Live-tested in WSL2.
- Windows: Windows 10 1703 or later for AppContainer, with a Python installed under
  Program Files for AppContainer workloads.
- Podman on Windows or macOS runs inside a Podman machine VM; resource limits need
  delegated cgroup controllers on the connection, and checkpoints and instance
  budgets need its rootful connection.

## Acceptance

Platform examples print the backend's own capability evidence and limitations, and
check native identity (cgroup, Job Object, AppContainer SID or container ID) rather
than inferring support from the OS name. Skipped tests are reported as skips, not
passes.

## Cleanup

Each platform verifies that its own boundary is empty and released: cgroup
`populated 0` on Linux, Job Object accounting on Windows, and engine state plus
container removal for Podman.

## Security

- No backend is certified for hostile multi-tenant use.
- No platform is assumed to be equivalent to Linux. The Windows Job Object backend is
  not a security boundary, and shared-kernel backends remain exposed to kernel
  vulnerabilities.

See [platform limitations](https://isolattice.readthedocs.io/en/latest/security/platform-limitations/)
and the [testing guide](https://isolattice.readthedocs.io/en/latest/development/testing/).
