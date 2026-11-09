"""Tests for AccountingRunner. Run with pytest."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, override

import pytest
from runner_template import AccountingRunner

from isolattice import CommandRunner, SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence


class RecordingRunner(CommandRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    @override
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        self.calls.append((tuple(argv), stdin))
        return await SubprocessRunner().spawn(argv, stdin=stdin)


def test_argv_and_stdin_reach_the_inner_runner_unchanged() -> None:
    inner = RecordingRunner()
    runner = AccountingRunner(inner)
    argv = (sys.executable, "-c", "print('ok')")

    async def scenario() -> bytes:
        process = await runner.spawn(argv, stdin=False)
        stdout, _ = await process.communicate()
        return stdout

    assert asyncio.run(scenario()).strip() == b"ok"
    assert inner.calls == [(argv, False)]
    assert sum(runner.spawned.values()) == 1


def test_the_default_runner_gives_a_stdin_pipe_when_asked() -> None:
    runner = AccountingRunner()
    argv = (sys.executable, "-c", "import sys; print(sys.stdin.read())")

    async def scenario() -> bytes:
        process = await runner.spawn(argv, stdin=True)
        stdout, _ = await process.communicate(b"hello")
        return stdout

    assert asyncio.run(scenario()).strip() == b"hello"


def test_a_non_runner_is_refused() -> None:
    with pytest.raises(TypeError, match="CommandRunner"):
        AccountingRunner(object())  # pyrefly: ignore[bad-argument-type]
