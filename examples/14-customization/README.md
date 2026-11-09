---
category: 14
status: runnable
---

# 14 Customization

Plugging your own extension points into a sandbox. Each extension point is an abstract
base class; you subclass it and pass the instance in explicitly. Nothing is discovered
or imported automatically.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_custom_extensions.py`](https://github.com/smuniharish/isolattice/blob/main/examples/14-customization/podman_custom_extensions.py) | A SQLite `ArtifactStore` checked with `check_artifact_store`, a counting `CommandRunner` passed to `PodmanBackend(runner=...)`, and a workspace snapshot round trip through the custom store | Rootless Podman |

| Extension point | Module | Built-in implementations | Passed to |
| --- | --- | --- | --- |
| `ArtifactStore` | `isolattice.storage` | `LocalArtifactStore(root)`, `S3ArtifactStore(...)` (`isolattice[s3]` extra) | `Sandbox(artifact_store=...)` |
| `EventSink` | `isolattice.events` | `LoggingEventSink(logger=None)` | `Sandbox(event_sink=...)`; see [15](../15-observability/README.md) |
| `CommandRunner` | `isolattice.commands` | `SubprocessRunner` | `PodmanBackend`, `DockerBackend`, `KubernetesBackend` (`runner=...`) |
| `Backend`, `BackendSession`, `Boundary` | `isolattice.backend` | The built-in backends | `Sandbox(backend=...)` |
| `TerminationDriver` | `isolattice.termination` | Used with `terminate_boundary()` | Custom backends |

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit Podman connection (rootless by default; `--allow-rootful` for an
  explicitly chosen rootful engine) and an already-local image containing `/bin/sh`.

## Run

```shell
uv run python examples/14-customization/podman_custom_extensions.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

```text
Store conformance: passed
Snapshot stored in SQLite: True
Restored workspace reads: kept in SQLite
Snapshot deleted: True
Engine clients started through the custom runner: <count>
```

## Acceptance

- `SQLiteArtifactStore` passes `isolattice.testing.check_artifact_store`: atomic
  publish that never overwrites, bounded fetch, `exists` and `delete`, and a structured
  `SandboxArtifactError` for every failure.
- A file written to `/workspace` is snapshotted into the SQLite store and read back from
  a new instance created with `workspace_snapshot=key`.
- Every Podman client the backend starts goes through `CountingRunner`, which delegates
  to `SubprocessRunner`.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs the script;
  [`test_extension_points.py`](https://github.com/smuniharish/isolattice/blob/main/tests/unit/test_extension_points.py)
  covers the extension contracts.

## Cleanup

Closing each instance removes its owned container and workspace volume. The snapshot
is deleted from the store, and the SQLite database lives in a temporary directory
removed when the script exits.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- Extensions run in the caller's process with its privileges; only use code you trust.
- Artifact stores never vouch for integrity: Isolattice re-verifies size and SHA-256
  after every fetch, and keys are checked with `validate_artifact_key`.
- A command runner sees engine arguments, which can contain environment values. The
  example counts executable names only; never log full arguments.
- An event sink that raises is logged and ignored, so it cannot stop termination or
  cleanup.

See [custom integrations](https://isolattice.readthedocs.io/en/latest/guides/custom-integrations/),
[custom backends](https://isolattice.readthedocs.io/en/latest/guides/custom-backend/) and the
[extension API](https://isolattice.readthedocs.io/en/latest/api/extension/).
