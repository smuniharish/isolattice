# Events

The runtime reports lifecycle transitions to one `EventSink`. The default is
`LoggingEventSink`; subclass `EventSink` to forward events to metrics, tracing
or an audit log. Event payloads never contain workload output, environment
values or credentials.

::: isolattice.SandboxEvent

::: isolattice.EventKind

::: isolattice.EventSink

::: isolattice.LoggingEventSink
