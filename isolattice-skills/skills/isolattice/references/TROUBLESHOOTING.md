# Troubleshooting isolattice

Every refusal is a `SandboxCapabilityError` raised before the workload starts.
Read the message, fix the host or the policy, and never weaken a required
guarantee just to make the error go away.

| Message contains | Cause | Fix |
| --- | --- | --- |
| `required capability X is limited` or `is unsupported` | The backend cannot fully enforce a capability you required. | Choose a backend that supports it, or prepare the host; see `references/backends.md`. |
| `required capability X needs an explicit budget` | A limit capability was required without a value. | Set `memory_bytes`, `cpu_cores` or `process_limit`. |
| `needs its explicit policy setting` (`ValueError`) | `INSTANCE_LIMITS`, `DISK_LIMIT`, `WORKSPACE` or `SNAPSHOTS` was required without its setting. | Set `instance_budget`, `scratch_bytes` or `workspace=True`. |
| `resource limits require delegated controllers` | Rootless Podman lacks delegated cgroup controllers. | Delegate `memory`, `cpu` and `pids` to the user, or use a connection that has them. |
| `cgroup controllers are not delegated` | The Linux `cgroup_root` lacks controllers. | Run through `scripts/linux/isolattice-delegated-run.sh` or delegate a subtree with systemd. |
| `Landlock is unavailable` or `libseccomp.so.2 is unavailable` | The Linux host lacks a required mechanism. | Use a kernel with Landlock enabled and install libseccomp. |
| `is not rootless` | The connection runs a rootful engine. | Use a rootless connection, or pass `allow_rootful=True` deliberately. |
| `CRIU checkpoints require a rootful Podman engine` | Checkpoints were required on rootless Podman. | Use a rootful connection with `allow_rootful=True`. |
| `only disabled networking is implemented` / `supports only network=NONE` | The policy asked for network access the backend cannot grant. | Keep `network=NetworkAccess.NONE`. |
| `Job Objects cannot restrict networking` | `WindowsJobBackend` with `network=NONE`. | Set `network=NetworkAccess.ENABLED` to acknowledge it, or use `WindowsAppContainerBackend`. |
| `Job Objects cannot make the filesystem read-only` | `WindowsJobBackend` with `read_only_root=True`. | Set `read_only_root=False` to acknowledge it, or use `WindowsAppContainerBackend`. |
| `Pods always join the cluster network` | `KubernetesBackend` with `network=NONE`. | Set `network=NetworkAccess.ENABLED` and enforce a NetworkPolicy yourself. |
| `snapshots and checkpoints require Sandbox(artifact_store=...)` | No artifact store. | Pass `LocalArtifactStore(path)` or `S3ArtifactStore(...)`. |
| `snapshots require a policy with workspace=True` | Workspace snapshot without a workspace. | Set `workspace=True`. |

Other errors:

- `TerminationVerificationError`: cleanup was not verified within
  `TerminationPolicy.cleanup_deadline`. The state is unknown. Keep the process
  alive so late cleanup can finish, watch the `LATE_CLEANUP_*` events, and
  investigate the engine. Raise `cleanup_deadline` only for slow engines.
- `SandboxTimeoutError`: the execution exceeded its timeout and was
  terminated. Raise `ExecutionRequest.timeout` if the workload is legitimately
  slow.
- `SandboxStateError: execution output already has a consumer`: call
  `handle.output()` only once.
- `SandboxArtifactError`: the artifact is missing, already exists, is
  oversized, or failed SHA-256 verification. Never bypass verification.
