---
name: linux-sandbox-security
description: "Use for Linux namespaces, cgroup delegation, migration controls, Landlock, seccomp or native launcher changes."
---

# Linux sandbox security

Read [containment](../../../docs/architecture/10-process-containment.md) and
[Linux design](../../../docs/backends/linux.md).

## Workflow

1. Confirm helper/privilege architecture approval.
2. Probe kernel ABI/build features, namespace permissions and cgroups v2 delegation.
3. Separate supervisor authority from untrusted workload credentials.
4. Establish namespaces/root/mounts/cgroup/security before executing user code.
5. Prevent migration outside the subtree and management-file access.
6. Verify population/identity, force termination and resource release.
7. Test fork/spawn/detachment/migration and exhaustion on approved disposable hosts.

Use kernel APIs and reviewed maintained helpers/bindings. Landlock best-effort
examples must not weaken REQUIRED rights; seccomp alone is not a sandbox.

Done requires scoped real kernel tests, explicit unsupported cases, diagnostics,
docs and examples. Do not run adversarial exhaustion tests on shared hosts.
