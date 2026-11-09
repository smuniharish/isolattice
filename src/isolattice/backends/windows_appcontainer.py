"""Native Windows backend built on AppContainers and nested Job Objects.

Every sandbox instance owns a fresh AppContainer profile, an instance Job
Object and a private directory granted only to the profile's package SID.
Each execution runs inside an execution Job Object nested in the instance job
and is created inside its AppContainer and both jobs by one ``CreateProcessW``
call in an out-of-process launcher, so it never runs outside its boundary.

By default the profile is a less privileged AppContainer (LPAC) holding only the
``registryRead`` capability, which the Windows loader needs to start most
programs. AppContainers have no network capabilities here, so outbound and
loopback connections are denied. This is not a qualified boundary for hostile
code; see the backend documentation for the remaining attack surface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
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
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
)
from ..termination import terminate_boundary
from .windows import (
    _NO_GRACEFUL_STOP,
    _base_interpreter,
    _JobDriver,
    _JobHandle,
    _JobLimits,
    _windows_path,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

BACKEND_NAME = "windows-appcontainer"
# Windows 10 1703: LPAC and PROC_THREAD_ATTRIBUTE_JOB_LIST.
_MINIMUM_BUILD = 15063
_HELPER = Path(__file__).with_name("_windows_helper.py")
_MAX_CONFIG_BYTES = 1024 * 1024
_MAX_STATUS_BYTES = 4096
_LPAC_CAPABILITIES = ("registryRead",)
_PROFILE_PREFIX = "isolattice."
_CREATE_NO_WINDOW = 0x08000000
_FILE_ALL_ACCESS = 0x001F01FF
_FILE_READ_EXECUTE = 0x001200A9


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    from .windows import _Job

    _userenv = ctypes.WinDLL("userenv", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernelbase = ctypes.WinDLL("kernelbase", use_last_error=True)

    _HRESULT_ALREADY_EXISTS = 0x800700B7
    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _GRANT_ACCESS = 1
    _SET_ACCESS = 2
    _REVOKE_ACCESS = 4
    _NO_INHERITANCE = 0
    _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 3
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_NAME = 1

    class _Trustee(ctypes.Structure):
        _fields_ = (
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_void_p),
        )

    class _ExplicitAccess(ctypes.Structure):
        _fields_ = (
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _Trustee),
        )

    _PSID_ARRAY = ctypes.POINTER(ctypes.c_void_p)

    _userenv.CreateAppContainerProfile.restype = ctypes.c_long
    _userenv.CreateAppContainerProfile.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _userenv.DeleteAppContainerProfile.restype = ctypes.c_long
    _userenv.DeleteAppContainerProfile.argtypes = (wintypes.LPCWSTR,)
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_wchar_p),
    )
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.FreeSid.restype = ctypes.c_void_p
    _advapi32.FreeSid.argtypes = (ctypes.c_void_p,)
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.SetEntriesInAclW.restype = wintypes.DWORD
    _advapi32.SetEntriesInAclW.argtypes = (
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccess),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _kernelbase.DeriveCapabilitySidsFromName.restype = wintypes.BOOL
    _kernelbase.DeriveCapabilitySidsFromName.argtypes = (
        wintypes.LPCWSTR,
        ctypes.POINTER(_PSID_ARRAY),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_PSID_ARRAY),
        ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def _windows_error(operation: str, code: int | None = None) -> SandboxBackendError:
        value = ctypes.get_last_error() if code is None else code
        return SandboxBackendError(f"{operation} failed with Windows error {value:#x}")

    def _sid_string(sid: int | None) -> str:
        text = ctypes.c_wchar_p()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _windows_error("ConvertSidToStringSidW")
        try:
            return str(text.value)
        finally:
            _kernel32.LocalFree(text)

    def _create_profile(name: str) -> str:
        sid = ctypes.c_void_p()
        result = _userenv.CreateAppContainerProfile(
            name, name, "Isolattice sandbox instance", None, 0, ctypes.byref(sid)
        )
        if result & 0xFFFFFFFF == _HRESULT_ALREADY_EXISTS:
            raise SandboxBackendError("an AppContainer profile with this name already exists")
        if result != 0:
            raise _windows_error("CreateAppContainerProfile", result & 0xFFFFFFFF)
        try:
            return _sid_string(sid.value)
        finally:
            _advapi32.FreeSid(sid)

    def _delete_profile(name: str) -> None:
        result = _userenv.DeleteAppContainerProfile(name)
        if result != 0:
            raise _windows_error("DeleteAppContainerProfile", result & 0xFFFFFFFF)

    def _capability_sid(name: str) -> str:
        groups = _PSID_ARRAY()
        group_count = wintypes.DWORD()
        sids = _PSID_ARRAY()
        sid_count = wintypes.DWORD()
        if not _kernelbase.DeriveCapabilitySidsFromName(
            name,
            ctypes.byref(groups),
            ctypes.byref(group_count),
            ctypes.byref(sids),
            ctypes.byref(sid_count),
        ):
            raise _windows_error("DeriveCapabilitySidsFromName")
        try:
            if sid_count.value != 1:
                raise SandboxBackendError(f"capability {name} has no unique SID")
            return _sid_string(sids[0])
        finally:
            for array, count in ((groups, group_count.value), (sids, sid_count.value)):
                for index in range(count):
                    _kernel32.LocalFree(array[index])
                _kernel32.LocalFree(ctypes.cast(array, ctypes.c_void_p))

    def _apply_access(path: Path, entry: _ExplicitAccess, *, replace_all: bool) -> None:
        current = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        if not replace_all:
            result = _advapi32.GetNamedSecurityInfoW(
                str(path),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION,
                None,
                None,
                ctypes.byref(current),
                None,
                ctypes.byref(descriptor),
            )
            if result:
                raise _windows_error("GetNamedSecurityInfoW", result)
        updated = ctypes.c_void_p()
        try:
            result = _advapi32.SetEntriesInAclW(
                1, ctypes.byref(entry), current, ctypes.byref(updated)
            )
            if result:
                raise _windows_error("SetEntriesInAclW", result)
            information = _DACL_SECURITY_INFORMATION
            if replace_all:
                information |= _PROTECTED_DACL_SECURITY_INFORMATION
            result = _advapi32.SetNamedSecurityInfoW(
                ctypes.create_unicode_buffer(str(path)),
                _SE_FILE_OBJECT,
                information,
                None,
                None,
                updated,
                None,
            )
            if result:
                raise _windows_error("SetNamedSecurityInfoW", result)
        finally:
            _kernel32.LocalFree(updated)
            _kernel32.LocalFree(descriptor)

    def _set_sid_access(path: Path, sid: str, mode: int, access: int) -> None:
        binary = ctypes.c_void_p()
        if not _advapi32.ConvertStringSidToSidW(sid, ctypes.byref(binary)):
            raise _windows_error("ConvertStringSidToSidW")
        try:
            inheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT if path.is_dir() else _NO_INHERITANCE
            trustee = _Trustee(None, 0, _TRUSTEE_IS_SID, 0, binary.value)
            _apply_access(
                path, _ExplicitAccess(access, mode, inheritance, trustee), replace_all=False
            )
        finally:
            _kernel32.LocalFree(binary)

    def _grant(path: Path, sid: str, access: int) -> None:
        _set_sid_access(path, sid, _GRANT_ACCESS, access)

    def _revoke(path: Path, sid: str) -> None:
        _set_sid_access(path, sid, _REVOKE_ACCESS, 0)

    def _reclaim(path: Path) -> None:
        """Replace the DACL with a protected full-control grant for the caller."""
        name = ctypes.create_unicode_buffer("CURRENT_USER")
        trustee = _Trustee(None, 0, _TRUSTEE_IS_NAME, 0, ctypes.addressof(name))
        inheritance = _SUB_CONTAINERS_AND_OBJECTS_INHERIT if path.is_dir() else _NO_INHERITANCE
        _apply_access(
            path,
            _ExplicitAccess(_FILE_ALL_ACCESS, _SET_ACCESS, inheritance, trustee),
            replace_all=True,
        )

    def _close_inherited(handles: Sequence[int]) -> None:
        for handle in handles:
            _kernel32.CloseHandle(handle)

    def _new_instance_job(limits: _JobLimits) -> _JobHandle:
        return _Job(limits)

    def _launcher_handles(
        config_read: int, status_write: int, jobs: Sequence[int]
    ) -> tuple[int, int, subprocess.STARTUPINFO]:
        """Make the pipe ends inheritable and restrict inheritance to them and ``jobs``."""
        import msvcrt

        config_handle = msvcrt.get_osfhandle(config_read)
        status_handle = msvcrt.get_osfhandle(status_write)
        os.set_handle_inheritable(config_handle, True)
        os.set_handle_inheritable(status_handle, True)
        startup = subprocess.STARTUPINFO(
            lpAttributeList={"handle_list": [config_handle, status_handle, *jobs]}
        )
        return config_handle, status_handle, startup

else:

    def _unsupported() -> SandboxCapabilityError:
        return SandboxCapabilityError("Windows AppContainers require Windows")

    def _create_profile(_name: str) -> str:
        raise _unsupported()

    def _delete_profile(_name: str) -> None:
        raise _unsupported()

    def _capability_sid(_name: str) -> str:
        raise _unsupported()

    def _grant(_path: Path, _sid: str, _access: int) -> None:
        raise _unsupported()

    def _revoke(_path: Path, _sid: str) -> None:
        raise _unsupported()

    def _reclaim(_path: Path) -> None:
        raise _unsupported()

    def _close_inherited(_handles: Sequence[int]) -> None:
        raise _unsupported()

    def _new_instance_job(_limits: _JobLimits) -> _JobHandle:
        raise _unsupported()

    def _launcher_handles(
        _config_read: int, _status_write: int, _jobs: Sequence[int]
    ) -> tuple[int, int, object]:
        raise _unsupported()


def _supported_host() -> str | None:
    if sys.platform == "win32":
        if sys.getwindowsversion().build < _MINIMUM_BUILD:
            return "less privileged AppContainers with job lists require Windows 10 1703 or later"
        return None
    return "Windows AppContainers require Windows"


def _report(available: bool, less_privileged: bool, error: str | None = None) -> BackendReport:
    def state(supported: CapabilityState) -> CapabilityState:
        return supported if available else CapabilityState.UNSUPPORTED

    readable = (
        "objects granted to ALL RESTRICTED APPLICATION PACKAGES (most system directories)"
        if less_privileged
        else "objects granted to ALL APPLICATION PACKAGES (system directories, Program Files)"
    )
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "AppContainer token: no access to the user profile or other user-owned files; "
            f"reads remain possible for {readable} and configured read-only paths; writes "
            "only to the instance directory",
        ),
        Capability.NETWORK_ISOLATION: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "no network capabilities: outbound, inbound and loopback connections are denied",
        ),
        Capability.PROCESS_ISOLATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "low-integrity AppContainer token cannot open the user's processes or write "
            "their objects; same session and desktop; brokered services remain reachable",
        ),
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "workloads start inside nested Job Objects; job accounting is verified empty "
            "before release; processes brokered by other services are not contained",
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "the host environment is not inherited; SYSTEMROOT and LOCALAPPDATA are set and "
            "Windows points TEMP and TMP at the instance profile",
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "per-execution and aggregate instance job budgets; disk and network budgets "
            "are unsupported",
        ),
        Capability.MEMORY_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "job-wide committed memory"
        ),
        Capability.CPU_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "hard-capped job CPU rate relative to host cores"
        ),
        Capability.PROCESS_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "active process count of the job"
        ),
        Capability.SNAPSHOTS: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "cold tar snapshots of the instance workspace"
        ),
        Capability.CHECKPOINTS: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "Windows provides no process checkpoint facility"
        ),
        Capability.INSTANCE_LIMITS: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "instance Job Object above every nested execution job",
        ),
        Capability.DISK_LIMIT: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "the instance directory has no quota"
        ),
        Capability.WORKSPACE: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "private host directory granted only to the instance AppContainer",
        ),
    }
    return BackendReport(
        name=BACKEND_NAME,
        available=available,
        capabilities=capabilities,
        limitations=(
            "AppContainers share the host kernel; kernel exploits are out of scope.",
            "Workloads share the host kernel, session and desktop and can reach services "
            "that accept AppContainer clients.",
            "Only network=NONE is supported; no network capabilities are granted.",
            "Executables must be readable by the AppContainer; grant others through "
            "read_only_paths. Grants rewrite the DACL of every file in the tree, which is "
            "slow for large trees, and interpreters inside user profiles may still fail to "
            "load their DLLs.",
            "Termination is forced; Job Objects provide no graceful stop.",
            "Profiles and ACL grants of a crashed host process are not reconciled.",
        ),
        error=error,
    )


def _scratch_tree(path: Path) -> None:
    """Remove a tree the workload may have locked down with ACLs or attributes."""
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass
    pending = [path]
    while pending:
        current = pending.pop()
        _reclaim(current)
        if current.is_symlink() or current.is_junction():
            continue
        current.chmod(stat.S_IWRITE | stat.S_IREAD)
        if current.is_dir():
            pending.extend(current.iterdir())
    shutil.rmtree(path)


def _read_status(descriptor: int) -> bytes:
    data = bytearray()
    while len(data) <= _MAX_STATUS_BYTES:
        chunk = os.read(descriptor, _MAX_STATUS_BYTES)
        if not chunk:
            break
        data += chunk
    return bytes(data[:_MAX_STATUS_BYTES])


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


async def _reap(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        async with asyncio.timeout(5):
            await process.wait()
    except TimeoutError as error:
        raise SandboxBackendError("the Windows workload launcher could not be reaped") from error


async def _launch(
    interpreter: Path,
    configuration: dict[str, object],
    jobs: Sequence[_JobHandle],
    *,
    stdin: int,
    timeout: float,  # noqa: ASYNC109 - also bounds reaping the launcher
) -> asyncio.subprocess.Process:
    """Start the launcher; return it once the workload is inside its boundary."""
    if sys.platform != "win32":
        raise SandboxExecutionError("the Windows workload launcher requires Windows")
    config_read, config_write = os.pipe()
    status_read, status_write = os.pipe()
    passed: list[int] = []
    try:
        for job in jobs:
            # A loop keeps every handle created before a failure for cleanup below.
            passed.append(job.inheritable_assign_handle())  # noqa: PERF401
        payload = json.dumps({**configuration, "jobs": passed}).encode()
        if len(payload) > _MAX_CONFIG_BYTES:
            raise SandboxExecutionError("the execution configuration is too large")
        config_handle, status_handle, startup = _launcher_handles(config_read, status_write, passed)
        try:
            process = await asyncio.create_subprocess_exec(
                interpreter,
                "-I",
                "-S",
                str(_HELPER),
                str(config_handle),
                str(status_handle),
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows")},
                startupinfo=startup,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as error:
            raise SandboxExecutionError(
                f"could not start the Windows workload launcher: {type(error).__name__}"
            ) from error
    except BaseException:
        os.close(config_write)
        os.close(status_read)
        raise
    finally:
        os.close(config_read)
        os.close(status_write)
        _close_inherited(passed)
    reader = asyncio.ensure_future(asyncio.to_thread(_read_status, status_read))
    try:
        try:
            await asyncio.to_thread(_write_all, config_write, payload)
        except OSError:
            pass  # The launcher exited early; its status explains why.
        finally:
            os.close(config_write)
        async with asyncio.timeout(timeout):
            message = await asyncio.shield(reader)
    except BaseException as error:
        await _reap(process)
        await asyncio.gather(reader, return_exceptions=True)
        os.close(status_read)
        if isinstance(error, TimeoutError):
            raise SandboxExecutionError(
                "the Windows workload launcher did not start in time"
            ) from error
        raise
    os.close(status_read)
    if message:
        await _reap(process)
        raise SandboxExecutionError(message.decode("utf-8", "replace"))
    return process


class WindowsAppContainerBackend(Backend):
    """Run workloads in per-instance AppContainers inside nested Job Objects."""

    def __init__(
        self,
        *,
        read_only_paths: Sequence[str | os.PathLike[str]] = (),
        state_directory: str | os.PathLike[str] | None = None,
        less_privileged: bool = True,
        startup_timeout: float = 15.0,
        helper_interpreter: str | os.PathLike[str] | None = None,
    ) -> None:
        if isinstance(read_only_paths, (str, os.PathLike)):
            raise TypeError("read_only_paths must be a sequence of paths")
        self._read_only_paths = tuple(
            _windows_path(item, "read-only path") for item in read_only_paths
        )
        self._state_directory = (
            None if state_directory is None else _windows_path(state_directory, "state_directory")
        )
        if not isinstance(less_privileged, bool):
            raise TypeError("less_privileged must be a bool")
        if isinstance(startup_timeout, bool) or not isinstance(startup_timeout, (int, float)):
            raise TypeError("startup_timeout must be a number")
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError("startup_timeout must be finite and positive")
        self._less_privileged = less_privileged
        self._startup_timeout = float(startup_timeout)
        self._helper_interpreter = _windows_path(
            _base_interpreter() if helper_interpreter is None else helper_interpreter,
            "helper_interpreter",
        )

    def _session(self, limits: _JobLimits) -> _AppContainerSession:
        return _AppContainerSession(
            limits=limits,
            read_only_paths=self._read_only_paths,
            state_directory=self._state_directory,
            less_privileged=self._less_privileged,
            startup_timeout=self._startup_timeout,
            helper_interpreter=self._helper_interpreter,
        )

    @override
    async def discover(self) -> BackendReport:
        problem = _supported_host()
        if problem is not None:
            return _report(False, self._less_privileged, problem)
        try:
            await self._probe()
        except (SandboxBackendError, SandboxCapabilityError, SandboxExecutionError) as error:
            return _report(False, self._less_privileged, str(error))
        return _report(True, self._less_privileged)

    async def _probe(self) -> None:
        """Start a trivial process through the whole AppContainer chain."""
        for path in self._read_only_paths:
            if not path.exists():
                raise SandboxCapabilityError(f"read-only path {path} does not exist")
        session = self._session(_JobLimits(64 * 1024 * 1024, None, 1))
        try:
            await asyncio.to_thread(session.allocate, None, False, grant_read_only=False)
            code = await session.probe()
        except OSError as error:
            raise SandboxCapabilityError(
                f"the AppContainer probe failed: {type(error).__name__}"
            ) from error
        finally:
            await session.close()
        if code != 7:
            raise SandboxCapabilityError(f"the AppContainer probe exited with status {code}")

    @override
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        problem = _supported_host()
        if problem is not None:
            raise SandboxCapabilityError(problem)
        if policy.network is not NetworkAccess.NONE:
            raise SandboxCapabilityError("the AppContainer backend supports only network=NONE")
        if not policy.read_only_root:
            raise SandboxCapabilityError(
                "AppContainer workloads can write only to their instance directory; "
                "set read_only_root=True"
            )
        if policy.scratch_bytes is not None:
            raise SandboxCapabilityError("the AppContainer backend cannot cap disk usage")
        limits = _JobLimits.from_policy(policy)
        budget = (
            None
            if policy.instance_budget is None
            else _JobLimits.from_policy(policy.instance_budget)
        )
        explicit = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.cpu_rate,
            Capability.PROCESS_LIMIT: limits.process_limit,
        }
        for capability, value in explicit.items():
            if capability in policy.required_capabilities and value is None:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} needs an explicit budget"
                )
        report = _report(True, self._less_privileged)
        for capability in policy.required_capabilities:
            evidence = report.capabilities[capability]
            if evidence.state is not CapabilityState.SUPPORTED:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} is {evidence.state.value}: "
                    f"{evidence.detail}"
                )
        await self._probe()
        session = self._session(limits)
        try:
            await asyncio.to_thread(session.allocate, budget, policy.workspace)
        except BaseException as error:
            try:
                await session.close()
            except BaseException as cleanup:
                raise SandboxBackendError(
                    f"instance allocation failed ({error}) and cleanup failed ({cleanup})"
                ) from error
            raise
        return session


class _AppContainerSession(BackendSession):
    backend_name = BACKEND_NAME

    def __init__(
        self,
        *,
        limits: _JobLimits,
        read_only_paths: tuple[Path, ...],
        state_directory: Path | None,
        less_privileged: bool,
        startup_timeout: float,
        helper_interpreter: Path,
    ) -> None:
        self._limits = limits
        self._read_only_paths = read_only_paths
        self._state_parent = state_directory
        self._less_privileged = less_privileged
        self._startup_timeout = startup_timeout
        self._helper_interpreter = helper_interpreter
        self._capabilities: list[str] = []
        self._profile: str | None = None
        self._sid: str | None = None
        self._granted: list[Path] = []
        self._root: Path | None = None
        self._workspace: Path | None = None
        self._instance_job: _JobHandle | None = None
        self._jobs: dict[str, _JobHandle] = {}
        self._scratch: dict[str, Path] = {}
        self._leftover_scratch: list[Path] = []
        self._descendants_terminated: set[str] = set()

    @property
    @override
    def workspace_path(self) -> str | None:
        return None if self._workspace is None else "/workspace"

    def allocate(
        self, budget: _JobLimits | None, workspace: bool, *, grant_read_only: bool = True
    ) -> None:
        if self._less_privileged:
            self._capabilities = [_capability_sid(name) for name in _LPAC_CAPABILITIES]
        name = f"{_PROFILE_PREFIX}{uuid.uuid4().hex}"
        self._sid = _create_profile(name)
        self._profile = name
        self._root = Path(tempfile.mkdtemp(prefix="isolattice-ac-", dir=self._state_parent))
        _grant(self._root, self._sid, _FILE_ALL_ACCESS)
        if workspace:
            self._workspace = self._root / "workspace"
            self._workspace.mkdir()
        if grant_read_only:
            for path in self._read_only_paths:
                _grant(path, self._sid, _FILE_READ_EXECUTE)
                self._granted.append(path)
        self._instance_job = _new_instance_job(budget or _JobLimits())

    def _require(self) -> tuple[str, Path, _JobHandle]:
        if self._sid is None or self._root is None or self._instance_job is None:
            raise SandboxBackendError("the AppContainer instance is not allocated")
        return self._sid, self._root, self._instance_job

    def _environment(self, explicit: dict[str, str]) -> dict[str, str]:
        environment = {
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
            # CreateProcessW requires LOCALAPPDATA to start an AppContainer process.
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        }
        for key, value in explicit.items():
            for existing in [item for item in environment if item.upper() == key.upper()]:
                del environment[existing]
            environment[key] = value
        return environment

    def _working_directory(self, scratch: Path, cwd: str) -> Path:
        parts = PurePosixPath(cwd).parts[1:]
        for part in parts:
            if any(character in part for character in ':\\*?"<>|') or part.endswith((" ", ".")):
                raise SandboxExecutionError("the requested working directory is invalid")
        base = scratch
        if parts[:1] == ("workspace",) and self._workspace is not None:
            base, parts = self._workspace, parts[1:]
        directory = base.joinpath(*parts)
        if not directory.is_dir():
            raise SandboxExecutionError("the requested working directory does not exist")
        return directory

    async def probe(self) -> int:
        sid, root, job = self._require()
        system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        process = await _launch(
            self._helper_interpreter,
            self._configuration(
                sid,
                [str(system / "System32" / "cmd.exe"), "/d", "/c", "exit 7"],
                root,
                self._environment({}),
            ),
            [job],
            stdin=asyncio.subprocess.DEVNULL,
            timeout=self._startup_timeout,
        )
        try:
            async with asyncio.timeout(self._startup_timeout):
                await process.communicate()
        except TimeoutError as error:
            job.terminate()
            raise SandboxCapabilityError("the AppContainer probe timed out") from error
        finally:
            await _reap(process)
        return process.returncode or 0

    def _configuration(
        self, sid: str, argv: list[str], cwd: Path, environment: dict[str, str]
    ) -> dict[str, object]:
        return {
            "sid": sid,
            "capabilities": self._capabilities,
            "less_privileged": self._less_privileged,
            "argv": argv,
            "cwd": str(cwd),
            "environment": environment,
        }

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        _, root, _ = self._require()
        executable = PureWindowsPath(request.argv[0])
        if not executable.drive or not executable.is_absolute():
            raise SandboxExecutionError(
                "the AppContainer backend requires an absolute executable path; "
                "search-path resolution is not performed"
            )
        boundary = Boundary(
            uuid.uuid4().hex, f"isolattice-{execution_id}", sandbox_id, execution_id
        )
        scratch = root / f"x-{execution_id}"
        await asyncio.to_thread(scratch.mkdir)
        try:
            self._working_directory(scratch, request.cwd)
            job = await asyncio.to_thread(_new_instance_job, self._limits)
        except BaseException:
            await asyncio.to_thread(_scratch_tree, scratch)
            raise
        self._scratch[boundary.boundary_id] = scratch
        self._jobs[boundary.boundary_id] = job
        register(boundary)
        return boundary

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        sid, _, instance = self._require()
        job = self._jobs.get(boundary.boundary_id)
        scratch = self._scratch.get(boundary.boundary_id)
        if job is None or scratch is None:
            raise SandboxBackendError("the boundary is not owned by this AppContainer session")
        boundary.start_requested = True
        return await _launch(
            self._helper_interpreter,
            self._configuration(
                sid,
                list(request.argv),
                self._working_directory(scratch, request.cwd),
                self._environment(dict(request.environment)),
            ),
            [instance, job],
            stdin=asyncio.subprocess.PIPE
            if request.stdin is not None
            else asyncio.subprocess.DEVNULL,
            timeout=self._startup_timeout,
        )

    @override
    async def workload_exited(self, boundary: Boundary) -> None:
        job = self._jobs.get(boundary.boundary_id)
        if job is not None and not job.closed and job.active_processes():
            job.terminate()
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
        job = self._jobs.get(boundary.boundary_id)
        if job is None:
            raise SandboxBackendError("the boundary is not owned by this AppContainer session")
        driver = _JobDriver(boundary, job)
        report = await terminate_boundary(
            driver, replace(policy, graceful_timeout=0), force_requested
        )
        del self._jobs[boundary.boundary_id]
        notes = list(report.notes)
        if boundary.boundary_id in self._descendants_terminated:
            self._descendants_terminated.discard(boundary.boundary_id)
            notes.append("descendant processes that outlived the root workload were terminated")
        if driver.terminated:
            notes.append(_NO_GRACEFUL_STOP)
        if driver.peak_memory is not None:
            notes.append(f"peak job committed memory: {driver.peak_memory} bytes")
        scratch = self._scratch.pop(boundary.boundary_id, None)
        if scratch is not None and report.state == "terminated":
            try:
                await asyncio.to_thread(_scratch_tree, scratch)
            except OSError as error:
                self._leftover_scratch.append(scratch)
                notes.append(f"execution directory removal deferred: {type(error).__name__}")
        elif scratch is not None:
            self._leftover_scratch.append(scratch)
        return replace(report, notes=tuple(notes))

    @override
    async def close(self) -> None:
        failures: list[str] = []
        if self._jobs:
            failures.append(f"{len(self._jobs)} execution boundaries were not terminated")
        job, self._instance_job = self._instance_job, None
        if job is not None:
            try:
                if job.active_processes():
                    failures.append("the instance job still contained processes")
                job.close()
            except SandboxBackendError as error:
                failures.append(f"instance job release failed: {error}")
        if self._root is not None:
            try:
                await asyncio.to_thread(_scratch_tree, self._root)
                self._root = None
                self._workspace = None
                self._leftover_scratch.clear()
            except (OSError, SandboxBackendError) as error:
                failures.append(f"instance directory removal failed: {type(error).__name__}")
        while self._granted:
            path = self._granted[-1]
            try:
                if self._sid is not None:
                    await asyncio.to_thread(_revoke, path, self._sid)
                self._granted.pop()
            except (OSError, SandboxBackendError) as error:
                failures.append(f"read-only grant on {path} was not revoked: {error}")
                break
        if self._profile is not None:
            try:
                await asyncio.to_thread(_delete_profile, self._profile)
                self._profile = None
            except SandboxBackendError as error:
                failures.append(str(error))
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
