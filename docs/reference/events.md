# Events reference

The runtime reports lifecycle transitions as `SandboxEvent` objects to the
`EventSink` passed to `Sandbox(event_sink=...)`, by default a `LoggingEventSink` that
writes to the `isolattice` logger. See the [events API](../api/events.md) and
[observability](../operations/observability.md).

## SandboxEvent

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | `EventKind` | What happened |
| `sandbox_id` | `str` | The instance the event belongs to |
| `backend` | `str` | The session's `backend_name`, for example `podman` |
| `execution_id` | `str` or `None` | The execution, when the event concerns one |
| `detail` | read-only mapping | Small scalar facts listed below |
| `timestamp` | `datetime` | UTC time the event was created |
| `is_warning` | `bool` property | `True` for unverified or delayed cleanup events |

Events never carry argv, environment, stdin or output. Artifact keys do appear in
`detail`, so keep secrets out of keys.

## Event kinds

| Kind | Value | Emitted when | `execution_id` | `detail` | Warning |
| --- | --- | --- | --- | --- | --- |
| `SANDBOX_CREATED` | `sandbox.created` | `Sandbox.create()` returns a ready instance, after any snapshot import | no | - | no |
| `SANDBOX_CLOSED` | `sandbox.closed` | An instance stopped and every cleanup succeeded | no | - | no |
| `SANDBOX_CLEANUP_FAILED` | `sandbox.cleanup-failed` | Stopping an instance found cleanup failures; `close()` raises a `BaseExceptionGroup` | no | `failures`: count | yes |
| `EXECUTION_STARTED` | `execution.started` | `start()`, `run()` or `restore()` admitted an execution | yes | `restored=True` for `restore()` | no |
| `EXECUTION_FINISHED` | `execution.finished` | An execution completed and its boundary was released | yes | `exit_code` | no |
| `EXECUTION_FAILED` | `execution.failed` | An execution raised, timed out or was cancelled | yes | `error`: exception class name, such as `SandboxTimeoutError` or `CancelledError` | no |
| `TERMINATION_UNKNOWN` | `termination.unknown` | `cleanup_deadline` expired; the caller gets `TerminationVerificationError` and a late cleanup continues | yes | `phase` | yes |
| `LATE_CLEANUP_FINISHED` | `late-cleanup.finished` | A late cleanup ended | no | `released`: whether the boundary was verified released | yes |
| `LATE_CLEANUP_FAILED` | `late-cleanup.failed` | A late cleanup raised or was cancelled | no | `error`: exception class name | yes |
| `ARTIFACT_PUBLISHED` | `artifact.published` | A snapshot or checkpoint was stored | for checkpoints and container snapshots | `key`, `artifact_kind`, `size` | no |
| `ARTIFACT_RESTORED` | `artifact.restored` | A fetched artifact passed verification, before it is imported | no | `key`, `artifact_kind`, `size` | no |

`artifact_kind` is an `ArtifactKind` value: `workspace-snapshot`,
`execution-checkpoint` or `container-snapshot`.

## Delivery rules

- `emit()` is called synchronously on the event loop and must not block. Buffer
  events and export them from another task; see
  [custom event sinks](../guides/custom-integrations.md#custom-event-sink).
- If `emit()` raises, the runtime logs the error and continues; telemetry never
  interrupts containment or cleanup.
- `LoggingEventSink` logs warning events at `WARNING` and the rest at `INFO`, with the
  event fields as structured `extra` data.

## Alerting suggestions

| Event | Suggested action |
| --- | --- |
| `sandbox.cleanup-failed` | Investigate; resources of that instance may remain |
| `termination.unknown` | Expect a late-cleanup event; page if none arrives |
| `late-cleanup.finished` with `released=False` | Reconcile the boundary by its labels or ID |
| `late-cleanup.failed` | Reconcile manually |
