"""Helpers for owned tasks whose cleanup must survive repeated cancellation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import NoReturn


@dataclass(slots=True)
class Settled[T]:
    value: T | None
    error: BaseException | None
    interrupted: asyncio.CancelledError | None


async def discard_remaining(reader: asyncio.StreamReader) -> bool:
    discarded = False
    while await reader.read(65536):
        discarded = True
        await asyncio.sleep(0)
    return discarded


async def settle[T](task: asyncio.Task[T]) -> Settled[T]:
    interrupted: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if not task.done() and interrupted is None:
                interrupted = error
        except BaseException:
            break
    try:
        return Settled(task.result(), None, interrupted)
    except BaseException as error:
        return Settled(None, error, interrupted)


async def wait_until(task: asyncio.Task[object], deadline: float) -> asyncio.CancelledError | None:
    """Wait for ``task`` until a loop-time deadline without cancelling it.

    Caller cancellation is absorbed and returned so bounded cleanup still completes.
    """
    interrupted: asyncio.CancelledError | None = None
    loop = asyncio.get_running_loop()
    while not task.done():
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            await asyncio.wait((task,), timeout=remaining)
        except asyncio.CancelledError as error:
            if interrupted is None:
                interrupted = error
    return interrupted


def raise_combined(message: str, *errors: BaseException | None) -> None:
    present = [error for error in errors if error is not None]
    if len(present) == 1:
        raise present[0]
    if present:
        raise BaseExceptionGroup(message, present)


def raise_with_cleanup(
    message: str, failure: BaseException, *cleanup: BaseException | None
) -> NoReturn:
    """Raise ``failure``, grouped with any cleanup errors that also occurred."""
    present = [error for error in cleanup if error is not None]
    if present:
        raise BaseExceptionGroup(message, [failure, *present])
    raise failure
