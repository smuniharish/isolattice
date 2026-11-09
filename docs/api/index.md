# API reference

Everything below is importable from the top-level `isolattice` package. The
reference is generated from the source docstrings, so it always matches the
installed version.

| Page | What it covers |
| --- | --- |
| [Sandbox](sandbox.md) | `Sandbox`, `SandboxInstance` and `ExecutionHandle`: the async lifecycle and execution API. |
| [Policies and models](models.md) | `SandboxPolicy`, `ExecutionRequest`, results, capabilities, budgets and artifact manifests. |
| [Backends](backends.md) | The built-in backends and their constructor options. |
| [Backend extension](extension.md) | `Backend`, `BackendSession`, `Boundary`, `TerminationDriver` and `CommandRunner` for custom backends. |
| [Artifact storage](storage.md) | `ArtifactStore`, `LocalArtifactStore` and `S3ArtifactStore` for snapshots and checkpoints. |
| [Events](events.md) | `SandboxEvent`, `EventKind` and pluggable `EventSink` implementations. |
| [Errors](errors.md) | The structured exception hierarchy. |
| [Conformance testing](testing.md) | Checks that a custom extension keeps its contract. |

Every extension point is an abstract base class. Subclass it, implement the
abstract methods and pass the instance to `Sandbox` or to the backend that uses
it; Isolattice never discovers implementations implicitly.

!!! warning "Shared kernel"
    No built-in backend defends against kernel exploits or is certified for
    hostile multi-tenant workloads. Each
    backend reports the capabilities it verified on the current host; read
    them with `Sandbox.discover()` before relying on one.
