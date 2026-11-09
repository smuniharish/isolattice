# EventSink and CommandRunner contracts

## EventSink

```python
class EventSink(ABC):
    @abstractmethod
    def emit(self, event: SandboxEvent) -> None: ...
```

`Sandbox(event_sink=...)` receives one `SandboxEvent` per lifecycle
transition. The default is `LoggingEventSink`, which logs to the
`isolattice` logger at WARNING for cleanup concerns and INFO otherwise.

`SandboxEvent` fields: `kind` (`EventKind`), `sandbox_id`, `backend`,
`execution_id`, `detail` (a read-only mapping of scalars) and `timestamp`
(UTC). `event.is_warning` is true for `sandbox.cleanup-failed`,
`termination.unknown`, `late-cleanup.finished` and `late-cleanup.failed`.

| `EventKind` value | Meaning |
| --- | --- |
| `sandbox.created`, `sandbox.closed` | An instance was prepared or closed. |
| `sandbox.cleanup-failed` | Closing an instance did not verify cleanup. |
| `execution.started`, `execution.finished`, `execution.failed` | One execution's lifecycle. |
| `termination.unknown` | Termination could not be verified; processes may remain. |
| `late-cleanup.finished`, `late-cleanup.failed` | Cleanup that outlived its deadline ended. |
| `artifact.published`, `artifact.restored` | A snapshot or checkpoint was stored or restored. |

Promises:

1. `emit` is synchronous and runs on the event loop. It must not block: no
   network calls and no file writes that can stall. Append to a bounded buffer
   and export from an async task.
2. When the buffer is full, drop and count rather than grow without bound.
3. The runtime logs and suppresses exceptions from `emit`, so telemetry never
   interrupts containment or cleanup. Handle your own errors anyway.
4. Events never carry argv, environment, stdin or output. Keep it that way
   when adding fields from your own context.
5. Alert an operator on `termination.unknown` and `sandbox.cleanup-failed`:
   they mean workload processes may still run.

## CommandRunner

```python
class CommandRunner(ABC):
    @abstractmethod
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process: ...
```

`PodmanBackend`, `DockerBackend` and `KubernetesBackend` start every engine
client (`podman`, `docker`, `kubectl`) through their `runner`. The default
`SubprocessRunner` starts a direct child with stdout and stderr pipes, and a
stdin pipe when `stdin` is true.

Promises:

1. Return a started `asyncio.subprocess.Process` with stdout and stderr
   pipes, and a stdin pipe exactly when `stdin` is true. The backend reads,
   bounds and waits for the process itself.
2. Run `argv` unchanged. The engine flags enforce the policy (network,
   read-only root, limits, labels); changing them can silently weaken a
   required guarantee.
3. Wrapping is fine: change the environment, working directory, priority or
   accounting, or delegate to another runner.
4. Never log full argv: it can contain workload environment values. Log the
   executable name at most.
