"""A CommandRunner that wraps another runner and counts client processes.

A runner may change how engine clients start, never which arguments they get:
the engine flags enforce the sandbox policy.
"""

from __future__ import annotations

from collections import Counter
from pathlib import PurePath
from typing import TYPE_CHECKING, override

from isolattice import CommandRunner, SubprocessRunner

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Sequence


class AccountingRunner(CommandRunner):
    """Delegate to ``inner`` and count spawned clients by executable name."""

    def __init__(self, inner: CommandRunner | None = None) -> None:
        """Wrap ``inner``, or a new ``SubprocessRunner`` when omitted."""
        if inner is not None and not isinstance(inner, CommandRunner):
            raise TypeError("inner must be a CommandRunner")
        self._inner = inner if inner is not None else SubprocessRunner()
        self.spawned: Counter[str] = Counter()

    @override
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        # Count only the executable name: the rest of argv can carry workload values.
        self.spawned[PurePath(argv[0]).stem] += 1
        return await self._inner.spawn(argv, stdin=stdin)
