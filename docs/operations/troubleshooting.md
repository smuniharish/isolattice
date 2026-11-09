# Troubleshooting

## Creation fails

| Error | Cause | Fix |
| --- | --- | --- |
| `SandboxCapabilityError: resource limits require delegated controllers: memory, pids` | The engine's cgroup lacks delegated controllers. | Use a connection with delegated controllers; see [deployment](deployment.md). |
| `SandboxCapabilityError` naming a required capability | The backend reports it `LIMITED` or `UNSUPPORTED`. | Read `report.capabilities[...].detail` from `discover()`; choose another backend or drop the requirement knowingly. |
| `SandboxCapabilityError` about networking on Kubernetes or Windows Job Objects | Those backends cannot restrict networking. | Set `network=NetworkAccess.ENABLED`, or use a backend that isolates networking. |
| `SandboxCapabilityError` mentioning rootful | The engine runs as host root. | Use a rootless engine, or pass `allow_rootful=True` to the backend deliberately. |
| `report.available` is `False` | The engine, connection, kubeconfig or kernel feature is missing. | Read `report.error`. Check the connection name with `podman system connection list`. |

## Execution fails

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Podman attach ended without an exited container` | The workload never started, often because `argv[0]` is not an absolute path and `PATH` is unset. | Use `/bin/sh` rather than `sh`, or pass `PATH` in `environment`. |
| `SandboxTimeoutError` | The execution passed its deadline. | Raise `ExecutionRequest.timeout` or `SandboxPolicy.execution_timeout`. |
| Output stops, then a timeout | Nobody read `handle.output()` and the queue filled. | Consume output while waiting. |
| `stdout_truncated` is true | Output exceeded `stdout_limit`. | Raise the limit, or write large output to the workspace. |
| `SandboxStateError: sandbox execution concurrency budget is exhausted` | Too many active executions. | Wait for some, or raise `max_concurrent_executions`. |

## Cleanup fails

`TerminationVerificationError` means the boundary could not be verified empty
within `cleanup_deadline`. The state is `unknown`, not terminated. Isolattice
keeps the resources owned and keeps cleaning up. `pending_late_cleanups` and
`LATE_CLEANUP_*` events report progress.

Do not remove containers, Pods or cgroups by hand while the owning process is
running. After it exits, list leftovers by label, for example
`podman ps -a --filter label=io.isolattice.sandbox`, and remove only those.

## Snapshots and checkpoints fail

| Error | Cause |
| --- | --- |
| `snapshots and checkpoints require Sandbox(artifact_store=...)` | No artifact store was given. |
| `artifact ... already exists` | Keys are write-once; choose a new key. |
| `artifact archive does not match its integrity manifest` | The stored archive changed after publication. Treat the store as tampered with. |
| `cold snapshots require no active executions` | Wait for executions to finish before `snapshot()`. |

See [exceptions](../architecture/12-exception-model.md) and the
[testing guide](../development/testing.md).
