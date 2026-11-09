"""Extension point for starting trusted container-engine client processes.

Container backends (Podman, Docker, Kubernetes) control their engines through
command-line clients. A :class:`CommandRunner` decides how those client processes
start, for example to add auditing, tracing or a wrapper that reaches a remote
engine host. Native backends start their workload launchers directly because the
started process is itself the containment identity.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["CommandRunner", "SubprocessRunner"]

STREAM_LIMIT = 65536


class CommandRunner(ABC):
    """Starts engine client processes for container backends.

    The returned process must have piped stdout and stderr and, when ``stdin`` is
    true, a piped stdin; otherwise stdin must be closed or empty. The backend owns
    the process: it bounds its output, enforces deadlines, and kills and reaps it.
    """

    @abstractmethod
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        """Start ``argv`` without a shell.

        Args:
            argv: The client executable and its arguments.
            stdin: Whether the caller writes to the process's standard input.

        Returns:
            The started client process.

        Raises:
            OSError: The client could not be started.
        """


class SubprocessRunner(CommandRunner):
    """Start clients as direct child processes with :mod:`asyncio` pipes."""

    @override
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        """Start ``argv`` as a direct child process without a shell."""
        if not argv:
            raise ValueError("argv must name an executable")
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LIMIT,
        )
