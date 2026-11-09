# Observability

Isolattice reports lifecycle events through an `EventSink`. Pass one to
`Sandbox`:

```python
import logging

from isolattice import LoggingEventSink, Sandbox

logging.basicConfig(level=logging.INFO)
sandbox = Sandbox(backend=backend, event_sink=LoggingEventSink())
```

`LoggingEventSink` writes to the `isolattice` logger, or a logger you pass. It
logs cleanup concerns at `WARNING` and everything else at `INFO`. The event
object is attached as `record.isolattice_event` for structured handlers.

## Events

| Event | When |
| --- | --- |
| `SANDBOX_CREATED`, `SANDBOX_CLOSED` | An instance is created or closed. |
| `SANDBOX_CLEANUP_FAILED` | Closing an instance failed. |
| `EXECUTION_STARTED`, `EXECUTION_FINISHED`, `EXECUTION_FAILED` | An execution starts, finishes or fails. |
| `TERMINATION_UNKNOWN` | Termination could not be verified within the deadline. |
| `LATE_CLEANUP_FINISHED`, `LATE_CLEANUP_FAILED` | Cleanup that continued after its deadline ended. |
| `ARTIFACT_PUBLISHED`, `ARTIFACT_RESTORED` | A snapshot or checkpoint was stored or restored. |

Each `SandboxEvent` has `kind`, `sandbox_id`, `backend`, `execution_id`,
`timestamp` and a small `detail` mapping of scalar values.
`event.is_warning` is true for the cleanup concerns. Events never contain argv,
environment values, stdin or output, so they are safe to forward to shared
telemetry.

## Your own sink

Subclass `EventSink` to export events to metrics, tracing or a queue:

```python
from isolattice import EventSink, SandboxEvent


class CountingSink(EventSink):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def emit(self, event: SandboxEvent) -> None:
        self.counts[event.kind.value] = self.counts.get(event.kind.value, 0) + 1
```

`emit` is called synchronously on the event loop. It must not block: buffer
events and export them from a separate task. If a sink raises, Isolattice logs
and suppresses the exception, so telemetry never interrupts cleanup.

OpenTelemetry and metrics exporters are proposed but not built in; an
`EventSink` is the integration point. See the
[observability architecture](../architecture/18-observability.md).
