"""Public exceptions raised by the runtime and backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import TerminationReport


class SandboxError(Exception):
    """Base class for Isolattice failures."""


class SandboxCapabilityError(SandboxError):
    """A requested guarantee is unavailable or has not been qualified."""


class SandboxStateError(SandboxError):
    """An operation is invalid for the current lifecycle state."""


class SandboxBackendError(SandboxError):
    """The explicitly selected backend could not complete an operation."""


class SandboxExecutionError(SandboxError):
    """A workload could not be started or its I/O could not be delivered."""


class SandboxTimeoutError(SandboxExecutionError, TimeoutError):
    """An execution exceeded its deadline and cleanup was initiated."""

    def __init__(self, message: str, report: TerminationReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class SandboxTerminationError(SandboxError):
    """A boundary could not be terminated and its resources released."""

    def __init__(self, message: str, report: TerminationReport | None = None) -> None:
        super().__init__(message)
        self.report = report


class TerminationVerificationError(SandboxTerminationError):
    """Boundary emptiness or required resource release could not be verified."""


class SandboxArtifactError(SandboxError):
    """A snapshot or checkpoint artifact is missing, unsafe or fails integrity checks."""
