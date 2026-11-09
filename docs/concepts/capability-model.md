# Capability model

A backend reports what it can enforce **as configured on this machine**, not
what its technology can do in general. `Sandbox.discover()` returns one
`BackendReport`:

| Field | Meaning |
| --- | --- |
| `name` | The backend name, such as `podman`. |
| `available` | Whether the backend can run workloads here. |
| `capabilities` | One `CapabilityEvidence(state, detail)` per `Capability`. |
| `limitations` | Plain-language caveats that apply to every policy. |
| `error` | Why the backend is unavailable, with no workload data. |

## States

| State | Meaning | Can be required |
| --- | --- | --- |
| `SUPPORTED` | Enforced, with the evidence in `detail`. | Yes |
| `LIMITED` | Partly enforced; `detail` says what is missing. | No |
| `UNSUPPORTED` | Not enforced here. | No |

## Capabilities

| Capability | Guarantee |
| --- | --- |
| `FILESYSTEM_ISOLATION` | The workload cannot read or write host files outside what the backend grants. |
| `NETWORK_ISOLATION` | The workload has no network access. |
| `PROCESS_ISOLATION` | The workload cannot see or signal host processes. |
| `VERIFIED_TERMINATION` | Every descendant can be terminated and verified gone. |
| `ENVIRONMENT_ISOLATION` | The workload sees only the requested environment. |
| `RESOURCE_LIMITS` | Kernel resource controls are available at all. |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | Per-execution limits are enforced. |
| `INSTANCE_LIMITS` | One budget across every execution in an instance. |
| `DISK_LIMIT` | Scratch space is capped. |
| `WORKSPACE` | A persistent workspace shared by an instance's executions. |
| `SNAPSHOTS` | Workspace snapshots can be published and restored. |
| `CHECKPOINTS` | A running execution can be checkpointed and restored. |

Evidence is specific. For example, the Podman backend reports
`NETWORK_ISOLATION` as `LIMITED` ("only an engine-created network namespace with
networking disabled"), so requiring it fails closed even though the workload
gets no network.

Policy settings are checked as well. Asking for `memory_bytes` on a connection
without a delegated memory controller fails even if you did not list
`MEMORY_LIMIT` as required.

See the [capability architecture](../architecture/06-capability-model.md) and
[capability enforcement](../security/capability-enforcement.md).
