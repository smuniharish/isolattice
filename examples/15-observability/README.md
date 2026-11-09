---
category: 15
status: runnable
---

# 15 Observability

Structured lifecycle events delivered to an `EventSink`. OpenTelemetry and metrics
exporters are not built in; forward events to them from your own sink.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_event_sink.py`](https://github.com/smuniharish/isolattice/blob/main/examples/15-observability/podman_event_sink.py) | A custom `EventSink` that queues events without blocking and exports them as JSON Lines from a background task | Rootless Podman |

What the API provides:

- `Sandbox(event_sink=...)` delivers `SandboxEvent` values to an `EventSink`. The
  default is `LoggingEventSink(logger=None)`, which writes to standard `logging`.
- `EventKind` covers `SANDBOX_CREATED`, `SANDBOX_CLOSED`, `SANDBOX_CLEANUP_FAILED`,
  `EXECUTION_STARTED`, `EXECUTION_FINISHED`, `EXECUTION_FAILED`,
  `TERMINATION_UNKNOWN`, `LATE_CLEANUP_FINISHED`, `LATE_CLEANUP_FAILED`,
  `ARTIFACT_PUBLISHED` and `ARTIFACT_RESTORED`.
- Every `ExecutionResult` carries a `TerminationReport`, and
  `SandboxInstance.pending_late_cleanups` exposes cleanup still in progress.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit Podman connection (rootless by default; `--allow-rootful` for an
  explicitly chosen rootful engine) and an already-local image containing `/bin/sh`.

## Run

```shell
uv run python examples/15-observability/podman_event_sink.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

```text
Event: sandbox.created
Event: execution.started
Event: execution.finished
Event: sandbox.closed
Exported events: 4
Workload output in events: False
```

## Acceptance

- One execution produces `sandbox.created`, `execution.started`,
  `execution.finished` and `sandbox.closed`, in order.
- The exported records contain none of the workload's output.
- No event is dropped by the bounded queue.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs the script.

## Cleanup

Closing the instance removes its owned container. The exporter task is stopped after
the sandbox closes, and the JSON Lines file lives in a temporary directory removed when
the script exits.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- Events carry lifecycle metadata only: no argv, environment values, input or output.
- `emit` is synchronous and runs on the event loop, so keep it fast: queue the event
  and export it elsewhere, as this example does. A sink that raises is logged and
  ignored, so it cannot stop termination or cleanup.
- Events are delivered in process and are not a durable audit log.

See [observability](https://isolattice.readthedocs.io/en/latest/operations/observability/),
the [event reference](https://isolattice.readthedocs.io/en/latest/reference/events/) and
the [events API](https://isolattice.readthedocs.io/en/latest/api/events/).
