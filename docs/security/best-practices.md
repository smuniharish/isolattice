# Security best practices

!!! warning "Know the limits first"
    Container, namespace and AppContainer backends share the host or node kernel,
    and the Windows Job Object backend is not a security boundary. For hostile or
    multi-tenant code, run workloads inside a VM or microVM boundary.

## Policy

- Select the backend explicitly by constructing it, and only after
  reading its [limitations](platform-limitations.md).
- List every guarantee you rely on in `required_capabilities`, so a weaker host fails
  closed with `SandboxCapabilityError`. See
  [capability enforcement](capability-enforcement.md).
- Set budgets for each required limit: `memory_bytes`, `cpu_cores`, `process_limit`,
  `scratch_bytes` and, where supported, `instance_budget`.
- Keep `network=NetworkAccess.NONE` and `read_only_root=True` unless the workload
  needs otherwise. Kubernetes and Windows Job cannot restrict networking.
- Keep `execution_timeout`, `stdout_limit` and `stderr_limit` as small as the workload
  allows, and check `stdout_truncated` and `stderr_truncated`.
- Run `await sandbox.discover()` on each deployment host and record the evidence.

## Requests

- Use an absolute `argv[0]` such as `/bin/sh`. Podman clears the image `PATH`.
- Pass only the environment the workload needs. Never pass long-lived credentials;
  arguments and environment values are visible to the engine owner.
- Treat stdout, stderr, exit codes and workspace files as untrusted input.

## Infrastructure

- Prefer rootless Podman; use `allow_rootful=True` only for CRIU checkpoints or pod
  instance budgets, on a host you treat as trusted.
- Delegate the `memory`, `cpu` and `pids` cgroup v2 controllers where you need limits.
- Never use the Windows Job backend as a security boundary; prefer AppContainer.
- Restrict access to engine sockets, kubeconfigs and artifact store credentials.
- Use a dedicated bucket or prefix for snapshots. Checkpoint archives contain
  workload memory and are trusted restore inputs.

## Lifecycle and monitoring

- Always use `async with` for `Sandbox` and `SandboxInstance` so cleanup runs.
- Treat `TerminationVerificationError`, `SandboxTerminationError` and any report state
  other than `terminated` as an incident, never as success.
- Alert on `SANDBOX_CLEANUP_FAILED`, `TERMINATION_UNKNOWN` and `LATE_CLEANUP_FAILED`
  events, and on a non-zero `instance.pending_late_cleanups`. See
  [observability](../operations/observability.md).
- Reconcile leftover containers, Pods and cgroups after a host crash. See
  [process escape prevention](process-escape-prevention.md).
