"""An EventSink that buffers events in memory and exports them from a task.

``emit`` runs on the event loop, so it only appends to a bounded queue.
``export`` drains the queue asynchronously into any async writer: an HTTP
client, a message-queue producer or a file written off the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import override

from isolattice import EventSink, SandboxEvent

type Record = dict[str, str | int | float | bool | None]
type Writer = Callable[[list[Record]], Awaitable[None]]


def to_record(event: SandboxEvent) -> Record:
    """A flat, JSON-ready record. Events carry no workload data."""
    record: Record = {
        "kind": event.kind.value,
        "sandbox_id": event.sandbox_id,
        "backend": event.backend,
        "execution_id": event.execution_id,
        "timestamp": event.timestamp.isoformat(),
        "warning": event.is_warning,
    }
    record.update({f"detail.{name}": value for name, value in event.detail.items()})
    return record


class BufferedEventSink(EventSink):
    """Keep at most ``capacity`` events and count the ones dropped when full."""

    def __init__(self, capacity: int = 10_000) -> None:
        """Bound the buffer to ``capacity`` events."""
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._queue: asyncio.Queue[Record] = asyncio.Queue(capacity)
        self.dropped = 0

    @override
    def emit(self, event: SandboxEvent) -> None:
        try:
            self._queue.put_nowait(to_record(event))
        except asyncio.QueueFull:
            self.dropped += 1

    async def export(self, write: Writer, *, batch: int = 100) -> None:
        """Send events to ``write`` in batches until cancelled, then flush the rest."""
        try:
            while True:
                records = [await self._queue.get()]
                while len(records) < batch and not self._queue.empty():
                    records.append(self._queue.get_nowait())
                await write(records)
        except asyncio.CancelledError:
            await self.flush(write)
            raise

    async def flush(self, write: Writer) -> None:
        """Send every buffered event to ``write`` now."""
        records: list[Record] = []
        while not self._queue.empty():
            records.append(self._queue.get_nowait())
        if records:
            await write(records)
