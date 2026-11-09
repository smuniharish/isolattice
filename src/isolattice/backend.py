"""Extension contract for sandbox backends.

A backend turns a :class:`~isolattice.SandboxPolicy` into owned, identity-bound
execution boundaries. Implement :class:`Backend` and :class:`BackendSession` to add
a backend; the runtime owns ordering, deadlines, output streaming and cleanup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .errors import SandboxCapabilityError

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from pathlib import Path

    from .models import (
        BackendReport,
        ExecutionRequest,
        SandboxPolicy,
        TerminationPolicy,
        TerminationReport,
    )

__all__ = ["Backend", "BackendSession", "Boundary"]


@dataclass(slots=True)
class Boundary:
    """One backend-owned execution boundary, identified by an immutable backend ID.

    Attributes:
        boundary_id: Immutable identity the backend uses for every later operation.
        name: Human-readable resource name used in reconciliation notes.
        sandbox_id: Owning sandbox instance.
        execution_id: Owning execution.
        start_requested: Whether a start was requested, so termination must prevent
            a delayed start.
        restore_source: Verified checkpoint the boundary resumes, if any.
    """

    boundary_id: str
    name: str
    sandbox_id: str
    execution_id: str
    start_requested: bool = False
    restore_source: Path | None = None


class BackendSession(ABC):
    """Trusted, cancellation-safe handoff of registered resources to the runtime.

    Creation must register accepted allocations or report uncertain ownership,
    including on cancellation. A failed or cancelled start must reap any local client
    not returned to the caller; the runtime terminates the registered boundary.
    Sessions own instance-wide resources (aggregate budgets, workspaces) and
    release them in :meth:`close`, which the runtime calls exactly once.

    Optional features fail closed with :class:`~isolattice.SandboxCapabilityError`
    unless a subclass overrides them.

    Attributes:
        backend_name: Stable backend name recorded in artifact manifests and events.
    """

    backend_name: str

    @property
    def workspace_path(self) -> str | None:
        """Path of the persistent instance workspace as seen by workloads."""
        return None

    @abstractmethod
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        """Allocate an unstarted boundary and register it before returning.

        Args:
            request: The execution to prepare.
            sandbox_id: Owning sandbox instance.
            execution_id: Owning execution.
            register: Callback that hands the boundary to the runtime; call it as
                soon as the backend may have accepted the allocation.

        Returns:
            The registered boundary.
        """

    @abstractmethod
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        """Start the boundary and return the attached client process.

        The process must expose stdout and stderr pipes, and stdin when the
        request supplies input.
        """

    @abstractmethod
    async def workload_exited(self, boundary: Boundary) -> None:
        """Handle the root workload's exit before its output is drained.

        Backends whose descendants can outlive the root and hold inherited
        output pipes must end the boundary here so draining reaches EOF.
        """

    @abstractmethod
    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        """Return the workload's authoritative exit code."""

    @abstractmethod
    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        """Terminate and release the boundary, verifying it is empty.

        Raises:
            TerminationVerificationError: Emptiness or release could not be verified.
        """

    async def close(self) -> None:
        """Release instance-wide resources after every boundary was terminated."""
        return

    async def export_workspace(self, destination: Path) -> None:
        """Write the quiesced workspace as an uncompressed tar archive.

        Raises:
            SandboxCapabilityError: The session has no workspace.
        """
        raise SandboxCapabilityError("this backend session has no workspace to snapshot")

    async def import_workspace(self, source: Path) -> None:
        """Populate the empty workspace from a validated tar archive.

        Raises:
            SandboxCapabilityError: The session has no workspace.
        """
        raise SandboxCapabilityError("this backend session has no workspace to restore")

    async def checkpoint(self, boundary: Boundary, destination: Path) -> None:
        """Checkpoint a running boundary to ``destination``, leaving it running.

        Raises:
            SandboxCapabilityError: The session does not support checkpoints.
        """
        raise SandboxCapabilityError("this backend session does not support checkpoints")

    async def restore_boundary(
        self,
        source: Path,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        """Allocate a boundary from a verified checkpoint; starting it resumes it.

        Raises:
            SandboxCapabilityError: The session does not support checkpoints.
        """
        raise SandboxCapabilityError("this backend session does not support checkpoints")

    async def snapshot_boundary(self, boundary: Boundary, destination: Path) -> None:
        """Write a quiesced root-filesystem tar of a running boundary, leaving it running.

        Raises:
            SandboxCapabilityError: The session does not support container snapshots.
        """
        raise SandboxCapabilityError("this backend session does not support container snapshots")

    async def import_root(self, source: Path) -> None:
        """Run this session's executions from a validated root-filesystem snapshot.

        Raises:
            SandboxCapabilityError: The session does not support container snapshots.
        """
        raise SandboxCapabilityError("this backend session does not support container snapshots")


class Backend(ABC):
    """Discovers its capabilities and prepares policy-enforcing sessions."""

    @abstractmethod
    async def discover(self) -> BackendReport:
        """Return verified capability evidence for this backend's current host."""

    @abstractmethod
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        """Validate ``policy`` and prepare a session that enforces it.

        Raises:
            SandboxCapabilityError: A required guarantee cannot be enforced.
        """
