"""Example presentation logic; live execution is tested separately."""

from __future__ import annotations

import asyncio
import runpy
from pathlib import Path
from types import FunctionType
from typing import TYPE_CHECKING

import pytest

from isolattice import ExecutionRequest, OutputChunk, TerminationReport

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.mark.parametrize(
    ("chunks", "ready"),
    [
        (
            [
                OutputChunk("stdout", b"R"),
                OutputChunk("stdout", b"EA"),
                OutputChunk("stdout", b"DY\n"),
            ],
            True,
        ),
        ([OutputChunk("stderr", b"READY\n"), OutputChunk("stdout", b"ENDED\n")], False),
        ([], False),
    ],
)
def test_stream_example_handles_fragmented_readiness_and_eof(
    chunks: list[OutputChunk], ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Handle:
        cancelled = False
        stream_closed = False

        async def output(self) -> AsyncGenerator[OutputChunk, None]:
            try:
                for chunk in chunks:
                    yield chunk
            finally:
                self.stream_closed = True

        async def cancel(self, *, force: bool) -> TerminationReport:
            assert force
            self.cancelled = True
            return TerminationReport("test", 1, 2, "terminated", True)

    handle = Handle()

    class Instance:
        async def __aenter__(self) -> Instance:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def start(self, request: ExecutionRequest) -> Handle:
            return handle

    class Owner:
        async def __aenter__(self) -> Owner:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def create(self, **kwargs: object) -> Instance:
            return Instance()

    def owner(**kwargs: object) -> Owner:
        return Owner()

    script = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "06-async-and-cancellation"
        / "podman_stream_cancel.py"
    )
    namespace = runpy.run_path(str(script))
    main = namespace["main"]
    assert isinstance(main, FunctionType)
    monkeypatch.setitem(main.__globals__, "Sandbox", owner)
    if ready:
        asyncio.run(main("uncontacted", "uncontacted"))
    else:
        with pytest.raises(RuntimeError, match="readiness"):
            asyncio.run(main("uncontacted", "uncontacted"))
    assert handle.cancelled is ready
    assert handle.stream_closed


def test_every_example_belongs_to_a_live_tested_backend() -> None:
    root = Path(__file__).resolve().parents[2] / "examples"
    examples = sorted(root.rglob("*.py"))
    assert examples
    for example in examples:
        assert example.name.startswith(
            ("podman_", "windows_", "linux_", "docker_", "kubernetes_")
        ), example
