"""Immutable configuration, capability and execution records."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Literal

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ENVIRONMENT_BYTES = 64 * 1024
# Windows strips trailing dots from path components and reserves device names.
_ARTIFACT_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?\Z")
_RESERVED_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)
MAX_ARTIFACT_KEY_LENGTH = 512
MAX_MANIFEST_BYTES = 64 * 1024
MANIFEST_SUFFIX = ".manifest.json"
_MANIFEST_FIELDS = frozenset(
    {"kind", "backend", "format", "archive", "sha256", "size", "created_at"}
)


def validate_artifact_key(key: object) -> str:
    """Return ``key`` when it is a portable artifact key, otherwise raise.

    Keys are ``/``-separated segments of ASCII letters, digits, ``.``, ``_`` and
    ``-``. A segment never starts with ``.`` or ``-``, so keys cannot traverse,
    hide partial files or collide with the manifest suffix. A segment never ends
    with ``.`` and is never a Windows device name such as ``CON`` or ``nul.txt``,
    so the same key names the same object on every store and host.

    Args:
        key: The candidate key.

    Returns:
        The validated key.

    Raises:
        TypeError: If ``key`` is not a string.
        ValueError: If ``key`` is empty, too long or has an unsafe segment.
    """
    if not isinstance(key, str):
        raise TypeError("artifact key must be a string")
    if not key or len(key) > MAX_ARTIFACT_KEY_LENGTH:
        raise ValueError(f"artifact key must have 1 to {MAX_ARTIFACT_KEY_LENGTH} characters")
    if any(
        _ARTIFACT_KEY_SEGMENT.match(segment) is None or _RESERVED_DEVICE.match(segment)
        for segment in key.split("/")
    ):
        raise ValueError(f"artifact key {key!r} has an empty or unsafe segment")
    if key.endswith(MANIFEST_SUFFIX):
        raise ValueError(f"artifact key must not end with {MANIFEST_SUFFIX!r}")
    return key


def _positive_number(value: object, name: str, *, zero: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        condition = "nonnegative" if zero else "positive"
        raise ValueError(f"{name} must be finite and {condition}")


def _positive_integer(value: object, name: str, *, zero: bool = False) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (not zero and value == 0):
        condition = "nonnegative" if zero else "positive"
        raise ValueError(f"{name} must be {condition}")


class CapabilityState(StrEnum):
    """How completely a backend enforces a capability on this host."""

    SUPPORTED = "supported"
    LIMITED = "limited"
    UNSUPPORTED = "unsupported"


class Capability(StrEnum):
    """A security or lifecycle guarantee that a backend can enforce."""

    FILESYSTEM_ISOLATION = "filesystem_isolation"
    NETWORK_ISOLATION = "network_isolation"
    PROCESS_ISOLATION = "process_isolation"
    VERIFIED_TERMINATION = "verified_termination"
    ENVIRONMENT_ISOLATION = "environment_isolation"
    RESOURCE_LIMITS = "resource_limits"
    MEMORY_LIMIT = "memory_limit"
    CPU_LIMIT = "cpu_limit"
    PROCESS_LIMIT = "process_limit"
    SNAPSHOTS = "snapshots"
    CHECKPOINTS = "checkpoints"
    INSTANCE_LIMITS = "instance_limits"
    DISK_LIMIT = "disk_limit"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    """The discovered enforcement state of one capability and its evidence."""

    state: CapabilityState
    detail: str


@dataclass(frozen=True, slots=True)
class BackendReport:
    """The result of probing one backend: availability, capabilities and limits."""

    name: str
    available: bool
    capabilities: Mapping[Capability, CapabilityEvidence]
    limitations: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", MappingProxyType(dict(self.capabilities)))


class NetworkAccess(StrEnum):
    """The network access a sandbox policy requests."""

    NONE = "none"
    ENABLED = "enabled"
    ALLOWLIST = "allowlist"


@dataclass(frozen=True, slots=True)
class TerminationPolicy:
    """Bounded attempts and deadlines for verified process-tree termination."""

    attempts: int = 3
    graceful_timeout: float = 5.0
    force_timeout: float = 2.0
    verification_interval: float = 0.5
    retry_delay: float = 1.0
    total_timeout: float = 60.0
    cleanup_deadline: float = 90.0

    def __post_init__(self) -> None:
        _positive_integer(self.attempts, "attempts")
        _positive_number(self.graceful_timeout, "graceful_timeout", zero=True)
        _positive_number(self.force_timeout, "force_timeout")
        _positive_number(self.verification_interval, "verification_interval")
        _positive_number(self.retry_delay, "retry_delay", zero=True)
        _positive_number(self.total_timeout, "total_timeout")
        _positive_number(self.cleanup_deadline, "cleanup_deadline")


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    """An aggregate budget shared by every concurrent execution of one instance."""

    memory_bytes: int | None = None
    cpu_cores: float | None = None
    process_limit: int | None = None

    def __post_init__(self) -> None:
        if self.memory_bytes is not None:
            _positive_integer(self.memory_bytes, "memory_bytes")
        if self.cpu_cores is not None:
            _positive_number(self.cpu_cores, "cpu_cores")
        if self.process_limit is not None:
            _positive_integer(self.process_limit, "process_limit")
        if self.memory_bytes is None and self.cpu_cores is None and self.process_limit is None:
            raise ValueError("a resource budget must set at least one limit")


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Explicit policy; every requested budget is enforced or the request is rejected."""

    network: NetworkAccess = NetworkAccess.NONE
    read_only_root: bool = True
    memory_bytes: int | None = None
    cpu_cores: float | None = None
    process_limit: int | None = None
    instance_budget: ResourceBudget | None = None
    scratch_bytes: int | None = None
    scratch_inodes: int | None = None
    workspace: bool = False
    required_capabilities: tuple[Capability, ...] = ()
    execution_timeout: float = 30.0
    max_concurrent_executions: int = 4
    output_queue_chunks: int = 16
    stdout_limit: int = 1024 * 1024
    stderr_limit: int = 1024 * 1024
    termination: TerminationPolicy = field(default_factory=TerminationPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.network, NetworkAccess):
            raise TypeError("network must be a NetworkAccess")
        if type(self.read_only_root) is not bool:
            raise TypeError("read_only_root must be a bool")
        if self.memory_bytes is not None:
            _positive_integer(self.memory_bytes, "memory_bytes")
        if self.cpu_cores is not None:
            _positive_number(self.cpu_cores, "cpu_cores")
        if self.process_limit is not None:
            _positive_integer(self.process_limit, "process_limit")
        if self.instance_budget is not None and not isinstance(
            self.instance_budget, ResourceBudget
        ):
            raise TypeError("instance_budget must be a ResourceBudget")
        if self.scratch_bytes is not None:
            _positive_integer(self.scratch_bytes, "scratch_bytes")
        if self.scratch_inodes is not None:
            _positive_integer(self.scratch_inodes, "scratch_inodes")
            if self.scratch_bytes is None:
                raise ValueError("scratch_inodes caps the scratch space; set scratch_bytes too")
        if type(self.workspace) is not bool:
            raise TypeError("workspace must be a bool")
        if not all(isinstance(item, Capability) for item in self.required_capabilities):
            raise TypeError("required_capabilities must contain Capability values")
        object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))
        for capability, configured in (
            (Capability.INSTANCE_LIMITS, self.instance_budget is not None),
            (Capability.DISK_LIMIT, self.scratch_bytes is not None),
            (Capability.WORKSPACE, self.workspace),
            (Capability.SNAPSHOTS, self.workspace or not self.read_only_root),
        ):
            if capability in self.required_capabilities and not configured:
                raise ValueError(
                    f"required capability {capability.value} needs its explicit policy setting"
                )
        _positive_number(self.execution_timeout, "execution_timeout")
        _positive_integer(self.max_concurrent_executions, "max_concurrent_executions")
        _positive_integer(self.output_queue_chunks, "output_queue_chunks")
        _positive_integer(self.stdout_limit, "stdout_limit", zero=True)
        _positive_integer(self.stderr_limit, "stderr_limit", zero=True)
        if not isinstance(self.termination, TerminationPolicy):
            raise TypeError("termination must be a TerminationPolicy")


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """An argv command; strings are never interpreted by a host shell."""

    argv: tuple[str, ...]
    stdin: bytes | None = None
    environment: Mapping[str, str] = field(default_factory=dict)
    cwd: str = "/"
    timeout: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.argv, (str, bytes)) or not isinstance(self.argv, (tuple, list)):
            raise TypeError("argv must be a sequence of strings")
        argv = tuple(self.argv)
        if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
            raise ValueError("argv must contain strings without NUL and a nonempty executable")
        if not argv[0] or argv[0].startswith("-"):
            raise ValueError("argv executable must be nonempty and must not start with '-'")
        object.__setattr__(self, "argv", argv)
        if self.stdin is not None and not isinstance(self.stdin, bytes):
            raise TypeError("stdin must be bytes or None")
        if not isinstance(self.environment, Mapping):
            raise TypeError("environment must be a mapping")
        environment = dict(self.environment)
        size = 0
        for key, value in environment.items():
            if not isinstance(key, str) or _ENVIRONMENT_NAME.fullmatch(key) is None:
                raise ValueError("environment names must use portable identifier syntax")
            if not isinstance(value, str) or any(char in value for char in ("\0", "\r", "\n")):
                raise ValueError("environment values must be strings without NUL or line breaks")
            size += len(f"{key}={value}\n".encode())
        if size > _MAX_ENVIRONMENT_BYTES:
            raise ValueError("environment exceeds the 64 KiB backend input limit")
        object.__setattr__(self, "environment", MappingProxyType(environment))
        if not isinstance(self.cwd, str) or "\0" in self.cwd:
            raise ValueError("cwd must be an absolute container path without NUL")
        path = PurePosixPath(self.cwd)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("cwd must be an absolute container path without '..'")
        if self.timeout is not None:
            _positive_number(self.timeout, "timeout")


@dataclass(frozen=True, slots=True)
class OutputChunk:
    """A bounded chunk of streamed workload output."""

    stream: Literal["stdout", "stderr"]
    data: bytes


@dataclass(frozen=True, slots=True)
class TerminationReport:
    """The verified outcome of terminating one containment boundary."""

    boundary_id: str
    attempts: int
    observations: int
    state: Literal["terminated", "non_empty", "unknown"]
    resources_released: bool
    notes: tuple[str, ...] = ()


class ArtifactKind(StrEnum):
    """The kind of artifact recorded in a manifest."""

    WORKSPACE_SNAPSHOT = "workspace-snapshot"
    EXECUTION_CHECKPOINT = "execution-checkpoint"
    CONTAINER_SNAPSHOT = "container-snapshot"


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Integrity record an artifact store keeps beside a snapshot or checkpoint.

    The digest detects corruption and accidental substitution; it is not a
    signature, so the manifest location must be as trusted as the archive.
    ``archive`` is the artifact key the record was published under.
    """

    kind: ArtifactKind
    backend: str
    format: str
    archive: str
    sha256: str
    size: int
    created_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ArtifactKind):
            raise TypeError("kind must be an ArtifactKind")
        for name in ("backend", "format", "archive", "created_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError(f"{name} must be a nonempty string")
        validate_artifact_key(self.archive)
        if not isinstance(self.sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None:
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        _positive_integer(self.size, "size", zero=True)

    def to_json(self) -> bytes:
        """Encode the manifest as canonical UTF-8 JSON for an artifact store."""
        return json.dumps(
            {
                "kind": self.kind.value,
                "backend": self.backend,
                "format": self.format,
                "archive": self.archive,
                "sha256": self.sha256,
                "size": self.size,
                "created_at": self.created_at,
            },
            indent=2,
            sort_keys=True,
        ).encode()

    @classmethod
    def from_json(cls, data: bytes) -> ArtifactManifest:
        """Decode a manifest produced by :meth:`to_json`.

        Args:
            data: The stored manifest bytes, at most ``MAX_MANIFEST_BYTES``.

        Returns:
            The validated manifest.

        Raises:
            ValueError: If the bytes are oversized, malformed or have unexpected fields.
        """
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("artifact manifest exceeds its size limit")
        try:
            record = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("artifact manifest is not valid JSON") from error
        if not isinstance(record, dict) or set(record) != _MANIFEST_FIELDS:
            raise ValueError("artifact manifest has unexpected fields")
        try:
            return cls(
                kind=ArtifactKind(record["kind"]),
                backend=record["backend"],
                format=record["format"],
                archive=record["archive"],
                sha256=record["sha256"],
                size=record["size"],
                created_at=record["created_at"],
            )
        except TypeError as error:
            raise ValueError(f"artifact manifest is malformed: {error}") from error


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """The completed execution: exit code, bounded output and cleanup report."""

    sandbox_id: str
    execution_id: str
    exit_code: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    duration: float
    termination: TerminationReport
