"""Explicit Docker Engine API execution with inspected and kernel-verified isolation."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, override

from .._async import raise_combined, settle
from ..backend import Backend, BackendSession, Boundary
from ..commands import CommandRunner, SubprocessRunner
from ..errors import (
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
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
    _positive_number,
)
from ..termination import BoundaryState, Observation, TerminationDriver, terminate_boundary
from . import _bootstrap
from ._cli import CommandError, json_object, mapping, reap_client, run_command, string_list

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_PRODUCT = "Docker"
_ID = re.compile(r"[0-9a-f]{64}\Z")
_OWNER_LABEL = "io.isolattice.sandbox"
_EXECUTION_LABEL = "io.isolattice.execution"
_USER = 65534
_NO_NEW_PRIVILEGES = frozenset({"no-new-privileges", "no-new-privileges:true"})
# Podman's Docker-compatible API injects this marker into every container environment.
_PODMAN_MARKER = "container=podman"
_EXIT_SETTLE_SECONDS = 5.0
_EXIT_SETTLE_INTERVAL = 0.05


_DEFAULT_RUNNER = SubprocessRunner()


@dataclass(frozen=True, slots=True)
class _Engine:
    executable: str
    host: str
    command_timeout: float
    image_id: str
    image_environment: tuple[str, ...]
    rootless: bool
    cgroup_v2: bool
    server: str
    runner: CommandRunner = _DEFAULT_RUNNER

    def command(self, *args: str) -> tuple[str, ...]:
        return (self.executable, f"--host={self.host}", *args)

    @property
    def podman(self) -> bool:
        return self.server.startswith("Podman Engine")


async def _invoke(
    engine: _Engine, operation: str, *args: str, accepted: tuple[int, ...] = (0,)
) -> tuple[bytes, int]:
    result = await run_command(
        engine.runner,
        engine.command(*args),
        product=_PRODUCT,
        operation=operation,
        timeout=engine.command_timeout,
        accepted=accepted,
    )
    return result.stdout, result.returncode


def _identifier(value: object, context: str) -> str:
    if isinstance(value, str):
        value = value.removeprefix("sha256:")
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise SandboxBackendError(f"Docker {context} did not return a full immutable ID")
    return value


def _cpu_quota(cores: float) -> int:
    exact = Decimal(str(cores)) * 100000
    if exact != exact.to_integral_value() or not 1000 <= exact < 2**63:
        raise SandboxCapabilityError(
            "Docker CPU budget must be exactly representable in 100 ms periods "
            "with a quota between 1000 and 2^63-1 microseconds"
        )
    return int(exact)


@dataclass(frozen=True, slots=True)
class _Limits:
    memory_bytes: int | None = None
    cpu_quota: int | None = None
    process_limit: int | None = None

    @classmethod
    def from_policy(cls, policy: SandboxPolicy) -> _Limits:
        memory = policy.memory_bytes
        if memory is not None and not 6 * 1024 * 1024 <= memory < 2**63:
            raise SandboxCapabilityError("Docker memory budget must be 6 MiB to 2^63-1")
        if policy.process_limit is not None and policy.process_limit >= 2**63:
            raise SandboxCapabilityError("Docker process budget must be less than 2^63")
        quota = _cpu_quota(policy.cpu_cores) if policy.cpu_cores is not None else None
        return cls(memory, quota, policy.process_limit)

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

    def checks(self) -> _bootstrap.KernelChecks:
        return _bootstrap.KernelChecks(
            uid=_USER,
            gid=_USER,
            memory_bytes=self.memory_bytes,
            cpu_quota=self.cpu_quota,
            process_limit=self.process_limit,
        )


def _report(
    available: bool, error: str | None = None, *, engine: _Engine | None = None
) -> BackendReport:
    limited = CapabilityState.LIMITED if available else CapabilityState.UNSUPPORTED
    cgroup = available and engine is not None and engine.cgroup_v2
    unsupported = CapabilityState.UNSUPPORTED
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: CapabilityEvidence(
            limited,
            "read-only image root without bind mounts or volumes; engines may add writable "
            "tmpfs mounts such as /dev/shm (and /tmp and /var/tmp on Podman)",
        ),
        Capability.NETWORK_ISOLATION: CapabilityEvidence(
            limited, "engine network mode none; only a loopback interface is created"
        ),
        Capability.PROCESS_ISOLATION: CapabilityEvidence(
            limited,
            "private PID namespace; zero capabilities, no-new-privileges, seccomp filter mode "
            "and the unprivileged UID are read from /proc/self/status before exec",
        ),
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            limited, "engine-reported container state and owned container removal"
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            limited,
            "the image environment is retained because the Docker API cannot unset it; "
            "the inspected environment must equal image plus requested values",
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            limited if cgroup else unsupported,
            "per-execution cgroup v2 limits verified from inside the workload cgroup",
        ),
        Capability.SNAPSHOTS: CapabilityEvidence(unsupported, "not implemented for Docker"),
        Capability.CHECKPOINTS: CapabilityEvidence(unsupported, "not implemented for Docker"),
        Capability.INSTANCE_LIMITS: CapabilityEvidence(
            unsupported, "Docker has no aggregate cgroup shared by several containers"
        ),
        Capability.DISK_LIMIT: CapabilityEvidence(
            limited,
            "size-capped tmpfs at /tmp is inspected, but engines may add other writable "
            "tmpfs mounts that only the memory limit bounds",
        ),
        Capability.WORKSPACE: CapabilityEvidence(unsupported, "not implemented for Docker"),
    }
    for capability, detail in (
        (Capability.MEMORY_LIMIT, "cgroup memory.max with swap disabled"),
        (Capability.CPU_LIMIT, "cgroup cpu.max bandwidth quota over 100 ms periods"),
        (Capability.PROCESS_LIMIT, "cgroup pids.max counts descendant tasks and threads"),
    ):
        capabilities[capability] = CapabilityEvidence(
            CapabilityState.SUPPORTED if cgroup else unsupported,
            f"{detail}; read from the workload cgroup before exec, failing closed"
            if cgroup
            else "requires an engine reporting cgroup v2",
        )
    return BackendReport(
        name="docker",
        available=available,
        capabilities=capabilities,
        limitations=(
            "Containers share the host kernel; kernel or engine exploits are out of scope.",
            "Each execution uses a separate container; nothing persists between executions.",
            "The engine, image, host kernel and configured endpoint are trusted.",
            "The image must provide a POSIX /bin/sh for the verifying prelude.",
            "Environment values and command arguments are visible to the engine owner.",
            f"Docker API served by {engine.server}; "
            f"{'rootless' if engine.rootless else 'rootful'} engine."
            if engine is not None
            else "Engine identity has not been established.",
        ),
        error=error,
    )


def _environment_file(environment: Mapping[str, str]) -> Path | None:
    if not environment:
        return None
    path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix="isolattice-env-", text=False)
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
                        SandboxBackendError("could not write Docker environment file"),
                        SandboxBackendError(f"could not remove environment file: {cleanup}"),
                    ],
                ) from error
        raise SandboxBackendError("could not write Docker environment file") from error
    return path


class DockerBackend(Backend):
    """Use one explicit Docker API endpoint; rootful engines require explicit opt-in.

    ``host`` is passed to the Docker CLI as ``--host`` (for example
    ``unix:///run/user/1000/docker.sock`` or ``npipe:////./pipe/docker_engine``),
    so ambient ``DOCKER_HOST`` and CLI contexts are never consulted.
    """

    def __init__(
        self,
        *,
        host: str,
        image: str,
        executable: str | None = None,
        command_timeout: float = 15.0,
        allow_rootful: bool = False,
        runner: CommandRunner | None = None,
    ) -> None:
        for name, value in (("host", host), ("image", image)):
            if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value:
                raise ValueError(f"{name} must be a nonempty explicit value")
        if executable is not None and (not isinstance(executable, str) or not executable):
            raise ValueError("executable must be a nonempty path or command")
        _positive_number(command_timeout, "command_timeout")
        if type(allow_rootful) is not bool:
            raise TypeError("allow_rootful must be a bool")
        self._host = host
        self._image = image
        self._executable = executable
        self._command_timeout = command_timeout
        self._allow_rootful = allow_rootful
        if runner is not None and not isinstance(runner, CommandRunner):
            raise TypeError("runner must be a CommandRunner")
        self._runner = runner if runner is not None else _DEFAULT_RUNNER

    async def _probe(self) -> _Engine:
        executable = self._executable or shutil.which("docker")
        if executable is None:
            raise SandboxBackendError("Docker executable was not found")
        provisional = _Engine(
            executable, self._host, self._command_timeout, "", (), True, False, "", self._runner
        )
        stdout, _ = await _invoke(
            provisional, "server discovery", "version", "--format", "{{json .Server}}"
        )
        server = json_object(stdout, _PRODUCT, "server discovery")
        components = server.get("Components")
        names = [
            f"{item.get('Name')} {item.get('Version')}"
            for item in (components if isinstance(components, list) else [])
            if isinstance(item, dict) and isinstance(item.get("Name"), str)
        ]
        identity = next((name for name in names if name.startswith("Podman Engine")), None)
        if identity is None:
            version = server.get("Version")
            if not isinstance(version, str):
                raise SandboxBackendError("Docker did not report its server version")
            identity = f"Docker Engine {version}"
        stdout, _ = await _invoke(
            provisional, "security discovery", "info", "--format", "{{json .}}"
        )
        info = json_object(stdout, _PRODUCT, "security discovery")
        options = string_list(info.get("SecurityOptions"))
        if options is None:
            raise SandboxBackendError("Docker did not report valid security options")
        names_only = {option.split(",", 1)[0] for option in options}
        rootless = "name=rootless" in names_only
        if not rootless and not self._allow_rootful:
            raise SandboxCapabilityError(
                "the explicitly configured Docker endpoint is not rootless; "
                "pass allow_rootful=True to accept a rootful engine"
            )
        if "name=seccomp" not in names_only:
            raise SandboxCapabilityError("the configured Docker engine does not report seccomp")
        stdout, _ = await _invoke(
            provisional,
            "local image discovery",
            "image",
            "inspect",
            "--format",
            "{{json .}}",
            "--",
            self._image,
        )
        image = json_object(stdout, _PRODUCT, "local image discovery")
        image_id = _identifier(image.get("Id"), "image discovery")
        config = mapping(image.get("Config"), _PRODUCT, "image config")
        environment = string_list(config.get("Env"))
        if environment is None:
            raise SandboxBackendError("Docker returned an invalid image environment")
        return _Engine(
            executable,
            self._host,
            self._command_timeout,
            image_id,
            tuple(environment),
            rootless,
            info.get("CgroupVersion") == "2",
            identity,
            self._runner,
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
        if not policy.read_only_root:
            raise SandboxCapabilityError("a writable container root is not implemented")
        if policy.workspace:
            raise SandboxCapabilityError("Docker workspaces are not implemented")
        if policy.instance_budget is not None:
            raise SandboxCapabilityError("Docker instance budgets are not implemented")
        limits = _Limits.from_policy(policy)
        budgets = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.cpu_quota,
            Capability.PROCESS_LIMIT: limits.process_limit,
        }
        for capability in policy.required_capabilities:
            if capability in budgets and budgets[capability] is None:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} needs an explicit budget"
                )
        engine = await self._probe()
        report = _report(True, engine=engine)
        for capability in policy.required_capabilities:
            evidence = report.capabilities[capability]
            if evidence.state is not CapabilityState.SUPPORTED:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} is {evidence.state.value}: "
                    f"{evidence.detail}"
                )
        if any(value is not None for value in budgets.values()) and not engine.cgroup_v2:
            raise SandboxCapabilityError("Docker resource limits require cgroup v2")
        if policy.scratch_inodes is not None and engine.podman:
            raise SandboxCapabilityError(
                "Podman's Docker-compatible API cannot cap the scratch tmpfs inode count"
            )
        return _DockerSession(engine, limits, policy.scratch_bytes, policy.scratch_inodes)


class _DockerSession(BackendSession):
    backend_name = "docker"

    def __init__(
        self,
        engine: _Engine,
        limits: _Limits,
        scratch_bytes: int | None,
        scratch_inodes: int | None = None,
    ) -> None:
        self._engine = engine
        self._limits = limits
        self._scratch_bytes = scratch_bytes
        self._scratch_inodes = scratch_inodes

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
            command = _bootstrap.command(self._limits.checks(), request.argv, execution_id)
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
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                f"{_USER}:{_USER}",
                "--ipc",
                "private",
                "--cgroupns",
                "private",
                "--log-driver",
                "none",
                "--restart",
                "no",
                "--no-healthcheck",
                "--pull",
                "never",
                "--workdir",
                request.cwd,
                "--entrypoint",
                command[0],
                *self._limits.arguments(),
            ]
            if self._scratch_bytes is not None:
                inodes = (
                    f",nr_inodes={self._scratch_inodes}" if self._scratch_inodes is not None else ""
                )
                args.extend(
                    (
                        "--tmpfs",
                        f"/tmp:rw,size={self._scratch_bytes}{inodes},mode=1777,nosuid,nodev",
                    )
                )
            if env_file is not None:
                args.extend(("--env-file", str(env_file)))
            if request.stdin is not None:
                args.append("--interactive")
            args.extend((f"sha256:{self._engine.image_id}", *command[1:]))
            try:
                stdout, _ = await _invoke(self._engine, "container creation", *args)
                try:
                    identifier = _identifier(stdout.decode("ascii").strip(), "creation")
                except (UnicodeDecodeError, SandboxBackendError) as error:
                    raise CommandError(
                        "Docker returned an invalid container ID", possibly_accepted=True
                    ) from error
            except BaseException as error:
                if isinstance(error, CommandError) and not error.possibly_accepted:
                    raise
                await self._reconcile_after(error, name, sandbox_id, execution_id, register)
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

    async def _reconcile_after(
        self,
        error: BaseException,
        name: str,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> NoReturn:
        """Register any container the uncertain create allocated, then re-raise."""
        try:
            candidate = await self._reconcile(name, sandbox_id, execution_id)
        except BaseException as reconciliation:
            unknown = TerminationVerificationError(
                f"Docker creation outcome is unknown for owned name {name}; "
                "the configured engine could not be reconciled"
            )
            causes = BaseExceptionGroup(
                "Docker creation and reconciliation failures", [error, reconciliation]
            )
            if isinstance(causes, Exception):
                raise unknown from causes
            raise BaseExceptionGroup(
                "Docker creation was interrupted and ownership is unknown",
                [error, reconciliation, unknown],
            ) from None
        if candidate is not None:
            register(candidate)
            if not isinstance(error, Exception):
                error.add_note("Docker allocated an owned container; cleanup is required")
                raise error
            raise SandboxBackendError(
                "Docker creation failed after allocation; cleanup is required"
            ) from error
        unknown = TerminationVerificationError(
            f"Docker creation outcome is unknown for owned name {name}; "
            "reconcile this name with the configured engine"
        )
        if not isinstance(error, Exception):
            raise BaseExceptionGroup(
                "Docker creation was interrupted and ownership is unknown", [error, unknown]
            ) from None
        raise unknown from error

    async def _reconcile(self, name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
        stdout, _ = await _invoke(
            self._engine,
            "container reconciliation",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"label={_EXECUTION_LABEL}={execution_id}",
            "--format",
            "{{.ID}}",
        )
        candidates: list[Boundary] = []
        for line in stdout.decode("ascii", "replace").split():
            candidate = Boundary(
                _identifier(line, "container reconciliation"), name, sandbox_id, execution_id
            )
            data = await self._inspect(candidate)
            if data.get("Name") in (name, f"/{name}"):
                candidates.append(candidate)
        if len(candidates) > 1:
            raise SandboxBackendError("Docker reported several containers for one owned name")
        return candidates[0] if candidates else None

    async def _inspect(self, boundary: Boundary) -> dict[str, object]:
        stdout, _ = await _invoke(
            self._engine,
            "container inspection",
            "container",
            "inspect",
            "--format",
            "{{json .}}",
            boundary.boundary_id,
        )
        data = json_object(stdout, _PRODUCT, "container inspection")
        if _identifier(data.get("Id"), "container inspection") != boundary.boundary_id:
            raise SandboxBackendError("Docker returned a different container identity")
        config = mapping(data.get("Config"), _PRODUCT, "container config")
        labels = mapping(config.get("Labels"), _PRODUCT, "container labels")
        if (
            labels.get(_OWNER_LABEL) != boundary.sandbox_id
            or labels.get(_EXECUTION_LABEL) != boundary.execution_id
        ):
            raise SandboxBackendError("refusing to operate on a container without ownership labels")
        return data

    async def _validate(self, boundary: Boundary, request: ExecutionRequest) -> None:
        data = await self._inspect(boundary)
        host = mapping(data.get("HostConfig"), _PRODUCT, "host config")
        config = mapping(data.get("Config"), _PRODUCT, "container config")
        if data.get("Name") not in (boundary.name, f"/{boundary.name}"):
            raise SandboxCapabilityError("Docker did not apply the owned container name")
        expected_resources: dict[str, int] = {}
        if self._limits.memory_bytes is not None:
            expected_resources.update(
                Memory=self._limits.memory_bytes, MemorySwap=self._limits.memory_bytes
            )
            if host.get("OomKillDisable") not in (None, False):
                raise SandboxCapabilityError("Docker disabled resource-limit OOM enforcement")
        if self._limits.cpu_quota is not None:
            expected_resources.update(CpuPeriod=100000, CpuQuota=self._limits.cpu_quota)
        if self._limits.process_limit is not None:
            expected_resources["PidsLimit"] = self._limits.process_limit
        if any(
            type(host.get(key)) is not int or host[key] != value
            for key, value in expected_resources.items()
        ):
            raise SandboxCapabilityError("Docker did not apply the requested resource limits")
        security = string_list(host.get("SecurityOpt"))
        cap_add = string_list(host.get("CapAdd"))
        if (
            host.get("NetworkMode") != "none"
            or host.get("ReadonlyRootfs") is not True
            or host.get("Privileged") is not False
            or host.get("PidMode") not in ("", "private")
            or host.get("UTSMode") not in ("", "private")
            or host.get("IpcMode") != "private"
            or host.get("CgroupnsMode") != "private"
            or host.get("UsernsMode") not in (None, "")
            or host.get("AutoRemove") is True
            or cap_add != []
            or not security
            or not set(security) <= _NO_NEW_PRIVILEGES
        ):
            raise SandboxCapabilityError("Docker did not apply the required container isolation")
        self._validate_mounts(data, host, config)
        log_config = mapping(host.get("LogConfig"), _PRODUCT, "logging config")
        restart = mapping(host.get("RestartPolicy"), _PRODUCT, "restart policy")
        if log_config.get("Type") != "none" or restart.get("Name") not in ("", "no"):
            raise SandboxCapabilityError("Docker changed container logging or enabled restart")
        command = _bootstrap.command(self._limits.checks(), request.argv, boundary.execution_id)
        healthcheck = config.get("Healthcheck")
        if (
            config.get("User") != f"{_USER}:{_USER}"
            or _identifier(data.get("Image"), "container image") != self._engine.image_id
            or config.get("WorkingDir") != request.cwd
            or config.get("Entrypoint") != [command[0]]
            or (string_list(config.get("Cmd")) or []) != list(command[1:])
            or config.get("Tty") is not False
            or config.get("OpenStdin") is not (request.stdin is not None)
            or not isinstance(healthcheck, dict)
            or healthcheck.get("Test") != ["NONE"]
        ):
            raise SandboxCapabilityError(
                "Docker changed the requested image, command or execution controls"
            )
        expected = {
            entry.split("=", 1)[0]: entry
            for entry in self._engine.image_environment
            if "=" in entry
        }
        expected.update({key: f"{key}={value}" for key, value in request.environment.items()})
        environment = string_list(config.get("Env"))
        if environment is None:
            raise SandboxCapabilityError("Docker returned an invalid container environment")
        allowed = set(expected.values())
        if self._engine.podman and "container" not in expected:
            allowed.add(_PODMAN_MARKER)
        if len(environment) != len(set(environment)) or not (
            set(expected.values()) <= set(environment) <= allowed
        ):
            raise SandboxCapabilityError("Docker added or changed container environment values")

    def _validate_mounts(
        self, data: dict[str, object], host: dict[str, object], config: dict[str, object]
    ) -> None:
        if (
            string_list(host.get("Binds")) != []
            or host.get("Mounts") not in (None, [])
            or data.get("Mounts") not in (None, [])
            or host.get("Devices") not in (None, [])
            or host.get("VolumesFrom") not in (None, [])
            or config.get("Volumes") not in (None, {})
        ):
            raise SandboxCapabilityError("Docker added an unexpected mount, volume or device")
        tmpfs = host.get("Tmpfs")
        if self._scratch_bytes is None:
            if tmpfs not in (None, {}):
                raise SandboxCapabilityError("Docker added an unexpected tmpfs mount")
            return
        mounts = mapping(tmpfs, _PRODUCT, "tmpfs mounts")
        options = mounts.get("/tmp")
        parts = set(options.split(",")) if isinstance(options, str) else set()
        required = {"rw", f"size={self._scratch_bytes}", "nosuid", "nodev"}
        if self._scratch_inodes is not None:
            required.add(f"nr_inodes={self._scratch_inodes}")
        if set(mounts) != {"/tmp"} or not required.issubset(parts):
            raise SandboxCapabilityError("Docker did not apply the size-capped scratch tmpfs")

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
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
                if process.stdout is None:
                    raise SandboxBackendError("Docker attach client has no output pipe")
                expected = _bootstrap.marker(boundary.execution_id)
                if await process.stdout.readexactly(len(expected)) != expected:
                    raise SandboxCapabilityError(
                        "Docker workload prelude did not verify kernel isolation before exec"
                    )
        except asyncio.IncompleteReadError as error:
            failure = SandboxCapabilityError(
                "Docker workload prelude rejected the kernel-reported isolation or limits"
            )
            failure.__cause__ = error
        except OSError as error:
            failure = SandboxExecutionError(f"Docker attach startup failed: {type(error).__name__}")
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
                "Docker attach startup and cleanup failed",
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
        driver = _DockerDriver(self._engine, self, boundary)
        observation = await driver.observe()
        # The engine can record the exit slightly after attach returns.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _EXIT_SETTLE_SECONDS
        while (
            observation.state in (BoundaryState.RUNNING, BoundaryState.UNKNOWN)
            and loop.time() < deadline
        ):
            await asyncio.sleep(_EXIT_SETTLE_INTERVAL)
            observation = await driver.observe()
        if observation.state is not BoundaryState.EXITED or observation.exit_code is None:
            raise SandboxExecutionError(
                f"Docker attach ended without an exited container (observed "
                f"{observation.detail or observation.state.value})"
            )
        if client_exit_code != observation.exit_code:
            raise SandboxExecutionError(
                "Docker attach exited with a status different from the container"
            )
        return observation.exit_code

    @override
    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        return await terminate_boundary(
            _DockerDriver(self._engine, self, boundary), policy, force_requested
        )


class _DockerDriver(TerminationDriver):
    signal_releases = False

    def __init__(self, engine: _Engine, session: _DockerSession, boundary: Boundary) -> None:
        self._engine = engine
        self._session = session
        self._boundary = boundary
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested

    @override
    async def observe(self) -> Observation:
        if not await self.exists():
            return Observation(BoundaryState.ABSENT)
        data = await self._session._inspect(self._boundary)
        state = mapping(data.get("State"), _PRODUCT, "container state")
        status = state.get("Status")
        if status == "created" and state.get("Running") is False and state.get("Pid") == 0:
            return Observation(BoundaryState.CREATED)
        if status in ("running", "paused", "restarting", "removing") or state.get("Running"):
            return Observation(BoundaryState.RUNNING)
        if status == "exited" and state.get("Running") is False and state.get("Pid") == 0:
            code = state.get("ExitCode")
            if type(code) is int:
                return Observation(BoundaryState.EXITED, code)
        return Observation(
            BoundaryState.UNKNOWN,
            detail=f"Docker returned an uncertain container state ({str(status)[:40]})",
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
        # Without --force, removal fails rather than deleting a running container.
        await _invoke(self._engine, "container removal", "rm", self.boundary_id)

    @override
    async def exists(self) -> bool:
        stdout, _ = await _invoke(
            self._engine,
            "container existence check",
            "ps",
            "--all",
            "--no-trunc",
            "--filter",
            f"id={self.boundary_id}",
            "--format",
            "{{.ID}}",
        )
        lines = stdout.decode("ascii", "replace").split()
        if any(_identifier(line, "container listing") != self.boundary_id for line in lines):
            raise SandboxBackendError("Docker listed an unexpected container for an exact ID")
        return bool(lines)
