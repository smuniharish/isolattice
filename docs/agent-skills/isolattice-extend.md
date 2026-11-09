# isolattice-extend skill

The `isolattice-extend` skill teaches a coding agent to extend Isolattice
through its abstract base classes, and to re-expose those extension points
from a downstream package.

Source:
[`isolattice-skills/skills/isolattice-extend`](https://github.com/smuniharish/isolattice/tree/main/isolattice-skills/skills/isolattice-extend)

## When it loads

The agent loads it for tasks such as:

- an `ArtifactStore` that keeps snapshots and checkpoints in a database,
  object store or internal service;
- an `EventSink` that exports lifecycle events to logging, metrics or tracing;
- a `CommandRunner` that changes how `podman`, `docker` or `kubectl` are
  started, for example to audit or trace every call;
- a `Backend`, `BackendSession` and `TerminationDriver` for a new isolation
  technology;
- a wrapper package that lets its own users bring their store, sink, runner
  and backend.

It doesn't load for running workloads, writing a policy or choosing a
built-in backend; that is the [isolattice](isolattice.md) skill.

## Workflow

1. Read the reference for the extension point before writing code.
2. Copy the matching template into the project.
3. Adapt it, keeping every contract rule in the reference.
4. Copy the matching tests and run them.
5. For a store, run `scripts/check_store.py` against the project's factory.

## Rules it enforces

- Extension points are ABCs. Subclass them explicitly; Isolattice rejects
  objects that only look similar.
- A store never overwrites an existing key and never vouches for integrity.
  Isolattice computes the manifest and verifies size and SHA-256 after every
  fetch.
- An event sink never blocks the event loop and never receives workload
  output, environment values or credentials.
- A runner may observe or wrap engine calls, but never removes arguments that
  enforce a policy, such as `--read-only` or `--network=none`.
- A backend reports only capabilities it enforces, with evidence, and its
  termination driver reports an unknown state instead of guessing.
- A downstream package passes extension points through as typed parameters,
  never as raw engine flags.

## Bundled files

| File | Purpose |
| --- | --- |
| `scripts/check_store.py` | Loads `module:factory` and runs `isolattice.testing.check_artifact_store`. `--path` adds an import path; `--json` prints one JSON object. Exits 0 on success, 1 on a broken contract and 2 when the factory can't be loaded. |
| `assets/store_template.py` | `SQLiteArtifactStore`, a complete store with a `from_environment()` factory. |
| `assets/test_store_contract.py` | Contract tests for the store template. |
| `assets/event_sink_template.py` | `BufferedEventSink`: a bounded queue that counts dropped events. |
| `assets/test_event_sink.py` | Tests for the sink template. |
| `assets/runner_template.py` | `AccountingRunner`, which wraps another runner and counts calls. |
| `assets/test_runner.py` | Tests for the runner template. |
| `references/store.md` | The `ArtifactStore` contract. |
| `references/events-and-runners.md` | `EventSink` and `CommandRunner`. |
| `references/backend.md` | `Backend`, `BackendSession`, `Boundary` and `TerminationDriver`. |

See [Install in your agent](install.md) to install it and
[Evaluation](evaluation.md) for how it is tested.
