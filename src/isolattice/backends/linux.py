"""Native Linux backend built on namespaces and a delegated cgroup v2.

Each execution runs in fresh user, mount, PID, network, IPC, UTS and cgroup
namespaces, inside a read-only pivoted root that exposes only configured host
paths. The workload runs as uid 65534 in a nested user namespace, with
no_new_privs set, every capability dropped, Landlock rules and a seccomp deny
list. Its process tree is contained by an owned cgroup v2 leaf under a
caller-delegated subtree, where ``cgroup.kill`` and ``cgroup.events`` provide
verified termination and the controllers enforce budgets.

The backend needs no privileges. It does need unprivileged user namespaces,
Landlock, libseccomp and a delegated cgroup subtree that contains no processes,
for example one created inside ``systemd-run --user --scope -p Delegate=yes``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import errno
import json
import math
import os
import platform
import shutil
import signal
import sys
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, override

from ..backend import Backend, BackendSession, Boundary
from ..errors import SandboxBackendError, SandboxCapabilityError, SandboxExecutionError
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
)
from ..termination import BoundaryState, Observation, TerminationDriver, terminate_boundary

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

BACKEND_NAME = "linux-namespaces"
DEFAULT_READ_ONLY_PATHS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/etc",
)
_HELPER = Path(__file__).with_name("_linux_helper.py")
_ARCHITECTURES = ("x86_64", "aarch64")
_CONTROLLERS = ("cpu", "memory", "pids")
_CGROUP_MOUNT = Path("/sys/fs/cgroup")
_CPU_PERIOD = 100_000
_MIN_CPU_QUOTA = 1_000
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_MAX_STATUS_BYTES = 4096
_MAX_CONFIG_BYTES = 1024 * 1024
_SANDBOX_PATHS = ("/proc", "/dev", "/tmp", "/workspace")


def _landlock_abi() -> int:
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        version = int(libc.syscall(444, None, ctypes.c_size_t(0), ctypes.c_uint32(1)))
        return max(version, 0)
    return 0


def _seccomp_available() -> bool:
    try:
        ctypes.CDLL("libseccomp.so.2")
    except OSError:
        return False
    return True


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii")
    except OSError as error:
        raise SandboxBackendError(f"could not read {path.name}: {type(error).__name__}") from error


def _write(path: Path, value: str) -> None:
    try:
        path.write_text(value, encoding="ascii")
    except OSError as error:
        raise SandboxBackendError(
            f"could not write {path.name}: {errno.errorcode.get(error.errno or 0, 'error')}"
        ) from error


def _populated(cgroup: Path) -> bool:
    for line in _read(cgroup / "cgroup.events").splitlines():
        key, _, value = line.partition(" ")
        if key == "populated":
            return value.strip() == "1"
    raise SandboxBackendError("cgroup.events has no populated field")


def _cpu_max(cores: float) -> str:
    return f"{max(_MIN_CPU_QUOTA, math.ceil(cores * _CPU_PERIOD))} {_CPU_PERIOD}"


@dataclass(frozen=True, slots=True)
class _Limits:
    memory_bytes: int | None = None
    cpu_cores: float | None = None
    process_limit: int | None = None

    @classmethod
    def from_budget(cls, budget: ResourceBudget | SandboxPolicy) -> _Limits:
        return cls(budget.memory_bytes, budget.cpu_cores, budget.process_limit)

    def controllers(self) -> set[str]:
        needed = set()
        if self.memory_bytes is not None:
            needed.add("memory")
        if self.cpu_cores is not None:
            needed.add("cpu")
        if self.process_limit is not None:
            needed.add("pids")
        return needed

    def apply(self, cgroup: Path) -> None:
        if self.memory_bytes is not None:
            _write(cgroup / "memory.max", str(self.memory_bytes))
            swap = cgroup / "memory.swap.max"
            if swap.exists():
                _write(swap, "0")
        if self.cpu_cores is not None:
            _write(cgroup / "cpu.max", _cpu_max(self.cpu_cores))
        if self.process_limit is not None:
            _write(cgroup / "pids.max", str(self.process_limit))

    def verify(self, cgroup: Path) -> None:
        expected = {}
        if self.memory_bytes is not None:
            expected["memory.max"] = str(self.memory_bytes)
        if self.cpu_cores is not None:
            expected["cpu.max"] = _cpu_max(self.cpu_cores)
        if self.process_limit is not None:
            expected["pids.max"] = str(self.process_limit)
        for name, value in expected.items():
            if _read(cgroup / name).strip() != value:
                raise SandboxBackendError(f"the kernel did not apply {name}={value}")


@dataclass(frozen=True, slots=True)
class _Host:
    """Facts discovered about the host and the delegated cgroup subtree."""

    problem: str | None
    landlock_abi: int = 0
    controllers: frozenset[str] = frozenset()


def _inspect_host(cgroup_root: Path) -> _Host:
    if sys.platform == "linux":
        if platform.machine() not in _ARCHITECTURES:
            return _Host(f"unsupported architecture {platform.machine()}")
        if not hasattr(os, "unshare"):
            return _Host("os.unshare is unavailable")
        abi = _landlock_abi()
        if abi < 1:
            return _Host("Landlock is unavailable or disabled")
        if not _seccomp_available():
            return _Host("libseccomp.so.2 is unavailable")
        if not (_CGROUP_MOUNT / "cgroup.controllers").is_file():
            return _Host("a unified cgroup v2 hierarchy is required at /sys/fs/cgroup")
        try:
            status = cgroup_root.stat()
        except OSError:
            return _Host("cgroup_root does not exist")
        if not cgroup_root.is_relative_to(_CGROUP_MOUNT) or cgroup_root == _CGROUP_MOUNT:
            return _Host("cgroup_root must be a delegated cgroup below /sys/fs/cgroup")
        if status.st_uid != os.geteuid():
            return _Host("cgroup_root is not delegated to (owned by) the calling user")
        for name in ("cgroup.procs", "cgroup.subtree_control", "cgroup.kill"):
            path = cgroup_root / name
            if not path.is_file() or not os.access(path, os.W_OK):
                return _Host(f"cgroup_root/{name} is missing or not writable")
        try:
            if (cgroup_root / "cgroup.procs").read_text(encoding="ascii").strip():
                return _Host("cgroup_root contains processes; it must be a process-free subtree")
            available = frozenset(
                (cgroup_root / "cgroup.controllers").read_text(encoding="ascii").split()
            )
        except OSError as error:
            return _Host(f"cgroup_root is unreadable: {type(error).__name__}")
        return _Host(None, abi, available & frozenset(_CONTROLLERS))
    return _Host("Linux namespaces require Linux")


def _report(host: _Host, error: str | None = None) -> BackendReport:
    available = host.problem is None and error is None

    def state(supported: CapabilityState) -> CapabilityState:
        return supported if available else CapabilityState.UNSUPPORTED

    def controlled(controller: str, detail: str) -> CapabilityEvidence:
        if available and controller in host.controllers:
            return CapabilityEvidence(CapabilityState.SUPPORTED, detail)
        return CapabilityEvidence(
            CapabilityState.UNSUPPORTED, f"the {controller} controller is not delegated"
        )

    budgets = available and bool(host.controllers)
    network = "an empty network namespace with only loopback"
    if host.landlock_abi >= 4:
        network += "; Landlock denies TCP bind and connect"
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "private mount namespace with a read-only pivoted root exposing only the "
            "configured read-only host paths, private /proc, minimal /dev, a private /tmp "
            f"tmpfs and the optional workspace; Landlock ABI {host.landlock_abi} limits "
            "writes to /tmp and /workspace",
        ),
        Capability.NETWORK_ISOLATION: CapabilityEvidence(state(CapabilityState.SUPPORTED), network),
        Capability.PROCESS_ISOLATION: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "private PID, IPC, UTS and cgroup namespaces; uid 65534 in a nested user "
            "namespace with no capabilities, no_new_privs and a seccomp deny list",
        ),
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "owned cgroup v2 leaf; cgroup.kill and populated=0 are observed before the leaf "
            "is removed",
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "the host environment is not inherited; only explicit values and a default PATH",
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            state(CapabilityState.SUPPORTED) if budgets else CapabilityState.UNSUPPORTED,
            "delegated cgroup v2 controllers: " + (", ".join(sorted(host.controllers)) or "none"),
        ),
        Capability.MEMORY_LIMIT: controlled("memory", "memory.max with swap disabled"),
        Capability.CPU_LIMIT: controlled("cpu", "cpu.max quota over a 100 ms period"),
        Capability.PROCESS_LIMIT: controlled("pids", "pids.max over every task of the boundary"),
        Capability.SNAPSHOTS: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "cold tar snapshots of the instance workspace"
        ),
        Capability.CHECKPOINTS: CapabilityEvidence(
            CapabilityState.UNSUPPORTED,
            "CRIU needs privileges this unprivileged backend does not have",
        ),
        Capability.INSTANCE_LIMITS: (
            CapabilityEvidence(
                CapabilityState.SUPPORTED, "per-instance parent cgroup above every execution leaf"
            )
            if budgets
            else CapabilityEvidence(CapabilityState.UNSUPPORTED, "no delegated controllers")
        ),
        Capability.DISK_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "size-capped /tmp tmpfs charged to the memory cgroup; workspaces have no quota",
        ),
        Capability.WORKSPACE: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "private host directory bind-mounted at /workspace for every execution",
        ),
    }
    return BackendReport(
        name=BACKEND_NAME,
        available=available,
        capabilities=capabilities,
        limitations=(
            "Namespaces share the host kernel; kernel exploits are out of scope.",
            "Kernel attack surface is reduced by seccomp but shared with the host kernel.",
            "Supplementary groups of the calling user are retained (setgroups is denied).",
            "Configured read-only host paths are visible to workloads.",
            "Only network=NONE is supported.",
        ),
        error=host.problem or error,
    )


def _require_supported(policy: SandboxPolicy, report: BackendReport) -> None:
    for capability in policy.required_capabilities:
        evidence = report.capabilities[capability]
        if evidence.state is not CapabilityState.SUPPORTED:
            raise SandboxCapabilityError(
                f"required capability {capability.value} is {evidence.state.value}: "
                f"{evidence.detail}"
            )


async def _spawn_helper(
    interpreter: str,
    configuration: dict[str, object],
    *,
    stdin: int,
) -> tuple[asyncio.subprocess.Process, int]:
    payload = json.dumps({**configuration, "parent": os.getpid()}).encode()
    if len(payload) > _MAX_CONFIG_BYTES:
        raise SandboxExecutionError("the execution configuration is too large")
    if sys.platform == "linux":
        config_fd = os.memfd_create("isolattice-config", os.MFD_CLOEXEC)
        read_status, write_status = os.pipe()
        try:
            os.write(config_fd, payload)
            os.lseek(config_fd, 0, os.SEEK_SET)
            process = await asyncio.create_subprocess_exec(
                interpreter,
                "-I",
                "-S",
                str(_HELPER),
                str(config_fd),
                str(write_status),
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={},
                pass_fds=(config_fd, write_status),
            )
        except OSError as error:
            os.close(read_status)
            raise SandboxExecutionError(
                f"could not start the Linux launcher: {type(error).__name__}"
            ) from error
        finally:
            os.close(config_fd)
            os.close(write_status)
        return process, read_status
    raise SandboxExecutionError("the Linux namespace backend requires Linux")


def _interpreter(value: str | os.PathLike[str] | None) -> str:
    if value is None:
        if not sys.executable:
            raise ValueError("helper_interpreter is required when sys.executable is unknown")
        return sys.executable
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError("helper_interpreter must be a path")
    path = os.fspath(value)
    if not PurePosixPath(path).is_absolute() or "\0" in path:
        raise ValueError("helper_interpreter must be an absolute interpreter path")
    return path


async def _read_status(descriptor: int, timeout: float) -> bytes:  # noqa: ASYNC109
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=_MAX_STATUS_BYTES + 1)
    pipe = os.fdopen(descriptor, "rb", buffering=0)
    try:
        transport, _ = await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), pipe
        )
    except BaseException:
        pipe.close()
        raise
    try:
        async with asyncio.timeout(timeout):
            data = bytearray()
            while chunk := await reader.read(_MAX_STATUS_BYTES):
                data += chunk
                if len(data) > _MAX_STATUS_BYTES:
                    break
            return bytes(data[:_MAX_STATUS_BYTES])
    finally:
        transport.close()


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        async with asyncio.timeout(5):
            await process.wait()
    except TimeoutError as error:
        raise SandboxBackendError("the Linux launcher could not be reaped") from error


class LinuxNamespaceBackend(Backend):
    """Run workloads in unprivileged Linux namespaces under a delegated cgroup subtree."""

    def __init__(
        self,
        *,
        cgroup_root: str | os.PathLike[str],
        read_only_paths: Sequence[str] = DEFAULT_READ_ONLY_PATHS,
        state_directory: str | os.PathLike[str] | None = None,
        startup_timeout: float = 15.0,
        helper_interpreter: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(cgroup_root, (str, os.PathLike)):
            raise TypeError("cgroup_root must be a path")
        root = Path(cgroup_root)
        if not PurePosixPath(os.fspath(cgroup_root)).is_absolute():
            raise ValueError("cgroup_root must be absolute")
        if isinstance(read_only_paths, str):
            raise TypeError("read_only_paths must be a sequence of paths")
        paths: list[str] = []
        for item in read_only_paths:
            if not isinstance(item, str):
                raise TypeError("read_only_paths must contain strings")
            path = PurePosixPath(item)
            if not path.is_absolute() or ".." in path.parts or str(path) == "/":
                raise ValueError(f"read-only path {item!r} must be absolute and not /")
            if any(
                path == PurePosixPath(reserved) or path.is_relative_to(reserved)
                for reserved in _SANDBOX_PATHS
            ):
                raise ValueError(f"read-only path {item!r} overlaps a sandbox-managed path")
            paths.append(str(path))
        if state_directory is not None and not Path(state_directory).is_absolute():
            raise ValueError("state_directory must be absolute")
        if isinstance(startup_timeout, bool) or not isinstance(startup_timeout, (int, float)):
            raise TypeError("startup_timeout must be a number")
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError("startup_timeout must be finite and positive")
        self._cgroup_root = root
        self._read_only_paths = tuple(paths)
        self._state_directory = None if state_directory is None else Path(state_directory)
        self._startup_timeout = float(startup_timeout)
        self._helper_interpreter = _interpreter(helper_interpreter)

    def _exposed_paths(self) -> list[str]:
        return [path for path in self._read_only_paths if os.path.lexists(path)]

    @override
    async def discover(self) -> BackendReport:
        host = await asyncio.to_thread(_inspect_host, self._cgroup_root)
        if host.problem is not None:
            return _report(host)
        try:
            await self._probe(host)
        except (SandboxBackendError, SandboxCapabilityError, SandboxExecutionError) as error:
            return _report(host, str(error))
        return _report(host)

    async def _probe(self, host: _Host) -> None:
        """Run the whole hardening chain once without executing any workload."""
        leaf = self._cgroup_root / f"isolattice-probe-{uuid.uuid4().hex}"
        state = Path(tempfile.mkdtemp(prefix="isolattice-probe-", dir=self._state_directory))
        try:
            await asyncio.to_thread(leaf.mkdir)
            mount_point = state / "root"
            mount_point.mkdir()
            process, status = await _spawn_helper(
                self._helper_interpreter,
                {
                    "probe": True,
                    "cgroup": str(leaf),
                    "root": str(mount_point),
                    "read_only_paths": self._exposed_paths(),
                    "scratch_bytes": 1024 * 1024,
                    "workspace": None,
                    "cwd": "/",
                    "argv": [],
                    "environment": {},
                    "landlock_abi": host.landlock_abi,
                },
                stdin=asyncio.subprocess.DEVNULL,
            )
            try:
                message = await _read_status(status, self._startup_timeout)
                async with asyncio.timeout(self._startup_timeout):
                    code = await process.wait()
            finally:
                await _reap(process)
            if message or code != 0:
                detail = message.decode("utf-8", "replace") or f"exit status {code}"
                raise SandboxCapabilityError(f"the namespace probe failed: {detail}")
        except TimeoutError as error:
            raise SandboxCapabilityError("the namespace probe timed out") from error
        except OSError as error:
            raise SandboxCapabilityError(
                f"the namespace probe could not run: {type(error).__name__}"
            ) from error
        finally:
            await asyncio.to_thread(_remove_tree, state)
            if leaf.exists():
                await asyncio.to_thread(_kill_and_remove, leaf)

    @override
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        host = await asyncio.to_thread(_inspect_host, self._cgroup_root)
        if host.problem is not None:
            raise SandboxCapabilityError(host.problem)
        if policy.network is not NetworkAccess.NONE:
            raise SandboxCapabilityError("the Linux namespace backend supports only network=NONE")
        if not policy.read_only_root:
            raise SandboxCapabilityError("the Linux namespace backend always uses a read-only root")
        if policy.workspace and Capability.DISK_LIMIT in policy.required_capabilities:
            raise SandboxCapabilityError(
                "workspaces have no quota; DISK_LIMIT cannot be required with a workspace"
            )
        limits = _Limits.from_budget(policy)
        budget = (
            None if policy.instance_budget is None else _Limits.from_budget(policy.instance_budget)
        )
        explicit = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.cpu_cores,
            Capability.PROCESS_LIMIT: limits.process_limit,
        }
        for capability, value in explicit.items():
            if capability in policy.required_capabilities and value is None:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} needs an explicit budget"
                )
        report = _report(host)
        _require_supported(policy, report)
        needed = limits.controllers() | (set() if budget is None else budget.controllers())
        missing = needed - host.controllers
        if missing:
            raise SandboxCapabilityError(
                "cgroup controllers are not delegated: " + ", ".join(sorted(missing))
            )
        await self._probe(host)
        session = _LinuxSession(
            cgroup_root=self._cgroup_root,
            read_only_paths=tuple(self._exposed_paths()),
            state_directory=self._state_directory,
            limits=limits,
            scratch_bytes=policy.scratch_bytes,
            landlock_abi=host.landlock_abi,
            startup_timeout=self._startup_timeout,
            helper_interpreter=self._helper_interpreter,
        )
        try:
            await asyncio.to_thread(
                session.allocate, sandbox_id, host.controllers, budget, policy.workspace
            )
        except BaseException as error:
            try:
                await session.close()
            except BaseException as cleanup:
                raise SandboxBackendError(
                    f"instance allocation failed ({error}) and cleanup failed ({cleanup})"
                ) from error
            raise
        return session


def _remove_tree(path: Path) -> None:
    # Workloads own their workspace and may remove owner permissions; restore them first.
    if not path.exists():
        return
    path.chmod(0o700)
    for current, directories, _ in path.walk():
        for name in directories:
            child = current / name
            if not child.is_symlink():
                child.chmod(0o700)
    shutil.rmtree(path)


def _kill_and_remove(cgroup: Path) -> None:
    if not cgroup.exists():
        return
    if _populated(cgroup):
        _write(cgroup / "cgroup.kill", "1")
    for _ in range(50):
        if not _populated(cgroup):
            break
        time.sleep(0.05)
    try:
        cgroup.rmdir()
    except OSError as error:
        raise SandboxBackendError(
            f"could not remove cgroup {cgroup.name}: {type(error).__name__}"
        ) from error


class _LinuxSession(BackendSession):
    backend_name = BACKEND_NAME

    def __init__(
        self,
        *,
        cgroup_root: Path,
        read_only_paths: tuple[str, ...],
        state_directory: Path | None,
        limits: _Limits,
        scratch_bytes: int | None,
        landlock_abi: int,
        startup_timeout: float,
        helper_interpreter: str,
    ) -> None:
        self._cgroup_root = cgroup_root
        self._read_only_paths = read_only_paths
        self._state_parent = state_directory
        self._limits = limits
        self._scratch_bytes = scratch_bytes
        self._landlock_abi = landlock_abi
        self._startup_timeout = startup_timeout
        self._helper_interpreter = helper_interpreter
        self._instance: Path | None = None
        self._state: Path | None = None
        self._workspace: Path | None = None
        self._leaves: dict[str, Path] = {}
        self._descendants_terminated: set[str] = set()

    @property
    @override
    def workspace_path(self) -> str | None:
        return None if self._workspace is None else "/workspace"

    def allocate(
        self,
        sandbox_id: str,
        controllers: frozenset[str],
        budget: _Limits | None,
        workspace: bool,
    ) -> None:
        self._state = Path(
            tempfile.mkdtemp(prefix=f"isolattice-{sandbox_id[:12]}-", dir=self._state_parent)
        )
        (self._state / "root").mkdir()
        if workspace:
            self._workspace = self._state / "workspace"
            self._workspace.mkdir(mode=0o700)
        enabled = set(_read(self._cgroup_root / "cgroup.subtree_control").split())
        wanted = sorted(controllers - enabled)
        if wanted:
            _write(
                self._cgroup_root / "cgroup.subtree_control",
                " ".join(f"+{name}" for name in wanted),
            )
        instance = self._cgroup_root / f"isolattice-{sandbox_id}"
        instance.mkdir()
        self._instance = instance
        if controllers:
            _write(
                instance / "cgroup.subtree_control",
                " ".join(f"+{name}" for name in sorted(controllers)),
            )
        if budget is not None:
            budget.apply(instance)
            budget.verify(instance)

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        if self._instance is None:
            raise SandboxBackendError("the Linux instance is not allocated")
        boundary = Boundary(
            uuid.uuid4().hex, f"isolattice-{execution_id}", sandbox_id, execution_id
        )
        leaf = self._instance / f"x-{execution_id}"

        def allocate() -> None:
            leaf.mkdir()
            try:
                self._limits.apply(leaf)
                self._limits.verify(leaf)
            except BaseException:
                leaf.rmdir()
                raise

        await asyncio.to_thread(allocate)
        self._leaves[boundary.boundary_id] = leaf
        register(boundary)
        return boundary

    def _configuration(self, leaf: Path, request: ExecutionRequest) -> dict[str, object]:
        if self._state is None:
            raise SandboxBackendError("the Linux instance is not allocated")
        environment = dict(request.environment)
        environment.setdefault("PATH", _DEFAULT_PATH)
        return {
            "probe": False,
            "cgroup": str(leaf),
            "root": str(self._state / "root"),
            "read_only_paths": list(self._read_only_paths),
            "scratch_bytes": self._scratch_bytes,
            "workspace": None if self._workspace is None else str(self._workspace),
            "cwd": request.cwd,
            "argv": list(request.argv),
            "environment": environment,
            "landlock_abi": self._landlock_abi,
        }

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        leaf = self._leaves.get(boundary.boundary_id)
        if leaf is None:
            raise SandboxBackendError("the boundary is not owned by this Linux session")
        boundary.start_requested = True
        process, status = await _spawn_helper(
            self._helper_interpreter,
            self._configuration(leaf, request),
            stdin=asyncio.subprocess.PIPE
            if request.stdin is not None
            else asyncio.subprocess.DEVNULL,
        )
        try:
            message = await _read_status(status, self._startup_timeout)
        except BaseException:
            await _reap(process)
            raise
        if message:
            await _reap(process)
            raise SandboxExecutionError(message.decode("utf-8", "replace"))
        return process

    @override
    async def workload_exited(self, boundary: Boundary) -> None:
        leaf = self._leaves.get(boundary.boundary_id)
        if leaf is not None and leaf.exists() and await asyncio.to_thread(_populated, leaf):
            await asyncio.to_thread(_write, leaf / "cgroup.kill", "1")
            self._descendants_terminated.add(boundary.boundary_id)

    @override
    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        return client_exit_code

    @override
    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        leaf = self._leaves.get(boundary.boundary_id)
        if leaf is None:
            raise SandboxBackendError("the boundary is not owned by this Linux session")
        driver = _CgroupDriver(boundary, leaf)
        report = await terminate_boundary(driver, policy, force_requested)
        del self._leaves[boundary.boundary_id]
        notes = list(report.notes)
        if boundary.boundary_id in self._descendants_terminated:
            self._descendants_terminated.discard(boundary.boundary_id)
            notes.append("descendant processes that outlived the root workload were killed")
        if driver.killed:
            notes.append("the cgroup was killed with cgroup.kill")
        return TerminationReport(
            boundary_id=report.boundary_id,
            attempts=report.attempts,
            observations=report.observations,
            state=report.state,
            resources_released=report.resources_released,
            notes=tuple(notes),
        )

    @override
    async def close(self) -> None:
        failures: list[str] = []
        if self._leaves:
            failures.append(f"{len(self._leaves)} execution boundaries were not terminated")
        if self._instance is not None:
            try:
                await asyncio.to_thread(self._instance.rmdir)
                self._instance = None
            except OSError as error:
                failures.append(f"instance cgroup removal failed: {type(error).__name__}")
        if self._state is not None:
            try:
                await asyncio.to_thread(_remove_tree, self._state)
                self._state = None
                self._workspace = None
            except OSError as error:
                failures.append(f"state directory removal failed: {type(error).__name__}")
        if failures:
            raise SandboxBackendError("; ".join(failures))

    def _require_workspace(self) -> Path:
        if self._workspace is None:
            raise SandboxCapabilityError("this instance has no workspace")
        return self._workspace

    @override
    async def export_workspace(self, destination: Path) -> None:
        workspace = self._require_workspace()

        def anonymize(member: tarfile.TarInfo) -> tarfile.TarInfo:
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            return member

        def export() -> None:
            with tarfile.open(destination, mode="x:", format=tarfile.PAX_FORMAT) as bundle:
                for entry in sorted(workspace.iterdir()):
                    bundle.add(entry, arcname=entry.name, filter=anonymize)

        try:
            await asyncio.to_thread(export)
        except (OSError, tarfile.TarError) as error:
            raise SandboxBackendError(
                f"the workspace could not be exported: {type(error).__name__}"
            ) from error

    @override
    async def import_workspace(self, source: Path) -> None:
        workspace = self._require_workspace()

        def restore() -> None:
            if any(workspace.iterdir()):
                raise SandboxBackendError("the workspace must be empty before a restore")
            with tarfile.open(source, mode="r:") as bundle:
                bundle.extractall(workspace, filter="data")

        try:
            await asyncio.to_thread(restore)
        except (OSError, tarfile.TarError) as error:
            raise SandboxBackendError(
                f"the workspace could not be restored: {type(error).__name__}"
            ) from error


class _CgroupDriver(TerminationDriver):
    signal_releases = False

    def __init__(self, boundary: Boundary, leaf: Path) -> None:
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested
        self._leaf = leaf
        self.killed = False

    @override
    async def observe(self) -> Observation:
        return await asyncio.to_thread(self._observe)

    def _observe(self) -> Observation:
        if not self._leaf.exists():
            return Observation(BoundaryState.ABSENT)
        if _populated(self._leaf):
            return Observation(BoundaryState.RUNNING)
        return Observation(BoundaryState.EXITED if self.start_requested else BoundaryState.CREATED)

    @override
    async def signal(self, *, force: bool) -> None:
        if force:
            await asyncio.to_thread(_write, self._leaf / "cgroup.kill", "1")
            self.killed = True
        else:
            await asyncio.to_thread(self._terminate_members)

    def _terminate_members(self) -> None:
        if sys.platform == "linux":
            expected = "0::/" + str(self._leaf.relative_to(_CGROUP_MOUNT))
            for line in _read(self._leaf / "cgroup.procs").split():
                try:
                    descriptor = os.pidfd_open(int(line))
                except (ProcessLookupError, ValueError):
                    continue
                except OSError as error:
                    raise SandboxBackendError(
                        f"pidfd_open failed: {type(error).__name__}"
                    ) from error
                try:
                    # The pidfd pins the identity; confirm it still belongs to the leaf.
                    with Path(f"/proc/{line}/cgroup").open(encoding="ascii") as handle:
                        if handle.read().strip() != expected:
                            continue
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                except (ProcessLookupError, FileNotFoundError):
                    continue
                finally:
                    os.close(descriptor)
        else:
            raise SandboxBackendError("Linux namespaces require Linux")

    @override
    async def remove(self) -> None:
        try:
            await asyncio.to_thread(self._leaf.rmdir)
        except FileNotFoundError:
            return
        except OSError as error:
            raise SandboxBackendError(
                f"the cgroup leaf could not be removed: {type(error).__name__}"
            ) from error

    @override
    async def exists(self) -> bool:
        return await asyncio.to_thread(self._leaf.exists)
