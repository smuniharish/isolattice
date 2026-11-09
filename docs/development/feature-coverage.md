# Feature coverage

[docs/development/features.yml](https://github.com/smuniharish/isolattice/blob/main/docs/development/features.yml)
is the feature ledger. It maps each of the 19 example categories to the features
it plans, the subset that is implemented, the tests and runnable examples that
exercise them, and the tests still planned. This page summarizes it and compares
it with the code.

Implemented means implemented within each backend's threat model: shared-kernel
backends do not defend against kernel exploits, and no backend is certified for
hostile multi-tenant code.

## Ledger format

| Field | Meaning |
| --- | --- |
| `id`, `category` | Unique identifier and category number 1–19 (matches `examples/NN-*`) |
| `status` | `partial` (some features implemented) or `proposed` (none) |
| `features` | Everything the category plans |
| `implemented` | Subset of `features` that exists in code (`partial` only) |
| `tests` | Test sources that import `isolattice` and exercise the implementation; empty for `proposed` |
| `runnable_examples` | Scripts under `examples/`, each named in one of the `tests` |
| `planned_tests` | Tests still to write; not evidence |
| `api`, `architecture`, `examples`, `security` | Canonical documents for the category |

`scripts/validate_repository.py` enforces these rules; see
[examples](examples.md#repository-validator).

## Implemented (partial categories)

| # | Category | Implemented features | Runnable examples |
| --- | --- | --- | --- |
| 1 | Getting started | first sandbox, shell command, async ownership | `podman_hello.py` |
| 2 | Execution | argv, cwd, explicit environment, stdin, stdout/stderr streaming, backpressure, capture limits, exit codes, execution events, concurrent execution | `podman_hello.py`, `podman_io.py`, `podman_stream_cancel.py`, `podman_event_sink.py` |
| 3 | Security | isolated root, read-only and writable paths, path restrictions, no network, environment isolation, process visibility, privileges | `podman_hello.py`, `linux_namespaces.py`, `windows_appcontainer.py` |
| 4 | Resources | CPU, memory, scratch disk bytes, process count, wall time, aggregate (instance) accounting | `podman_limits.py`, `podman_instance_budget.py`, `windows_job_limits.py` |
| 5 | Lifecycle | stop/kill/close, grace/force, children and grandchildren, orphan prevention, boundary generation, ownership, stable termination | `podman_stream_cancel.py`, `windows_job_limits.py` |
| 6 | Async | async-only, concurrent sandboxes, cancellation (cooperative startup, repeated), timeout, bounded cleanup, failure cleanup, error groups | `podman_stream_cancel.py` |
| 7 | Capabilities | discovery, supported/limited/unsupported, explicit selection, backend failure | `podman_capabilities.py`, `windows_job_capabilities.py` |
| 8 | Native | Linux namespaces/cgroups/Landlock/seccomp, Windows Job Objects, AppContainer tokens and ACLs, restricted low-integrity token | `windows_job_limits.py`, `windows_job_capabilities.py`, `linux_namespaces.py`, `windows_appcontainer.py`, `windows_restricted_token.py` |
| 9 | Containers | Docker, Podman, rootless, explicit rootful, cgroup v2 budgets, connection provenance, engine streaming, verified container cleanup | `podman_hello.py`, `podman_limits.py`, `podman_stream_cancel.py`, `docker_isolated_run.py` |
| 10 | Kubernetes | local cluster, namespaces, requests/limits, security contexts, log streaming | `kubernetes_isolated_run.py` |
| 12 | Snapshots | persistence, export/import, container root snapshots, pluggable storage (local and S3), safe extraction | `podman_workspace_snapshot.py`, `podman_container_snapshot.py` |
| 13 | Checkpoints | process memory/register/thread state via CRIU, checkpoint storage | `podman_checkpoint.py` |
| 14 | Customization | custom backend, event sink, command runner, snapshot and checkpoint storage | `podman_custom_extensions.py` |
| 15 | Observability | lifecycle events, structured logs, safe correlation | `podman_event_sink.py` |
| 18 | Failures | unavailable backend, capability mismatch, termination failed, verification unknown, snapshot corruption, structured exceptions | `podman_failures.py` |

Tests per category are listed in the ledger. The main sources are the unit
contract tests (`test_async_runtime.py`, `test_properties.py`,
`test_termination.py`, `test_runtime_models.py`, `test_runtime_artifacts.py`,
`test_extension_points.py`, `test_s3_store_contract.py` and one contract file per
backend) and the live suites in `tests/integration/`.
See [testing](testing.md).

## Proposed

These have no implementation. The ledger lists `tests: []` for them.

| # | Category | What is proposed |
| --- | --- | --- |
| 11 | Remote execution | Remote backend and remote execution protocol: endpoint registry, authentication, leases, idempotency, scheduling, failover and orphan reconciliation |
| 16 | Framework integrations | FastAPI, Starlette, Jupyter, ML, data processing, LLM code and background workers |
| 17 | Production workflows | Multi-tenant services, secure code runners, distributed and failover applications |
| 19 | Platform-specific | Platform qualification and compatibility matrix, including the macOS backend (Seatbelt is under evaluation only) |

Also proposed, inside otherwise partial or ledger-proposed categories:

- macOS native backend (category 8 lists only a Seatbelt feasibility study).
- Metrics, tracing and OpenTelemetry exporters (category 15). Users can write an
  `EventSink` today.
- Kubernetes Jobs, RBAC, NetworkPolicy enforcement, volumes and node selection
  (category 10).
- `NetworkAccess.ALLOWLIST` enforcement, proxies, devices and arbitrary mounts
  (category 3).

## Not implemented inside partial categories

- Disk inode limits (category 4). Only scratch-space byte limits exist.
- Snapshot signing and authenticity (category 12). Snapshots carry SHA-256
  integrity manifests that are verified on every read, but they are not signed.
