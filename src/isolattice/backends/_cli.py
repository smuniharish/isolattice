"""Bounded, cancellation-safe invocation of trusted engine command-line clients."""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .._async import discard_remaining, raise_combined, raise_with_cleanup, settle
from ..errors import SandboxBackendError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ..commands import CommandRunner

MAX_CONTROL_OUTPUT = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandError(SandboxBackendError):
    """A client failure; ``possibly_accepted`` means the engine may have acted."""

    def __init__(self, message: str, *, possibly_accepted: bool) -> None:
        super().__init__(message)
        self.possibly_accepted = possibly_accepted


async def _read_limited(reader: asyncio.StreamReader, product: str) -> bytes:
    data = bytearray()
    while chunk := await reader.read(65536):
        data.extend(chunk)
        if len(data) > MAX_CONTROL_OUTPUT:
            raise SandboxBackendError(f"{product} control output exceeded its 1 MiB limit")
    return bytes(data)


async def reap_client(process: asyncio.subprocess.Process, product: str) -> None:
    """Kill and reap a local client process, draining its pipes within a bound."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        async with asyncio.timeout(5):
            results = await asyncio.gather(
                process.wait(),
                *(
                    discard_remaining(reader)
                    for reader in (process.stdout, process.stderr)
                    if reader is not None
                ),
                return_exceptions=True,
            )
    except TimeoutError as error:
        raise SandboxBackendError(
            f"{product} client process or its output pipes could not be reaped"
        ) from error
    raise_combined(
        f"{product} client process and output cleanup failed",
        *(result for result in results if isinstance(result, BaseException)),
    )


async def run_command(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    product: str,
    operation: str,
    timeout: float,  # noqa: ASYNC109 - also bounds killing the client
    accepted: tuple[int, ...] = (0,),
    stdin: bytes | None = None,
) -> CommandResult:
    """Run one client command with bounded output; output is withheld from errors."""
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        async with asyncio.timeout_at(deadline):
            process = await runner.spawn(argv, stdin=stdin is not None)
    except TimeoutError as spawn_error:
        raise CommandError(
            f"{product} client startup for {operation} exceeded its deadline; "
            "engine outcome may be unknown",
            possibly_accepted=True,
        ) from spawn_error
    except OSError as spawn_error:
        raise CommandError(
            f"could not start {product} for {operation}: {type(spawn_error).__name__}",
            possibly_accepted=False,
        ) from spawn_error
    if process.stdout is None or process.stderr is None:
        await reap_client(process, product)
        raise CommandError(
            f"{product} client for {operation} was started without output pipes",
            possibly_accepted=True,
        )

    async def feed() -> None:
        if stdin is None:
            return
        assert process.stdin is not None
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()
        await process.stdin.wait_closed()

    tasks = (
        asyncio.create_task(_read_limited(process.stdout, product)),
        asyncio.create_task(_read_limited(process.stderr, product)),
        asyncio.create_task(process.wait()),
        asyncio.create_task(feed()),
    )
    error: BaseException | None = None
    result: tuple[bytes, bytes, int] | None = None
    try:
        async with asyncio.timeout_at(deadline):
            stdout, stderr, returncode, _ = await asyncio.gather(*tasks)
            result = (stdout, stderr, returncode)
    except TimeoutError as cause:
        error = CommandError(
            f"{product} {operation} exceeded its deadline; engine outcome may be unknown",
            possibly_accepted=True,
        )
        error.__cause__ = cause
    except BaseException as cause:
        error = cause
    if error is not None:
        for task in tasks:
            task.cancel()

        async def cleanup_client() -> None:
            await asyncio.gather(*tasks, return_exceptions=True)
            await reap_client(process, product)

        cleanup = await settle(asyncio.create_task(cleanup_client()))
        raise_with_cleanup(
            f"{product} client failure and cleanup failure",
            error,
            cleanup.error,
            cleanup.interrupted,
        )
    assert result is not None
    stdout, stderr, returncode = result
    if returncode not in accepted:
        raise CommandError(
            f"{product} {operation} failed with exit code {returncode}; "
            "backend output is withheld because it may contain workload data",
            possibly_accepted=True,
        )
    return CommandResult(stdout, stderr, returncode)


def json_object(data: bytes, product: str, operation: str) -> dict[str, object]:
    try:
        parsed: object = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxBackendError(f"{product} {operation} returned invalid JSON") from error
    return mapping(parsed, product, f"{operation} result")


def mapping(value: object, product: str, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SandboxBackendError(f"{product} {context} has an invalid shape")
    return value


def string_list(value: object) -> list[str] | None:
    """Return ``value`` as a list of strings, treating ``None`` as empty."""
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None
