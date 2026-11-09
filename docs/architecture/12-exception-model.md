# 12. Exception model

This page is the reference for every exception Isolattice raises. The generated API
is in [errors](../api/errors.md).

## Hierarchy

```text
Exception
├── SandboxError
│   ├── SandboxCapabilityError
│   ├── SandboxStateError
│   ├── SandboxBackendError
│   ├── SandboxExecutionError
│   │   └── SandboxTimeoutError        (also a TimeoutError; .report)
│   ├── SandboxTerminationError        (.report)
│   │   └── TerminationVerificationError
│   └── SandboxArtifactError
└── AssertionError
    └── isolattice.testing.ConformanceError
```

Invalid arguments and model values raise `TypeError` or `ValueError` before any
backend work.

## When each error is raised

| Exception | Raised when |
| --- | --- |
| `SandboxCapabilityError` | A setting or required capability cannot be enforced, an optional operation is unsupported, or an artifact operation has no store |
| `SandboxStateError` | An operation does not fit the owner's state: a closed or stopping owner, the concurrency limit reached, a second `output()` consumer, or a snapshot with active executions |
| `SandboxBackendError` | The selected backend, engine, cluster or OS call failed, timed out or returned unexpected data |
| `SandboxExecutionError` | A workload could not be started, its isolation could not be verified before exec, or its I/O could not be delivered |
| `SandboxTimeoutError` | An execution exceeded its deadline; `report` is the cleanup report when available |
| `SandboxTerminationError` | A boundary could not be terminated and released; `report` describes what was observed |
| `TerminationVerificationError` | Emptiness or release could not be verified within the termination policy or `cleanup_deadline`; the report state is `non_empty` or `unknown` |
| `SandboxArtifactError` | An artifact is missing, already exists, exceeds a limit, has an unsafe archive member, or fails key, kind, backend, format, size or digest checks |
| `ConformanceError` | `check_artifact_store()` found a store that violates the `ArtifactStore` contract |

A workload's nonzero exit code is an `ExecutionResult`, not an exception.

## Composition

Cleanup never hides the original failure, and a cleanup failure is never swallowed:

- If an operation and its cleanup both fail, the caller receives a
  `BaseExceptionGroup` containing the original error and every cleanup error. With a
  single failure, that exception is raised directly.
- `SandboxInstance.stop()`, `kill()` and `close()` group the failures of every
  execution and the session close; a `SANDBOX_CLEANUP_FAILED` event is emitted.
- A cancelled caller waits for owned cleanup; the cancellation is raised together
  with any cleanup failure.
- Backend errors are chained with `raise ... from` so the platform cause stays
  available. Messages and notes never include argv, environment values, stdin or
  output.

Use `except*` to handle grouped failures:

```python
try:
    await instance.close()
except* TerminationVerificationError as group:
    for error in group.exceptions:
        report_unverified(error.report)
```

## Not raised

Exceptions from an `EventSink` are logged and suppressed so that observability can
never change lifecycle outcomes.
