"""Explicit Podman execution with inspected isolation and gated resource limits."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, override

from .._async import raise_combined, raise_with_cleanup, settle
from ..backend import Backend, BackendSession, Boundary
from ..commands import CommandRunner, SubprocessRunner
from ..errors import (
    SandboxArtifactError,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxExecutionError,
    TerminationVerificationError,
)
from ..models import (
    BackendReport,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    ExecutionRequest,
    NetworkAccess,
    ResourceBudget,
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
    _positive_number,
)
from ..termination import BoundaryState, Observation, TerminationDriver, terminate_boundary
from ._cli import CommandError, CommandResult, json_object, mapping, reap_client, run_command
from ._podman_log_filter import parse_timestamp

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_ID = re.compile(r"[0-9a-f]{64}\Z")
_PRODUCT = "Podman"
_OWNER_LABEL = "io.isolattice.sandbox"
_EXECUTION_LABEL = "io.isolattice.execution"
_WORKSPACE = "/workspace"
_LOG_MAX_SIZE = "16mb"
_LOG_FILTER = Path(__file__).with_name("_podman_log_filter.py")
_MAX_SPEC_BYTES = 4 * 1024 * 1024
_MAX_LOG_BYTES = 64 * 1024 * 1024
_REQUIRED_NAMESPACES = frozenset({"pid", "network", "ipc", "uts", "mount", "cgroup"})
_RESTORED_MOUNTS = frozenset(
    {
        "/proc",
        "/dev",
        "/sys",
        "/dev/pts",
        "/dev/mqueue",
        "/dev/shm",
        "/sys/fs/cgroup",
        "/run/.containerenv",
        "/run/secrets",
        "/etc/hosts",
        "/etc/hostname",
        "/etc/resolv.conf",
        "/tmp",
    }
)
_ENGINE_STORAGE = ("/var/lib/containers/storage/", "/run/containers/storage/")


_DEFAULT_RUNNER = SubprocessRunner()


@dataclass(frozen=True, slots=True)
class _Engine:
    executable: str
    connection: str
    command_timeout: float
    image_id: str
    baseline_capabilities: frozenset[str]
    rootless: bool = True
    cgroup_version: str = ""
    controllers: frozenset[str] = frozenset()
    transfer_timeout: float = 300.0
    runner: CommandRunner = _DEFAULT_RUNNER
    helper_interpreter: str = sys.executable

    def command(self, *args: str) -> tuple[str, ...]:
        return (self.executable, f"--connection={self.connection}", *args)


def _cpu_quota(cores: float, context: str) -> int:
    exact = Decimal(str(cores)) * 100000
    if exact != exact.to_integral_value() or not 1000 <= exact < 2**63:
        raise SandboxCapabilityError(
            f"Podman {context} CPU budget must be exactly representable in 100 ms periods "
            "with a quota between 1000 and 2^63-1 microseconds"
        )
    return int(exact)


def _memory(value: int, context: str) -> int:
    if not 6 * 1024 * 1024 <= value < 2**63:
        raise SandboxCapabilityError(f"Podman {context} memory budget must be 6 MiB to 2^63-1")
    return value


@dataclass(frozen=True, slots=True)
class _Budget:
    """Aggregate pod cgroup budget shared by every execution of an instance."""

    memory_bytes: int | None = None
    cpu_quota: int | None = None

    @classmethod
    def from_budget(cls, budget: ResourceBudget) -> _Budget:
        if budget.process_limit is not None:
            raise SandboxCapabilityError(
                "Podman pods cannot enforce an aggregate process budget; "
                "use the per-execution process_limit instead"
            )
        return cls(
            _memory(budget.memory_bytes, "instance") if budget.memory_bytes is not None else None,
            _cpu_quota(budget.cpu_cores, "instance") if budget.cpu_cores is not None else None,
        )

    @property
    def controllers(self) -> frozenset[str]:
        return frozenset(
            name
            for name, value in (("memory", self.memory_bytes), ("cpu", self.cpu_quota))
            if value is not None
        )

    def arguments(self) -> tuple[str, ...]:
        args: list[str] = []
        if self.memory_bytes is not None:
            args.extend(
                ("--memory", str(self.memory_bytes), "--memory-swap", str(self.memory_bytes))
            )
        if self.cpu_quota is not None:
            args.extend(("--cpus", str(Decimal(self.cpu_quota) / 100000)))
        return tuple(args)

    def matches(self, data: Mapping[str, object]) -> bool:
        expected: dict[str, int] = {}
        if self.memory_bytes is not None:
            expected.update(memory_limit=self.memory_bytes, memory_swap=self.memory_bytes)
        if self.cpu_quota is not None:
            expected.update(cpu_quota=self.cpu_quota, cpu_period=100000)
        return all(
            type(data.get(key)) is int and data[key] == value for key, value in expected.items()
        )


@dataclass(frozen=True, slots=True)
class _Limits:
    memory_bytes: int | None = None
    cpu_quota: int | None = None
    process_limit: int | None = None

    @classmethod
    def from_policy(cls, policy: SandboxPolicy) -> _Limits:
        memory = policy.memory_bytes
        if memory is not None:
            _memory(memory, "execution")
        if policy.process_limit is not None and policy.process_limit >= 2**63:
            raise SandboxCapabilityError("Podman process budget must be less than 2^63")
        quota = _cpu_quota(policy.cpu_cores, "execution") if policy.cpu_cores is not None else None
        return cls(memory, quota, policy.process_limit)

    @property
    def controllers(self) -> frozenset[str]:
        return frozenset(
            name
            for name, value in (
                ("memory", self.memory_bytes),
                ("cpu", self.cpu_quota),
                ("pids", self.process_limit),
            )
            if value is not None
        )

    def arguments(self) -> tuple[str, ...]:
        args: list[str] = []
        if self.memory_bytes is not None:
            args.extend(
                ("--memory", str(self.memory_bytes), "--memory-swap", str(self.memory_bytes))
            )
        if self.cpu_quota is not None:
            args.extend(("--cpu-period", "100000", "--cpu-quota", str(self.cpu_quota)))
        if self.process_limit is not None:
            args.extend(("--pids-limit", str(self.process_limit)))
        return tuple(args)

    def marker(self, execution_id: str) -> bytes:
        return f"ISOLATTICE_READY_{execution_id}\n".encode()

    def command(self, request: ExecutionRequest, execution_id: str) -> tuple[str, ...]:
        if not self.controllers:
            return request.argv
        checks: list[str] = []
        if self.memory_bytes is not None:
            checks.extend(
                (
                    "IFS= read -r value < /sys/fs/cgroup/memory.max || exit 125",
                    f'[ "$value" -gt 0 ] && [ "$value" -le {self.memory_bytes} ] || exit 125',
                    "IFS= read -r value < /sys/fs/cgroup/memory.swap.max || exit 125",
                    '[ "$value" = 0 ] || exit 125',
                )
            )
        if self.cpu_quota is not None:
            checks.extend(
                (
                    "IFS= read -r value < /sys/fs/cgroup/cpu.max || exit 125",
                    f'[ "$value" = "{self.cpu_quota} 100000" ] || exit 125',
                )
            )
        if self.process_limit is not None:
            checks.extend(
                (
                    "IFS= read -r value < /sys/fs/cgroup/pids.max || exit 125",
                    f'[ "$value" = "{self.process_limit}" ] || exit 125',
                )
            )
        checks.append('printf "%s\\n" "$1"; shift; exec "$@"')
        return (
            "/bin/sh",
            "-c",
            "; ".join(checks),
            "isolattice-limits",
            self.marker(execution_id).decode().rstrip("\n"),
            *request.argv,
        )


async def _invoke(
    engine: _Engine,
    operation: str,
    *args: str,
    accepted: tuple[int, ...] = (0,),
    timeout: float | None = None,  # noqa: ASYNC109 - forwarded to the bounded runner
) -> CommandResult:
    return await run_command(
        engine.runner,
        engine.command(*args),
        product=_PRODUCT,
        operation=operation,
        timeout=engine.command_timeout if timeout is None else timeout,
        accepted=accepted,
    )


def _object(data: bytes, operation: str) -> dict[str, object]:
    return json_object(data, _PRODUCT, operation)


def _mapping(value: object, context: str) -> dict[str, object]:
    return mapping(value, _PRODUCT, context)


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise SandboxBackendError(f"Podman {context} did not return a full immutable ID")
    return value


def _report(
    available: bool, error: str | None = None, *, engine: _Engine | None = None
) -> BackendReport:
    limited = CapabilityState.LIMITED if available else CapabilityState.UNSUPPORTED
    controllers = (
        engine.controllers
        if available and engine is not None and engine.cgroup_version == "v2"
        else frozenset()
    )
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: CapabilityEvidence(
            limited,
            "read-only image root unless the policy requests a writable container layer; "
            "the only host-backed mount is the optional owned workspace volume",
        ),
        Capability.NETWORK_ISOLATION: CapabilityEvidence(
            limited, "only an engine-created network namespace with networking disabled"
        ),
        Capability.PROCESS_ISOLATION: CapabilityEvidence(
            limited, "private container PID namespace; hostile-workload qualification incomplete"
        ),
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            limited, "engine-reported boundary state and owned container removal"
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            limited, "image environment removed; explicit environment is visible to the engine"
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            limited if controllers & {"memory", "cpu", "pids"} else CapabilityState.UNSUPPORTED,
            "per-execution cgroup v2 budgets, scratch tmpfs caps and rootful pod budgets "
            "are gated individually; workspace volumes have no quota",
        ),
        Capability.SNAPSHOTS: CapabilityEvidence(
            CapabilityState.SUPPORTED if available else CapabilityState.UNSUPPORTED,
            "cold export and import of the owned workspace volume while executions are idle, "
            "and pause-consistent root-filesystem exports restored as owned images",
        ),
        Capability.CHECKPOINTS: _checkpoint_evidence(available, engine),
        Capability.INSTANCE_LIMITS: _instance_evidence(available, engine, controllers),
        Capability.DISK_LIMIT: CapabilityEvidence(
            CapabilityState.SUPPORTED if available else CapabilityState.UNSUPPORTED,
            "per-execution size-capped tmpfs at /tmp, inspected before start and charged "
            "to memory; a writable root layer and workspace volumes have no quota",
        ),
        Capability.WORKSPACE: CapabilityEvidence(
            CapabilityState.SUPPORTED if available else CapabilityState.UNSUPPORTED,
            f"owned named volume mounted at {_WORKSPACE} and removed when the instance closes",
        ),
    }
    for capability, controller, detail in (
        (Capability.MEMORY_LIMIT, "memory", "cgroup memory.max with swap disabled"),
        (Capability.CPU_LIMIT, "cpu", "cgroup cpu.max bandwidth quota over 100 ms periods"),
        (Capability.PROCESS_LIMIT, "pids", "cgroup pids.max counts descendant tasks and threads"),
    ):
        capabilities[capability] = CapabilityEvidence(
            CapabilityState.SUPPORTED if controller in controllers else CapabilityState.UNSUPPORTED,
            f"{detail}; inspected and verified before workload exec"
            if controller in controllers
            else f"requires a delegated {controller} controller on cgroup v2",
        )
    return BackendReport(
        name="podman",
        available=available,
        capabilities=capabilities,
        limitations=(
            "Containers share the host kernel; kernel or engine exploits are out of scope.",
            "Each execution uses a separate container; only the optional workspace persists.",
            "The Podman engine, image, host kernel and configured connection are trusted.",
            "Environment values and command arguments are visible to the engine owner.",
            "Checkpoint archives contain workload memory and are trusted restore inputs.",
            "Container snapshots exclude the workspace volume and /tmp scratch.",
            f"Explicit engine mode: {'rootless' if engine.rootless else 'rootful'}."
            if engine is not None
            else "Engine mode has not been established.",
        ),
        error=error,
    )


def _require_supported(
    policy: SandboxPolicy, report: BackendReport, skipped: set[Capability]
) -> None:
    for capability in policy.required_capabilities:
        if capability in skipped:
            continue
        evidence = report.capabilities[capability]
        if evidence.state is not CapabilityState.SUPPORTED:
            raise SandboxCapabilityError(
                f"required capability {capability.value} is {evidence.state.value}: "
                f"{evidence.detail}"
            )


def _checkpoint_evidence(available: bool, engine: _Engine | None) -> CapabilityEvidence:
    if not available or engine is None:
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "requires an established rootful Podman engine"
        )
    if engine.rootless:
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "CRIU checkpoints require a rootful Podman engine"
        )
    return CapabilityEvidence(
        CapabilityState.LIMITED,
        "CRIU via podman checkpoint/restore; instances must require CHECKPOINTS, which "
        "probes CRIU live and keeps size-bounded engine logs; workspaces and pod budgets "
        "cannot be combined with checkpoints",
    )


def _instance_evidence(
    available: bool, engine: _Engine | None, controllers: frozenset[str]
) -> CapabilityEvidence:
    if not available or engine is None:
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "requires an established rootful Podman engine"
        )
    if engine.rootless:
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED,
            "rootless cgroupfs pods were observed not to apply pod cgroup limits",
        )
    if not {"memory", "cpu"} & controllers:
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "requires cgroup v2 memory or cpu controllers"
        )
    return CapabilityEvidence(
        CapabilityState.SUPPORTED,
        "rootful pod cgroup memory.max (swap disabled) and cpu.max shared by all executions; "
        "aggregate process budgets are unsupported",
    )


class PodmanBackend(Backend):
    """Use one named Podman connection; rootful engines require explicit opt-in."""

    def __init__(
        self,
        *,
        connection: str,
        image: str,
        executable: str | None = None,
        command_timeout: float = 15.0,
        allow_rootful: bool = False,
        transfer_timeout: float = 300.0,
        runner: CommandRunner | None = None,
        helper_interpreter: str | None = None,
    ) -> None:
        for name, value in (("connection", connection), ("image", image)):
            if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value:
                raise ValueError(f"{name} must be a nonempty explicit value")
        if executable is not None and (not isinstance(executable, str) or not executable):
            raise ValueError("executable must be a nonempty path or command")
        _positive_number(command_timeout, "command_timeout")
        _positive_number(transfer_timeout, "transfer_timeout")
        if type(allow_rootful) is not bool:
            raise TypeError("allow_rootful must be a bool")
        self._connection = connection
        self._image = image
        self._executable = executable
        self._command_timeout = command_timeout
        self._allow_rootful = allow_rootful
        self._transfer_timeout = transfer_timeout
        if runner is not None and not isinstance(runner, CommandRunner):
            raise TypeError("runner must be a CommandRunner")
        if helper_interpreter is not None and (
            not isinstance(helper_interpreter, str) or not helper_interpreter
        ):
            raise ValueError("helper_interpreter must be a nonempty interpreter path")
        self._runner = runner if runner is not None else _DEFAULT_RUNNER
        self._helper_interpreter = helper_interpreter or sys.executable

    async def _probe(self) -> _Engine:
        executable = self._executable or shutil.which("podman")
        if executable is None:
            raise SandboxBackendError("Podman executable was not found")
        provisional = _Engine(
            executable,
            self._connection,
            self._command_timeout,
            "",
            frozenset(),
            runner=self._runner,
            helper_interpreter=self._helper_interpreter,
        )
        security_result = await _invoke(
            provisional, "security discovery", "info", "--format", "{{json .Host}}"
        )
        host = _object(security_result.stdout, "security discovery")
        security = _mapping(host.get("security"), "host security")
        rootless = security.get("rootless")
        if not isinstance(rootless, bool):
            raise SandboxBackendError("Podman did not report rootless engine identity")
        if not rootless and not self._allow_rootful:
            raise SandboxCapabilityError("the explicitly configured Podman engine is not rootless")
        if security.get("seccompEnabled") is not True:
            raise SandboxCapabilityError("the configured Podman engine does not report seccomp")
        capabilities = security.get("capabilities")
        if not isinstance(capabilities, str):
            raise SandboxBackendError("Podman did not report its baseline capabilities")
        controllers = host.get("cgroupControllers")
        version = host.get("cgroupVersion")
        if (
            not isinstance(version, str)
            or not isinstance(controllers, list)
            or not all(isinstance(item, str) for item in controllers)
        ):
            raise SandboxBackendError("Podman did not report valid cgroup controller metadata")
        image_result = await _invoke(
            provisional,
            "local image discovery",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "--",
            self._image,
        )
        try:
            image_text = image_result.stdout.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise SandboxBackendError("Podman image discovery returned an invalid ID") from error
        image_id = _identifier(image_text.removeprefix("sha256:"), "image discovery")
        return _Engine(
            executable,
            self._connection,
            self._command_timeout,
            image_id,
            frozenset(filter(None, capabilities.split(","))),
            rootless=rootless,
            cgroup_version=version,
            controllers=frozenset(controllers),
            transfer_timeout=self._transfer_timeout,
            runner=self._runner,
            helper_interpreter=self._helper_interpreter,
        )

    @override
    async def discover(self) -> BackendReport:
        try:
            engine = await self._probe()
        except (SandboxBackendError, SandboxCapabilityError) as error:
            return _report(False, str(error))
        return _report(True, engine=engine)

    @override
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        if policy.network is not NetworkAccess.NONE:
            raise SandboxCapabilityError("only disabled networking is implemented")
        if not policy.read_only_root and Capability.DISK_LIMIT in policy.required_capabilities:
            raise SandboxCapabilityError(
                "DISK_LIMIT cannot be required with a writable root: the container layer "
                "has no quota"
            )
        limits = _Limits.from_policy(policy)
        budget = (
            _Budget.from_budget(policy.instance_budget)
            if policy.instance_budget is not None
            else None
        )
        if policy.workspace and Capability.DISK_LIMIT in policy.required_capabilities:
            raise SandboxCapabilityError(
                "DISK_LIMIT cannot be required with a workspace: Podman volumes have no quota"
            )
        checkpoints = Capability.CHECKPOINTS in policy.required_capabilities
        if checkpoints and (policy.workspace or budget is not None or not policy.read_only_root):
            raise SandboxCapabilityError(
                "Podman checkpoints cannot be combined with workspaces, instance budgets "
                "or a writable root"
            )
        resource_budgets = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.cpu_quota,
            Capability.PROCESS_LIMIT: limits.process_limit,
        }
        for capability in policy.required_capabilities:
            if capability in resource_budgets and resource_budgets[capability] is None:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} needs an explicit budget"
                )
        skipped = {*resource_budgets, Capability.CHECKPOINTS}
        _require_supported(policy, _report(True), skipped | {Capability.INSTANCE_LIMITS})
        engine = await self._probe()
        _require_supported(policy, _report(True, engine=engine), skipped)
        needed = limits.controllers | (budget.controllers if budget is not None else frozenset())
        if needed and engine.cgroup_version != "v2":
            raise SandboxCapabilityError("resource limits require cgroup v2")
        missing = needed - engine.controllers
        if missing:
            raise SandboxCapabilityError(
                f"resource limits require delegated controllers: {', '.join(sorted(missing))}"
            )
        if budget is not None and engine.rootless:
            raise SandboxCapabilityError(
                "instance budgets need a rootful engine: "
                + _instance_evidence(True, engine, engine.controllers).detail
            )
        if checkpoints:
            if engine.rootless:
                raise SandboxCapabilityError("CRIU checkpoints require a rootful Podman engine")
            await _probe_checkpoint(engine, sandbox_id)
        session = _PodmanSession(
            engine,
            limits,
            sandbox_id=sandbox_id,
            scratch_bytes=policy.scratch_bytes,
            checkpoints=checkpoints,
            read_only_root=policy.read_only_root,
        )
        try:
            await session._allocate_instance(budget, workspace=policy.workspace)
        except BaseException as error:
            cleanup = await settle(asyncio.create_task(session.close()))
            raise_with_cleanup(
                "Podman instance allocation and cleanup failed",
                error,
                cleanup.interrupted,
                cleanup.error,
            )
        return session


async def _probe_checkpoint(engine: _Engine, sandbox_id: str) -> None:
    """Checkpoint a short-lived hardened container to prove CRIU works on this engine."""
    name = f"isolattice-{sandbox_id}-criu-probe"
    failure: BaseException | None = None
    archive: Path | None = None
    try:
        created = await _invoke(
            engine,
            "checkpoint probe creation",
            "create",
            "--name",
            name,
            "--label",
            f"{_OWNER_LABEL}={sandbox_id}",
            "--label",
            f"{_EXECUTION_LABEL}=criu-probe",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--log-driver",
            "k8s-file",
            "--log-opt",
            f"max-size={_LOG_MAX_SIZE}",
            "--pull",
            "never",
            "--entrypoint",
            "/bin/sleep",
            engine.image_id,
            "60",
        )
        identifier = _identifier(created.stdout.decode("ascii").strip(), "probe creation")
        await _invoke(engine, "checkpoint probe start", "start", identifier)
        descriptor, raw = tempfile.mkstemp(prefix="isolattice-criu-probe-", suffix=".tar.gz")
        os.close(descriptor)
        archive = Path(raw)
        archive.unlink()
        await _invoke(
            engine,
            "checkpoint probe",
            "container",
            "checkpoint",
            "--leave-running",
            "--export",
            str(archive),
            identifier,
            timeout=engine.transfer_timeout,
        )
        if not archive.is_file() or archive.stat().st_size == 0:
            raise SandboxCapabilityError("the Podman CRIU probe produced no checkpoint archive")
    except BaseException as error:
        failure = error
    cleanup: list[BaseException] = []
    try:
        await _invoke(
            engine,
            "checkpoint probe removal",
            "rm",
            "--force",
            "--ignore",
            "--time",
            "0",
            name,
        )
    except BaseException as error:
        cleanup.append(error)
    if archive is not None:
        try:
            archive.unlink(missing_ok=True)
        except OSError as error:
            cleanup.append(error)
    if failure is not None and isinstance(failure, CommandError):
        replacement = SandboxCapabilityError(
            "the configured Podman engine could not checkpoint a probe container with CRIU"
        )
        replacement.__cause__ = failure
        failure = replacement
    raise_combined("Podman CRIU probe and cleanup failed", failure, *cleanup)


def _spec_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SandboxArtifactError(f"checkpoint spec {context} is missing or invalid")
    return value


def _validate_spec(data: bytes) -> None:
    """Reject checkpoint specs that would restore outside the hardened container shape."""
    try:
        spec = _spec_mapping(json.loads(data), "document")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SandboxArtifactError("checkpoint spec is not valid JSON") from error
    process = _spec_mapping(spec.get("process"), "process")
    user = _spec_mapping(process.get("user"), "user")
    groups = user.get("additionalGids", [])
    if (
        user.get("uid") != 65534
        or user.get("gid") != 65534
        or not isinstance(groups, list)
        or any(group != 65534 for group in groups)
    ):
        raise SandboxArtifactError("checkpoint spec does not run as the unprivileged user")
    if process.get("noNewPrivileges") is not True:
        raise SandboxArtifactError("checkpoint spec does not set no-new-privileges")
    capabilities = _spec_mapping(process.get("capabilities", {}), "capabilities")
    if any(value not in (None, []) for value in capabilities.values()):
        raise SandboxArtifactError("checkpoint spec grants Linux capabilities")
    if _spec_mapping(spec.get("root"), "root").get("readonly") is not True:
        raise SandboxArtifactError("checkpoint spec does not use a read-only root")
    linux = _spec_mapping(spec.get("linux"), "linux")
    namespaces = linux.get("namespaces")
    if not isinstance(namespaces, list):
        raise SandboxArtifactError("checkpoint spec namespaces are invalid")
    kinds: set[object] = set()
    for entry in namespaces:
        namespace = _spec_mapping(entry, "namespace")
        if namespace.get("path") not in (None, ""):
            raise SandboxArtifactError("checkpoint spec joins an existing namespace")
        kinds.add(namespace.get("type"))
    if kinds != _REQUIRED_NAMESPACES:
        raise SandboxArtifactError("checkpoint spec does not use exactly the private namespaces")
    if linux.get("devices") not in (None, []):
        raise SandboxArtifactError("checkpoint spec adds devices")
    masked = linux.get("maskedPaths")
    if not isinstance(linux.get("seccomp"), dict) or not isinstance(masked, list) or not masked:
        raise SandboxArtifactError("checkpoint spec lacks seccomp or masked paths")
    mounts = spec.get("mounts")
    if not isinstance(mounts, list):
        raise SandboxArtifactError("checkpoint spec mounts are invalid")
    for entry in mounts:
        mount = _spec_mapping(entry, "mount")
        options = mount.get("options", [])
        if mount.get("destination") not in _RESTORED_MOUNTS or not isinstance(options, list):
            raise SandboxArtifactError("checkpoint spec has an unexpected mount destination")
        source = mount.get("source")
        if (mount.get("type") == "bind" or "bind" in options or "rbind" in options) and (
            not isinstance(source, str)
            or not source.startswith(_ENGINE_STORAGE)
            or ".." in PurePosixPath(source).parts
        ):
            raise SandboxArtifactError("checkpoint spec binds a path outside engine storage")


def _inspect_checkpoint(source: Path) -> int | None:
    """Validate a Podman checkpoint export; return the last pre-checkpoint log timestamp."""
    found: dict[str, bytes] = {}
    limits = {"spec.dump": _MAX_SPEC_BYTES, "ctr.log": _MAX_LOG_BYTES}
    try:
        with tarfile.open(source, mode="r:*") as bundle:
            for member in bundle:
                key = str(PurePosixPath(member.name))
                if key not in limits:
                    continue
                if key in found:
                    raise SandboxArtifactError(f"checkpoint archive repeats {key}")
                if not member.isreg() or member.size > limits[key]:
                    raise SandboxArtifactError(f"checkpoint archive {key} is invalid or too large")
                stream = bundle.extractfile(member)
                if stream is None:
                    raise SandboxArtifactError(f"checkpoint archive {key} is unreadable")
                with stream:
                    found[key] = stream.read(limits[key] + 1)
    except (tarfile.TarError, OSError, EOFError) as error:
        raise SandboxArtifactError("checkpoint archive is not a readable Podman export") from error
    if "spec.dump" not in found or "ctr.log" not in found:
        raise SandboxArtifactError(
            "checkpoint archive lacks its spec or container log; only checkpoints from "
            "CHECKPOINTS-enabled Podman instances can be restored"
        )
    _validate_spec(found["spec.dump"])
    entries = [line for line in found["ctr.log"].splitlines() if line.strip()]
    if not entries:
        return None
    try:
        return parse_timestamp(entries[-1].split(b" ", 1)[0])
    except ValueError as error:
        raise SandboxArtifactError("checkpoint container log has an invalid timestamp") from error


def _environment_file(environment: Mapping[str, str]) -> Path | None:
    if not environment:
        return None
    path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="isolattice-env-", suffix=".env")
        path = Path(name)
        with os.fdopen(descriptor, "wb") as file:
            for key, value in environment.items():
                file.write(f"{key}={value}\n".encode())
    except OSError as error:
        if path is not None:
            try:
                path.unlink()
            except OSError as cleanup:
                raise BaseExceptionGroup(
                    "environment file creation and cleanup failed",
                    [
                        SandboxBackendError("could not write Podman environment file"),
                        SandboxBackendError(f"could not remove environment file: {cleanup}"),
                    ],
                ) from error
        raise SandboxBackendError("could not write Podman environment file") from error
    return path


class _PodmanSession(BackendSession):
    backend_name = "podman"

    def __init__(
        self,
        engine: _Engine,
        limits: _Limits | None = None,
        *,
        sandbox_id: str | None = None,
        scratch_bytes: int | None = None,
        checkpoints: bool = False,
        read_only_root: bool = True,
    ) -> None:
        self._engine = engine
        self._limits = limits if limits is not None else _Limits()
        self._sandbox_id = sandbox_id
        self._scratch_bytes = scratch_bytes
        self._checkpoints = checkpoints
        self._read_only_root = read_only_root
        self._image_id = engine.image_id
        self._import_attempted = False
        self._pod_name: str | None = None
        self._pod: tuple[str, str] | None = None
        self._volume: str | None = None
        self._volume_ready = False
        self._marks: dict[str, int | None] = {}
        self._closed = False

    @property
    @override
    def workspace_path(self) -> str | None:
        return _WORKSPACE if self._volume_ready else None

    async def _owned(self, kind: str, name: str) -> dict[str, object] | None:
        """Inspect an owned pod or volume by name; ``None`` means it does not exist."""
        exists = await _invoke(
            self._engine, f"{kind} existence check", kind, "exists", name, accepted=(0, 1)
        )
        if exists.returncode == 1:
            return None
        result = await _invoke(
            self._engine, f"{kind} inspection", kind, "inspect", name, "--format", "{{json .}}"
        )
        data = _object(result.stdout, f"{kind} inspection")
        labels = _mapping(data.get("Labels"), f"{kind} labels")
        if (
            self._sandbox_id is None
            or data.get("Name") != name
            or labels.get(_OWNER_LABEL) != self._sandbox_id
        ):
            raise SandboxBackendError(f"refusing to operate on a {kind} without ownership labels")
        return data

    async def _allocate_instance(self, budget: _Budget | None, *, workspace: bool) -> None:
        if self._sandbox_id is None:
            raise AssertionError("instance resources need a sandbox ID")
        owner = f"{_OWNER_LABEL}={self._sandbox_id}"
        if budget is not None:
            pod = f"isolattice-{self._sandbox_id}"
            self._pod_name = pod
            await _invoke(
                self._engine,
                "pod creation",
                "pod",
                "create",
                "--name",
                pod,
                "--label",
                owner,
                "--infra=false",
                "--share=",
                *budget.arguments(),
            )
            data = await self._owned("pod", pod)
            if data is None:
                raise SandboxBackendError("Podman did not create the instance pod")
            cgroup = data.get("CgroupPath")
            if (
                data.get("CreateInfra") is not False
                or not isinstance(cgroup, str)
                or not cgroup
                or not budget.matches(data)
            ):
                raise SandboxCapabilityError("Podman did not apply the instance budget to the pod")
            self._pod = (_identifier(data.get("Id"), "pod inspection"), cgroup)
        if workspace:
            volume = f"isolattice-{self._sandbox_id}-workspace"
            self._volume = volume
            await _invoke(
                self._engine, "volume creation", "volume", "create", "--label", owner, volume
            )
            data = await self._owned("volume", volume)
            if data is None:
                raise SandboxBackendError("Podman did not create the workspace volume")
            if data.get("Driver") != "local" or data.get("Options") not in (None, {}):
                raise SandboxCapabilityError(
                    "Podman created the workspace volume with unexpected driver options"
                )
            self._volume_ready = True

    @override
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._volume_ready = False
        failures: list[BaseException] = []
        for kind, name in (("pod", self._pod_name), ("volume", self._volume)):
            if name is None:
                continue
            try:
                if await self._owned(kind, name) is None:
                    continue
                # Without --force, removal fails rather than deleting live executions.
                await _invoke(self._engine, f"{kind} removal", kind, "rm", name)
                if await self._owned(kind, name) is not None:
                    raise TerminationVerificationError(f"Podman {kind} {name} still exists")
            except BaseException as error:
                failures.append(error)
        if self._import_attempted:
            try:
                await self._remove_imported_images()
            except BaseException as error:
                failures.append(error)
        raise_combined("Podman instance resource cleanup failed", *failures)

    def _instance_arguments(self) -> tuple[str, ...]:
        args: list[str] = (
            ["--log-driver", "k8s-file", "--log-opt", f"max-size={_LOG_MAX_SIZE}"]
            if self._checkpoints
            else ["--log-driver", "none"]
        )
        if self._pod is not None:
            args.extend(("--pod", self._pod[0]))
        if self._scratch_bytes is not None:
            args.extend(
                (
                    "--tmpfs",
                    f"/tmp:rw,size={self._scratch_bytes},mode=1777,nosuid,nodev,notmpcopyup",
                )
            )
        if self._volume_ready:
            args.extend(
                ("--mount", f"type=volume,source={self._volume},destination={_WORKSPACE},U=true")
            )
        return tuple(args)

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        name = f"isolattice-{sandbox_id}-{execution_id}"
        env_file = _environment_file(request.environment)
        boundary: Boundary | None = None
        failure: BaseException | None = None
        try:
            command = self._limits.command(request, execution_id)
            args = [
                "create",
                "--name",
                name,
                "--label",
                f"{_OWNER_LABEL}={sandbox_id}",
                "--label",
                f"{_EXECUTION_LABEL}={execution_id}",
                "--network",
                "none",
                "--read-only" if self._read_only_root else "--read-only=false",
                "--read-only-tmpfs=false",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "65534:65534",
                "--pid",
                "private",
                "--ipc",
                "private",
                "--uts",
                "private",
                "--cgroupns",
                "private",
                "--http-proxy=false",
                "--unsetenv-all",
                "--no-healthcheck",
                "--image-volume",
                "ignore",
                "--no-hosts",
                "--no-hostname",
                "--pull",
                "never",
                "--workdir",
                request.cwd,
                "--entrypoint",
                command[0],
            ]
            args.extend(self._limits.arguments())
            args.extend(self._instance_arguments())
            if env_file is not None:
                args.extend(("--env-file", str(env_file)))
            if request.stdin is not None:
                args.append("--interactive")
            args.extend((self._image_id, *command[1:]))
            try:
                result = await _invoke(self._engine, "container creation", *args)
                try:
                    identifier = _identifier(result.stdout.decode("ascii").strip(), "creation")
                except (UnicodeDecodeError, SandboxBackendError) as error:
                    raise CommandError(
                        "Podman returned an invalid container ID", possibly_accepted=True
                    ) from error
            except BaseException as error:
                # Once the client may have sent create, every failure leaves ownership uncertain.
                if isinstance(error, CommandError) and not error.possibly_accepted:
                    raise
                try:
                    candidate = await self._reconcile(name, sandbox_id, execution_id)
                except BaseException as reconciliation:
                    unknown = TerminationVerificationError(
                        f"Podman creation outcome is unknown for owned name {name}; "
                        "the configured engine could not be reconciled"
                    )
                    causes = BaseExceptionGroup(
                        "Podman creation and reconciliation failures", [error, reconciliation]
                    )
                    if isinstance(causes, Exception):
                        raise unknown from causes
                    raise BaseExceptionGroup(
                        "Podman creation was interrupted and ownership is unknown",
                        [error, reconciliation, unknown],
                    ) from None
                if candidate is not None:
                    register(candidate)
                    if not isinstance(error, Exception):
                        error.add_note("Podman allocated an owned container; cleanup is required")
                        raise
                    raise SandboxBackendError(
                        "Podman creation failed after allocation; cleanup is required"
                    ) from error
                unknown = TerminationVerificationError(
                    f"Podman creation outcome is unknown for owned name {name}; "
                    "reconcile this name with the configured engine"
                )
                if not isinstance(error, Exception):
                    raise BaseExceptionGroup(
                        "Podman creation was interrupted and ownership is unknown", [error, unknown]
                    ) from None
                raise unknown from error
            boundary = Boundary(identifier, name, sandbox_id, execution_id)
            register(boundary)
            await self._validate(boundary, request)
        except BaseException as error:
            failure = error
        cleanup_error: BaseException | None = None
        if env_file is not None:
            try:
                env_file.unlink()
            except OSError as error:
                cleanup_error = SandboxBackendError(
                    f"could not remove temporary environment file {env_file}: "
                    f"{type(error).__name__}"
                )
                cleanup_error.__cause__ = error
        raise_combined("container creation and environment cleanup failed", failure, cleanup_error)
        assert boundary is not None
        return boundary

    async def _reconcile(self, name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
        result = await _invoke(
            self._engine, "container reconciliation", "container", "exists", name, accepted=(0, 1)
        )
        if result.returncode == 1:
            return None
        inspect = await _invoke(
            self._engine,
            "container reconciliation",
            "container",
            "inspect",
            name,
            "--format",
            "{{json .}}",
        )
        data = _object(inspect.stdout, "container reconciliation")
        identifier = _identifier(data.get("Id"), "container reconciliation")
        candidate = Boundary(identifier, name, sandbox_id, execution_id)
        self._check_owner(data, candidate)
        return candidate

    @staticmethod
    def _check_owner(data: dict[str, object], boundary: Boundary) -> None:
        if _identifier(data.get("Id"), "container inspection") != boundary.boundary_id:
            raise SandboxBackendError("Podman returned a different container identity")
        config = _mapping(data.get("Config"), "container config")
        labels = _mapping(config.get("Labels"), "container labels")
        if boundary.restore_source is not None:
            # Restored containers keep the labels of the checkpointed execution; ownership
            # rests on the immutable ID returned by restore and the owned unique name.
            if data.get("Name") != boundary.name or not isinstance(labels.get(_OWNER_LABEL), str):
                raise SandboxBackendError("refusing to operate on an unowned restored container")
            return
        if (
            labels.get(_OWNER_LABEL) != boundary.sandbox_id
            or labels.get(_EXECUTION_LABEL) != boundary.execution_id
        ):
            raise SandboxBackendError("refusing to operate on a container without ownership labels")

    async def _inspect(self, boundary: Boundary) -> dict[str, object]:
        result = await _invoke(
            self._engine,
            "container inspection",
            "container",
            "inspect",
            boundary.boundary_id,
            "--format",
            "{{json .}}",
        )
        data = _object(result.stdout, "container inspection")
        self._check_owner(data, boundary)
        return data

    async def _validate(self, boundary: Boundary, request: ExecutionRequest | None) -> None:
        """Verify isolation and instance resources; ``request`` is ``None`` for restores."""
        data = await self._inspect(boundary)
        host = _mapping(data.get("HostConfig"), "host config")
        config = _mapping(data.get("Config"), "container config")
        expected_resources: dict[str, int] = {}
        if self._limits.memory_bytes is not None:
            expected_resources.update(
                Memory=self._limits.memory_bytes, MemorySwap=self._limits.memory_bytes
            )
            if host.get("OomKillDisable") is not False:
                raise SandboxCapabilityError("Podman disabled resource-limit OOM enforcement")
        if self._limits.cpu_quota is not None:
            expected_resources.update(CpuPeriod=100000, CpuQuota=self._limits.cpu_quota)
        if self._limits.process_limit is not None:
            expected_resources["PidsLimit"] = self._limits.process_limit
        if any(
            type(host.get(key)) is not int or host[key] != value
            for key, value in expected_resources.items()
        ):
            raise SandboxCapabilityError("Podman did not apply the requested resource limits")
        expected = {
            "NetworkMode": "none",
            "PidMode": "private",
            "IpcMode": "private",
            "UTSMode": "private",
            "CgroupMode": "private",
        }
        if (
            any(host.get(key) != value for key, value in expected.items())
            or host.get("ReadonlyRootfs") is not self._read_only_root
            or host.get("Privileged") is not False
        ):
            raise SandboxCapabilityError("Podman did not apply the required container isolation")
        dropped = host.get("CapDrop")
        if (
            host.get("CapAdd") not in (None, [])
            or not isinstance(dropped, list)
            or not all(isinstance(capability, str) for capability in dropped)
        ):
            raise SandboxCapabilityError("Podman capability configuration is invalid")
        if not self._engine.baseline_capabilities.issubset(set(dropped)):
            raise SandboxCapabilityError("Podman did not drop its baseline capabilities")
        security = host.get("SecurityOpt")
        if not isinstance(security, list) or "no-new-privileges" not in security:
            raise SandboxCapabilityError("Podman did not enable no-new-privileges")
        self._validate_mounts(data, host, config)
        log_config = _mapping(host.get("LogConfig"), "logging config")
        restart = _mapping(host.get("RestartPolicy"), "restart policy")
        expected_log = "k8s-file" if self._checkpoints else "none"
        if log_config.get("Type") != expected_log or restart.get("Name") != "no":
            raise SandboxCapabilityError("Podman changed container logging or enabled restart")
        if self._pod is None:
            if data.get("Pod") not in (None, ""):
                raise SandboxCapabilityError("Podman placed the container in an unexpected pod")
        elif data.get("Pod") != self._pod[0] or host.get("CgroupParent") != self._pod[1]:
            raise SandboxCapabilityError("Podman did not place the container in the budget pod")
        if config.get("User") != "65534:65534":
            raise SandboxCapabilityError("Podman did not apply the unprivileged user")
        if data.get("Image") != self._image_id:
            raise SandboxCapabilityError("Podman did not use the requested immutable image")
        if request is None:
            return
        if config.get("WorkingDir") != request.cwd:
            raise SandboxCapabilityError("Podman did not apply the requested working directory")
        command = config.get("Cmd")
        planned_command = self._limits.command(request, boundary.execution_id)
        expected_command = list(planned_command[1:])
        if command is None:
            command = []
        healthcheck = _mapping(config.get("Healthcheck"), "container healthcheck")
        if (
            config.get("Entrypoint") != [planned_command[0]]
            or command != expected_command
            or config.get("Tty") is not False
            or config.get("OpenStdin") is not (request.stdin is not None)
            or healthcheck.get("Test") != ["NONE"]
        ):
            raise SandboxCapabilityError(
                "Podman changed the requested command or execution controls"
            )
        environment_value = config.get("Env")
        expected_environment = {f"{key}={value}" for key, value in request.environment.items()}
        environment: list[object]
        if environment_value is None:
            environment = []
        elif isinstance(environment_value, list):
            environment = environment_value
        else:
            raise SandboxCapabilityError("Podman returned an invalid container environment")
        if (
            not all(isinstance(entry, str) for entry in environment)
            or len(environment) != len(expected_environment)
            or set(environment) != expected_environment
        ):
            raise SandboxCapabilityError("Podman retained or changed the container environment")

    def _validate_mounts(
        self, data: dict[str, object], host: dict[str, object], config: dict[str, object]
    ) -> None:
        if host.get("Devices") not in (None, []) or config.get("Volumes") not in (None, {}):
            raise SandboxCapabilityError("Podman added an unexpected device or image volume")
        tmpfs = host.get("Tmpfs")
        if self._scratch_bytes is None:
            if tmpfs not in (None, {}):
                raise SandboxCapabilityError("Podman added an unexpected tmpfs mount")
        else:
            mounts = _mapping(tmpfs, "tmpfs mounts")
            options = mounts.get("/tmp")
            parts = set(options.split(",")) if isinstance(options, str) else set()
            if set(mounts) != {"/tmp"} or not {
                "rw",
                f"size={self._scratch_bytes}",
                "nosuid",
                "nodev",
            }.issubset(parts):
                raise SandboxCapabilityError("Podman did not apply the size-capped scratch tmpfs")
        binds = host.get("Binds")
        mounts_value = data.get("Mounts")
        if not self._volume_ready:
            if binds not in (None, []) or mounts_value not in (None, []):
                raise SandboxCapabilityError("Podman added an unexpected bind or volume mount")
            return
        prefix = f"{self._volume}:{_WORKSPACE}:"
        if (
            not isinstance(binds, list)
            or len(binds) != 1
            or not isinstance(binds[0], str)
            or not binds[0].startswith(prefix)
            or not {"nosuid", "nodev"}.issubset(binds[0][len(prefix) :].split(","))
            or not isinstance(mounts_value, list)
            or len(mounts_value) != 1
        ):
            raise SandboxCapabilityError("Podman did not mount exactly the owned workspace")
        mount = _mapping(mounts_value[0], "workspace mount")
        if (
            mount.get("Type") != "volume"
            or mount.get("Name") != self._volume
            or mount.get("Destination") != _WORKSPACE
            or mount.get("RW") is not True
        ):
            raise SandboxCapabilityError("Podman did not mount exactly the owned workspace")

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        if boundary.restore_source is not None:
            return await self._follow_restored(boundary)
        boundary.start_requested = True
        args = ["start", "--attach"]
        if request.stdin is not None:
            args.append("--interactive")
        args.append(boundary.boundary_id)
        process: asyncio.subprocess.Process | None = None
        failure: BaseException | None = None
        try:
            async with asyncio.timeout(self._engine.command_timeout):
                process = await self._engine.runner.spawn(
                    self._engine.command(*args), stdin=request.stdin is not None
                )
                if self._limits.controllers:
                    if process.stdout is None:
                        raise SandboxBackendError("Podman attach client has no output pipe")
                    marker = self._limits.marker(boundary.execution_id)
                    if await process.stdout.readexactly(len(marker)) != marker:
                        raise SandboxCapabilityError(
                            "Podman did not verify kernel resource limits before workload startup"
                        )
        except asyncio.IncompleteReadError as error:
            failure = SandboxCapabilityError(
                "Podman resource bootstrap ended before verifying kernel limits"
            )
            failure.__cause__ = error
        except OSError as error:
            failure = SandboxExecutionError(f"Podman attach startup failed: {type(error).__name__}")
            failure.__cause__ = error
        except BaseException as error:
            failure = error
        if failure is not None:
            cleanup = (
                await settle(asyncio.create_task(reap_client(process, _PRODUCT)))
                if process is not None
                else None
            )
            raise_combined(
                "Podman attach startup and cleanup failed",
                failure,
                cleanup.error if cleanup is not None else None,
                cleanup.interrupted if cleanup is not None else None,
            )
        assert process is not None
        return process

    @override
    async def workload_exited(self, boundary: Boundary) -> None:
        # The container stops with its init process, which closes the attach pipes.
        return None

    @override
    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        observation = await _PodmanDriver(self._engine, self, boundary).observe()
        if observation.state is not BoundaryState.EXITED or observation.exit_code is None:
            raise SandboxExecutionError(
                "Podman attach ended without an exited container"
                "; if the workload never started, check that argv[0] is an absolute path: "
                "the container environment holds only ExecutionRequest.environment"
            )
        if boundary.restore_source is not None:
            if client_exit_code != 0:
                raise SandboxExecutionError(
                    "Podman log streaming for the restored execution failed"
                )
            return observation.exit_code
        if client_exit_code != observation.exit_code:
            raise SandboxExecutionError(
                "Podman attach exited with a status different from the container"
            )
        return observation.exit_code

    async def _follow_restored(self, boundary: Boundary) -> asyncio.subprocess.Process:
        if boundary.boundary_id not in self._marks:
            raise SandboxExecutionError("the restored execution has no validated output mark")
        if not self._engine.helper_interpreter:
            raise SandboxExecutionError("restored output streaming needs a Python interpreter")
        mark = self._marks[boundary.boundary_id]
        try:
            return await self._engine.runner.spawn(
                (
                    self._engine.helper_interpreter,
                    "-I",
                    str(_LOG_FILTER),
                    "" if mark is None else str(mark),
                    *self._engine.command("logs", "--follow", "--timestamps", boundary.boundary_id),
                ),
                stdin=False,
            )
        except OSError as error:
            raise SandboxExecutionError(
                f"could not stream restored Podman output: {type(error).__name__}"
            ) from error

    def _require_checkpoints(self) -> None:
        if not self._checkpoints:
            raise SandboxCapabilityError(
                "Podman checkpoints require an instance whose policy requires CHECKPOINTS"
            )

    @override
    async def checkpoint(self, boundary: Boundary, destination: Path) -> None:
        self._require_checkpoints()
        observation = await _PodmanDriver(self._engine, self, boundary).observe()
        if observation.state is not BoundaryState.RUNNING:
            raise SandboxExecutionError("only a running Podman container can be checkpointed")
        await _invoke(
            self._engine,
            "container checkpoint",
            "container",
            "checkpoint",
            "--leave-running",
            "--export",
            str(destination),
            boundary.boundary_id,
            timeout=self._engine.transfer_timeout,
        )
        if not await asyncio.to_thread(destination.is_file):
            raise SandboxBackendError("Podman did not write the checkpoint archive")

    @override
    async def restore_boundary(
        self,
        source: Path,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        self._require_checkpoints()
        mark = await asyncio.to_thread(_inspect_checkpoint, source)
        name = f"isolattice-{sandbox_id}-{execution_id}"
        try:
            result = await _invoke(
                self._engine,
                "container restore",
                "container",
                "restore",
                "--import",
                str(source),
                "--name",
                name,
                timeout=self._engine.transfer_timeout,
            )
            try:
                lines = result.stdout.decode("ascii").split()
                identifier = _identifier(lines[-1] if lines else "", "restore")
            except (UnicodeDecodeError, SandboxBackendError) as error:
                raise CommandError(
                    "Podman returned an invalid restored container ID", possibly_accepted=True
                ) from error
        except BaseException as error:
            if isinstance(error, CommandError) and not error.possibly_accepted:
                raise
            candidate: Boundary | None = None
            reconciliation: BaseException | None = None
            try:
                candidate = await self._reconcile_restored(name, sandbox_id, execution_id, source)
            except BaseException as failure:
                reconciliation = failure
            if candidate is not None:
                register(candidate)
                error.add_note("Podman restored an owned container; cleanup is required")
            unknown = (
                TerminationVerificationError(
                    f"Podman restore outcome is unknown for owned name {name}; "
                    "reconcile this name with the configured engine"
                )
                if candidate is None
                else None
            )
            raise_with_cleanup("Podman restore failed", error, reconciliation, unknown)
        boundary = Boundary(
            identifier,
            name,
            sandbox_id,
            execution_id,
            start_requested=True,
            restore_source=source,
        )
        register(boundary)
        await self._validate(boundary, None)
        self._marks[identifier] = mark
        return boundary

    async def _reconcile_restored(
        self, name: str, sandbox_id: str, execution_id: str, source: Path
    ) -> Boundary | None:
        result = await _invoke(
            self._engine, "restore reconciliation", "container", "exists", name, accepted=(0, 1)
        )
        if result.returncode == 1:
            return None
        inspect = await _invoke(
            self._engine,
            "restore reconciliation",
            "container",
            "inspect",
            name,
            "--format",
            "{{json .}}",
        )
        data = _object(inspect.stdout, "restore reconciliation")
        candidate = Boundary(
            _identifier(data.get("Id"), "restore reconciliation"),
            name,
            sandbox_id,
            execution_id,
            start_requested=True,
            restore_source=source,
        )
        self._check_owner(data, candidate)
        return candidate

    async def _workspace_volume(self) -> str:
        if not self._volume_ready or self._volume is None:
            raise SandboxCapabilityError("this Podman instance has no workspace volume")
        if await self._owned("volume", self._volume) is None:
            raise SandboxBackendError("the owned Podman workspace volume no longer exists")
        return self._volume

    @override
    async def export_workspace(self, destination: Path) -> None:
        volume = await self._workspace_volume()
        await _invoke(
            self._engine,
            "workspace export",
            "volume",
            "export",
            volume,
            "--output",
            str(destination),
            timeout=self._engine.transfer_timeout,
        )

    @override
    async def import_workspace(self, source: Path) -> None:
        volume = await self._workspace_volume()
        await _invoke(
            self._engine,
            "workspace import",
            "volume",
            "import",
            volume,
            str(source),
            timeout=self._engine.transfer_timeout,
        )

    @override
    async def snapshot_boundary(self, boundary: Boundary, destination: Path) -> None:
        data = await self._inspect(boundary)
        if _mapping(data.get("State"), "container state").get("Status") != "running":
            raise SandboxExecutionError("only a running Podman container can be snapshotted")
        await _invoke(self._engine, "container pause", "pause", boundary.boundary_id)
        failure: BaseException | None = None
        try:
            data = await self._inspect(boundary)
            if _mapping(data.get("State"), "container state").get("Status") != "paused":
                raise SandboxBackendError("Podman did not pause the container for its snapshot")
            await _invoke(
                self._engine,
                "container export",
                "export",
                "--output",
                str(destination),
                boundary.boundary_id,
                timeout=self._engine.transfer_timeout,
            )
            if not await asyncio.to_thread(destination.is_file):
                raise SandboxBackendError("Podman did not write the container snapshot")
        except BaseException as error:
            failure = error
        resume = await settle(asyncio.create_task(self._resume(boundary)))
        raise_combined(
            "Podman container snapshot and resume failed",
            failure,
            resume.error,
            resume.interrupted,
        )

    async def _resume(self, boundary: Boundary) -> None:
        try:
            await _invoke(self._engine, "container unpause", "unpause", boundary.boundary_id)
        except SandboxBackendError:
            # A concurrent stop may already have ended the paused workload.
            if not await _PodmanDriver(self._engine, self, boundary).exists():
                return
            state = _mapping((await self._inspect(boundary)).get("State"), "container state")
            if state.get("Status") == "paused":
                raise

    @override
    async def import_root(self, source: Path) -> None:
        if self._sandbox_id is None or self._import_attempted:
            raise SandboxBackendError("this Podman session cannot import another root snapshot")
        self._import_attempted = True
        result = await _invoke(
            self._engine,
            "root snapshot import",
            "import",
            "--quiet",
            "--change",
            f"LABEL={_OWNER_LABEL}={self._sandbox_id}",
            str(source),
            timeout=self._engine.transfer_timeout,
        )
        lines = result.stdout.decode("ascii", "replace").split()
        identifier = _identifier(
            lines[-1].removeprefix("sha256:") if lines else "", "root snapshot import"
        )
        inspect = await _invoke(
            self._engine,
            "imported image inspection",
            "image",
            "inspect",
            identifier,
            "--format",
            "{{json .}}",
        )
        data = _object(inspect.stdout, "imported image inspection")
        labels = _mapping(data.get("Labels"), "imported image labels")
        if data.get("Id") != identifier or labels.get(_OWNER_LABEL) != self._sandbox_id:
            raise SandboxBackendError("Podman did not create an owned image from the snapshot")
        self._image_id = identifier

    async def _owned_images(self) -> list[str]:
        result = await _invoke(
            self._engine,
            "owned image listing",
            "image",
            "ls",
            "--all",
            "--no-trunc",
            "--quiet",
            "--filter",
            f"label={_OWNER_LABEL}={self._sandbox_id}",
        )
        return [
            _identifier(line.removeprefix("sha256:"), "owned image listing")
            for line in result.stdout.decode("ascii", "replace").split()
        ]

    async def _remove_imported_images(self) -> None:
        # Listing by owner label also finds an image from an import that failed late.
        for identifier in await self._owned_images():
            # Without --force, removal fails rather than deleting an image still in use.
            await _invoke(self._engine, "owned image removal", "image", "rm", identifier)
        if await self._owned_images():
            raise TerminationVerificationError("an owned Podman snapshot image still exists")

    @override
    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        return await terminate_boundary(
            _PodmanDriver(self._engine, self, boundary), policy, force_requested
        )


class _PodmanDriver(TerminationDriver):
    signal_releases = False

    def __init__(self, engine: _Engine, session: _PodmanSession, boundary: Boundary) -> None:
        self._engine = engine
        self._session = session
        self._boundary = boundary
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested

    @override
    async def observe(self) -> Observation:
        exists = await self.exists()
        if not exists:
            return Observation(BoundaryState.ABSENT)
        data = await self._session._inspect(self._boundary)
        state = _mapping(data.get("State"), "container state")
        status = state.get("Status")
        if status == "created" and state.get("Running") is False and state.get("Pid") == 0:
            return Observation(BoundaryState.CREATED)
        if status in ("running", "paused", "stopping") or state.get("Running") is True:
            return Observation(BoundaryState.RUNNING)
        if status == "exited" and state.get("Running") is False and state.get("Pid") == 0:
            code = state.get("ExitCode")
            if type(code) is int:
                return Observation(BoundaryState.EXITED, code)
        return Observation(
            BoundaryState.UNKNOWN, detail="Podman returned an uncertain container state"
        )

    @override
    async def signal(self, *, force: bool) -> None:
        await self._session._inspect(self._boundary)
        await _invoke(
            self._engine,
            "container signal",
            "kill",
            "--signal",
            "KILL" if force else "TERM",
            self.boundary_id,
        )

    @override
    async def remove(self) -> None:
        await self._session._inspect(self._boundary)
        await _invoke(self._engine, "container removal", "rm", self.boundary_id)

    @override
    async def exists(self) -> bool:
        result = await _invoke(
            self._engine,
            "container existence check",
            "container",
            "exists",
            self.boundary_id,
            accepted=(0, 1),
        )
        return result.returncode == 0
