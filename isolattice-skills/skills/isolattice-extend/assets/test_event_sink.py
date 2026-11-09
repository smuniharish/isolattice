"""Tests for BufferedEventSink. Run with pytest."""

from __future__ import annotations

import asyncio
import contextlib

from event_sink_template import BufferedEventSink, Record

from isolattice import EventKind, SandboxEvent


def event(kind: EventKind = EventKind.EXECUTION_STARTED) -> SandboxEvent:
    return SandboxEvent(kind=kind, sandbox_id="s1", backend="test", detail={"exit_code": 0})


def test_emit_never_blocks_and_counts_dropped_events() -> None:
    async def scenario() -> int:
        sink = BufferedEventSink(capacity=2)
        for _ in range(5):
            sink.emit(event())
        return sink.dropped

    assert asyncio.run(scenario()) == 3


def test_export_sends_batches_and_flushes_on_cancellation() -> None:
    batches: list[list[Record]] = []

    async def scenario() -> None:
        three_written = asyncio.Event()

        async def write(records: list[Record]) -> None:
            batches.append(records)
            if sum(len(batch) for batch in batches) >= 3:
                three_written.set()

        sink = BufferedEventSink()
        exporter = asyncio.create_task(sink.export(write, batch=2))
        for _ in range(3):
            sink.emit(event())
        await asyncio.wait_for(three_written.wait(), timeout=5)
        sink.emit(event(EventKind.TERMINATION_UNKNOWN))
        exporter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await exporter

    asyncio.run(scenario())

    assert sum(len(batch) for batch in batches) == 4
    assert batches[-1][-1]["kind"] == "termination.unknown"
    assert batches[-1][-1]["warning"] is True


def test_records_are_flat_and_carry_the_detail() -> None:
    records: list[Record] = []

    async def write(batch: list[Record]) -> None:
        records.extend(batch)

    async def scenario() -> None:
        sink = BufferedEventSink()
        sink.emit(event())
        await sink.flush(write)

    asyncio.run(scenario())

    (record,) = records
    assert record["detail.exit_code"] == 0
    assert record["sandbox_id"] == "s1"
