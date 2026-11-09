"""Record a sandbox's lifecycle events with a custom EventSink and export them as JSON Lines.

``emit`` runs on the event loop, so the sink only appends to a bounded queue; a
separate task writes the queue to disk. Events never carry argv, environment,
stdin or output, so the exported file holds no workload data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import override

from isolattice import (
    EventKind,
    EventSink,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxEvent,
    SandboxPolicy,
)


class JsonLinesEventSink(EventSink):
    """Queue events without blocking and export them from a background task."""

    def __init__(self, path: Path, *, capacity: int = 1024) -> None:
        self._path = path
        self._queue: asyncio.Queue[SandboxEvent | None] = asyncio.Queue(maxsize=capacity)
        self.dropped = 0

    @override
    def emit(self, event: SandboxEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Never stall containment or cleanup on telemetry; count what was lost.
            self.dropped += 1

    async def export(self) -> None:
        """Write queued events until ``finish`` is called."""
        with self._path.open("a", encoding="utf-8") as file:
            while (event := await self._queue.get()) is not None:
                record = {
                    "kind": event.kind.value,
                    "sandbox_id": event.sandbox_id,
                    "backend": event.backend,
                    "execution_id": event.execution_id,
                    "warning": event.is_warning,
                    "detail": dict(event.detail),
                    "timestamp": event.timestamp.isoformat(),
                }
                await asyncio.to_thread(file.write, json.dumps(record) + "\n")

    async def finish(self) -> None:
        """Ask the exporter to stop after the events queued so far."""
        await self._queue.put(None)


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    policy = SandboxPolicy()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.jsonl"
        sink = JsonLinesEventSink(path)
        exporter = asyncio.create_task(sink.export())
        try:
            async with (
                Sandbox(backend=backend, event_sink=sink) as sandbox,
                await sandbox.create(policy=policy) as instance,
            ):
                result = await instance.run(
                    ExecutionRequest(argv=("/bin/sh", "-c", "echo observed"))
                )
                if result.exit_code != 0:
                    raise RuntimeError("the observed execution failed")
        finally:
            await sink.finish()
            await exporter
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    kinds = [record["kind"] for record in records]
    for expected in (
        EventKind.SANDBOX_CREATED,
        EventKind.EXECUTION_STARTED,
        EventKind.EXECUTION_FINISHED,
        EventKind.SANDBOX_CLOSED,
    ):
        if expected.value not in kinds:
            raise RuntimeError(f"missing lifecycle event: {expected.value}")
    if sink.dropped:
        raise RuntimeError(f"{sink.dropped} events were dropped")
    for kind in kinds:
        print("Event:", kind)
    print("Exported events:", len(records))
    print("Workload output in events:", any("observed" in json.dumps(r) for r in records))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
