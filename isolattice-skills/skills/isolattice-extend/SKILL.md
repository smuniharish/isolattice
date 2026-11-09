---
name: isolattice-extend
description: >-
  Use this skill when extending the isolattice Python package: writing a
  custom ArtifactStore for snapshots and checkpoints (a database, object
  store or internal service), an EventSink that exports lifecycle events to
  logging, metrics or tracing, a CommandRunner that changes how engine clients
  (podman, docker, kubectl) are started, or a Backend, BackendSession and
  TerminationDriver for a new isolation technology, and when re-exposing these
  extension points from a downstream package. Do not use it to run workloads,
  write a SandboxPolicy or pick a built-in backend; the isolattice skill covers
  those. Do not use it for general abstract-base-class or plugin design that
  does not involve isolattice.
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer with isolattice 0.1 installed. The bundled script and
  templates run offline with pytest; a custom Backend needs its own host.
metadata:
  version: "0.1.0"
  documentation: "https://isolattice.readthedocs.io/en/latest/"
---

# Extending isolattice

Every isolattice extension point is an abstract base class. Subclass it,
implement every abstract method and pass the instance to `Sandbox` or to a
backend constructor. Isolattice never discovers plugins on its own and never
loads code by name.

| Extension point | Subclass | Pass it to |
| --- | --- | --- |
| Artifact storage | `isolattice.ArtifactStore` | `Sandbox(artifact_store=...)` |
| Lifecycle events | `isolattice.EventSink` | `Sandbox(event_sink=...)` |
| Engine client processes | `isolattice.CommandRunner` | `PodmanBackend`, `DockerBackend` or `KubernetesBackend` (`runner=...`) |
| Isolation technology | `isolattice.Backend`, `BackendSession`, `TerminationDriver` | `Sandbox(backend=...)` |

## Workflow

1. Identify the extension point from the table. Read its reference page before
   writing code: [references/store.md](references/store.md),
   [references/events-and-runners.md](references/events-and-runners.md) or
   [references/backend.md](references/backend.md).
2. Copy the matching template into the project: `assets/store_template.py`,
   `assets/event_sink_template.py` or `assets/runner_template.py`.
3. Adapt it. Keep every promise the reference lists; never remove a check
   to make a test pass.
4. Copy the matching tests (`assets/test_store_contract.py`,
   `assets/test_event_sink.py` or `assets/test_runner.py`) and run them.
5. For a store, also run
   `python scripts/check_store.py package.module:factory --path src`, where
   `factory` takes no arguments (the template's `from_environment`). It
   calls `isolattice.testing.check_artifact_store` on a fresh instance and
   names the broken promise.
6. Wire the instance in through the constructor argument from the table, then
   run the application's own tests.

## Rules

- Subclass the ABC explicitly. Isolattice checks `isinstance` and refuses
  duck-typed objects.
- Report failures with `SandboxArtifactError` (stores) or
  `SandboxBackendError` (backends). Never return a partial result.
- A store keeps bytes; it never vouches for integrity. The runtime re-checks
  size and SHA-256 after every fetch, but a store still enforces `max_bytes`
  itself so an oversized object is never copied.
- `EventSink.emit` runs synchronously on the event loop: no blocking I/O.
  Buffer in memory with a bound and export asynchronously.
- A `CommandRunner` passes argv unchanged. It may change how a process is
  started (environment, working directory, logging, accounting) but never
  which engine flags run, because those flags enforce the policy.
- A custom `Backend` reports only capabilities it enforces and has verified
  on the host. `UNKNOWN` never counts as terminated.
- Never log argv, environment, stdin, output or credentials.

## Downstream packages

A package built on isolattice re-exposes every extension point instead of
hard-coding defaults: accept `backend`, `artifact_store`, `event_sink` and,
where it builds engine backends, `runner`, then pass them through unchanged.
Default to `LocalArtifactStore`, `LoggingEventSink` and `SubprocessRunner`
only when the caller passes nothing.

## Files

- `scripts/check_store.py`: loads `module:factory` (`--path` adds an import
  directory), runs the store conformance check and prints PASS or FAIL.
  `--json` prints one object. Exit codes are 0 (conforms), 1 (a promise is
  broken) and 2 (the factory cannot be loaded or built).
- `assets/store_template.py`: `SQLiteArtifactStore`, a complete store on the
  standard-library `sqlite3` module, and its `from_environment` factory.
- `assets/test_store_contract.py`: contract tests for the store template.
- `assets/event_sink_template.py`: `BufferedEventSink`, bounded in memory,
  exported by an async task.
- `assets/test_event_sink.py`: tests for the sink template.
- `assets/runner_template.py`: `AccountingRunner`, which wraps another runner
  and counts client processes per executable.
- `assets/test_runner.py`: tests for the runner template.
- [references/store.md](references/store.md): the ArtifactStore contract.
- [references/events-and-runners.md](references/events-and-runners.md):
  EventSink and CommandRunner contracts.
- [references/backend.md](references/backend.md): Backend, BackendSession and
  TerminationDriver contracts and the evidence a new backend needs.
