"""Engine-client helper contracts: spawn failures, output parsing and stdin feeding.

These tests prove the shared CLI plumbing fails closed and withholds output; they
do not prove any engine's isolation.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, override

import pytest

from isolattice import CommandRunner, SandboxBackendError, SubprocessRunner
from isolattice.backends import _cli

if TYPE_CHECKING:
    from collections.abc import Sequence


class MissingExecutableRunner(CommandRunner):
    @override
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        raise FileNotFoundError(argv[0])


def test_spawn_failure_is_not_possibly_accepted() -> None:
    async def check() -> None:
        with pytest.raises(_cli.CommandError, match="could not start Engine for inspect") as raised:
            await _cli.run_command(
                MissingExecutableRunner(),
                ("engine", "inspect"),
                product="Engine",
                operation="inspect",
                timeout=5,
            )
        assert raised.value.possibly_accepted is False
        assert isinstance(raised.value.__cause__, FileNotFoundError)

    asyncio.run(check())


def test_stdin_is_fed_and_rejected_exit_codes_withhold_output() -> None:
    echo = "import sys; data = sys.stdin.buffer.read(); sys.stdout.buffer.write(data[::-1])"

    async def check() -> None:
        result = await _cli.run_command(
            SubprocessRunner(),
            (sys.executable, "-c", echo),
            product="Engine",
            operation="echo",
            timeout=30,
            stdin=b"secret-input",
        )
        assert result.stdout == b"tupni-terces"
        with pytest.raises(_cli.CommandError, match="exit code 3") as raised:
            await _cli.run_command(
                SubprocessRunner(),
                (sys.executable, "-c", "print('workload-data'); raise SystemExit(3)"),
                product="Engine",
                operation="fail",
                timeout=30,
            )
        assert "workload-data" not in str(raised.value)

    asyncio.run(check())


@pytest.mark.parametrize("data", [b"not json", b"\xff\xfe", b"[1, 2]", b'{"1": 1, "a": [}'])
def test_json_object_rejects_invalid_or_non_object_output(data: bytes) -> None:
    with pytest.raises(
        SandboxBackendError, match=r"Engine (info returned invalid JSON|info result)"
    ):
        _cli.json_object(data, "Engine", "info")


def test_json_object_accepts_string_keyed_objects() -> None:
    assert _cli.json_object(b'{"a": [1]}', "Engine", "info") == {"a": [1]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, []), (["a", "b"], ["a", "b"]), (["a", 1], None), ("a", None), ({"a": "b"}, None)],
)
def test_string_list_accepts_only_lists_of_strings(
    value: object, expected: list[str] | None
) -> None:
    assert _cli.string_list(value) == expected
