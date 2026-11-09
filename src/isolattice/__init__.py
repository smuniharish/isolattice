"""Async sandbox execution with explicit backend selection and verified policies."""

from importlib.metadata import PackageNotFoundError, version

from .backend import Backend, BackendSession, Boundary
from .backends import (
    DockerBackend,
    KubernetesBackend,
    LinuxNamespaceBackend,
    PodmanBackend,
    WindowsAppContainerBackend,
    WindowsJobBackend,
)
from .commands import CommandRunner, SubprocessRunner
from .errors import (
    SandboxArtifactError,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxError,
    SandboxExecutionError,
    SandboxStateError,
    SandboxTerminationError,
    SandboxTimeoutError,
    TerminationVerificationError,
)
from .events import EventKind, EventSink, LoggingEventSink, SandboxEvent
from .models import (
    ArtifactKind,
    ArtifactManifest,
    BackendReport,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    ExecutionRequest,
    ExecutionResult,
    NetworkAccess,
    OutputChunk,
    ResourceBudget,
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
    validate_artifact_key,
)
from .runtime import ExecutionHandle, Sandbox, SandboxInstance
from .s3 import S3ArtifactStore, S3Credentials
from .storage import ArtifactStore, LocalArtifactStore
from .termination import BoundaryState, Observation, TerminationDriver, terminate_boundary

try:
    __version__ = version("isolattice")
except PackageNotFoundError:  # pragma: no cover - only when run from an uninstalled tree
    __version__ = "0.0.0"

__all__ = [
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactStore",
    "Backend",
    "BackendReport",
    "BackendSession",
    "Boundary",
    "BoundaryState",
    "Capability",
    "CapabilityEvidence",
    "CapabilityState",
    "CommandRunner",
    "DockerBackend",
    "EventKind",
    "EventSink",
    "ExecutionHandle",
    "ExecutionRequest",
    "ExecutionResult",
    "KubernetesBackend",
    "LinuxNamespaceBackend",
    "LocalArtifactStore",
    "LoggingEventSink",
    "NetworkAccess",
    "Observation",
    "OutputChunk",
    "PodmanBackend",
    "ResourceBudget",
    "S3ArtifactStore",
    "S3Credentials",
    "Sandbox",
    "SandboxArtifactError",
    "SandboxBackendError",
    "SandboxCapabilityError",
    "SandboxError",
    "SandboxEvent",
    "SandboxExecutionError",
    "SandboxInstance",
    "SandboxPolicy",
    "SandboxStateError",
    "SandboxTerminationError",
    "SandboxTimeoutError",
    "SubprocessRunner",
    "TerminationDriver",
    "TerminationPolicy",
    "TerminationReport",
    "TerminationVerificationError",
    "WindowsAppContainerBackend",
    "WindowsJobBackend",
    "__version__",
    "terminate_boundary",
    "validate_artifact_key",
]
