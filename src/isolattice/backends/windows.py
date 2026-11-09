"""Native Windows backend built on nested Job Objects.

Job Objects contain a workload's whole process tree, enforce per-execution
memory, CPU and process budgets, and let termination be verified through job
accounting. They are not a security boundary: workloads keep the calling
user's identity and its filesystem, registry, network and IPC access.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
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
    ResourceBudget,
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
)
from ..termination import BoundaryState, Observation, TerminationDriver, terminate_boundary

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_MINIMUM_BUILD = 9200  # Windows 8 / Server 2012: nested Job Objects.
_TERMINATED_EXIT_CODE = 1
_LOW_INTEGRITY_PROBE_OUTPUT = b"4096 denied"
# Prints the probe's own integrity RID and whether it can open the caller (argv[1]) for
# termination; ``whoami /groups`` fails at low integrity.
_INTEGRITY_PROBE = """import ctypes, sys
from ctypes import wintypes as w
k = ctypes.WinDLL('kernel32', use_last_error=True)
a = ctypes.WinDLL('advapi32', use_last_error=True)
k.GetCurrentProcess.restype = w.HANDLE
k.OpenProcess.restype = w.HANDLE
k.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
k.CloseHandle.argtypes = (w.HANDLE,)
a.OpenProcessToken.argtypes = (w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE))
a.GetTokenInformation.argtypes = (
    w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)
)
a.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
a.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
a.GetSidSubAuthority.restype = ctypes.POINTER(w.DWORD)
a.GetSidSubAuthority.argtypes = (ctypes.c_void_p, w.DWORD)
token = w.HANDLE()
buffer = ctypes.create_string_buffer(64)
size = w.DWORD()
if not (
    a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token))
    and a.GetTokenInformation(token, 25, buffer, 64, ctypes.byref(size))
):
    raise SystemExit(1)
sid = ctypes.c_void_p.from_buffer(buffer).value
caller = k.OpenProcess(0x0001, False, int(sys.argv[1]))
if caller:
    k.CloseHandle(caller)
rid = a.GetSidSubAuthority(sid, a.GetSidSubAuthorityCount(sid)[0] - 1)[0]
print(rid, 'opened' if caller else 'denied')
"""
_NO_GRACEFUL_STOP = (
    "Windows Job Objects provide no graceful stop; the job's process tree was terminated"
)


class _JobHandle(ABC):
    """One owned Job Object."""

    @property
    @abstractmethod
    def closed(self) -> bool:
        """Whether the job handle was closed."""

    @abstractmethod
    def active_processes(self) -> int:
        """Return the job's active process count."""

    @abstractmethod
    def peak_memory(self) -> int:
        """Return the job's peak committed memory in bytes."""

    @abstractmethod
    def terminate(self) -> None:
        """Terminate every process in the job."""

    @abstractmethod
    def assign(self, process_handle: int) -> None:
        """Assign a process to the job."""

    @abstractmethod
    def inheritable_assign_handle(self) -> int:
        """Return a new inheritable handle that can only assign processes."""

    @abstractmethod
    def close(self) -> None:
        """Close the job handle, killing remaining processes."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _CREATE_NO_WINDOW = 0x08000000
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _JOB_BASIC_ACCOUNTING = 1
    _JOB_EXTENDED_LIMITS = 9
    _JOB_CPU_RATE = 15
    _LIMIT_ACTIVE_PROCESS = 0x00000008
    _LIMIT_JOB_MEMORY = 0x00000200
    _LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _CPU_RATE_ENABLE = 0x00000001
    _CPU_RATE_HARD_CAP = 0x00000004

    class _BasicLimit(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple(
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        )

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    class _CpuRate(ctypes.Structure):
        _fields_ = (("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD))

    class _Accounting(ctypes.Structure):
        _fields_ = (
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        )

    class _ThreadEntry(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        )

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.DuplicateHandle.restype = wintypes.BOOL
    _kernel32.DuplicateHandle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _JOB_OBJECT_ASSIGN_PROCESS = 0x0001

    def _failure(operation: str) -> SandboxBackendError:
        return SandboxBackendError(
            f"{operation} failed with Windows error {ctypes.get_last_error()}"
        )

    def _close_handle(handle: int) -> None:
        if not _kernel32.CloseHandle(handle):
            raise _failure("CloseHandle")

    class _Job(_JobHandle):
        """One owned Job Object; only assign-only duplicates ever leave this process."""

        def __init__(self, limits: _JobLimits) -> None:
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise _failure("CreateJobObjectW")
            self._handle: int | None = int(handle)
            try:
                self._configure(limits)
            except BaseException:
                self.close()
                raise

        @property
        @override
        def closed(self) -> bool:
            return self._handle is None

        def _open(self) -> int:
            if self._handle is None:
                raise SandboxBackendError("the owned Job Object handle is already closed")
            return self._handle

        def _query[T: ctypes.Structure](self, information_class: int, value: T) -> T:
            if not _kernel32.QueryInformationJobObject(
                self._open(), information_class, ctypes.byref(value), ctypes.sizeof(value), None
            ):
                raise _failure("QueryInformationJobObject")
            return value

        def _configure(self, limits: _JobLimits) -> None:
            extended = _ExtendedLimit()
            flags = _LIMIT_KILL_ON_JOB_CLOSE | _LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            if limits.memory_bytes is not None:
                flags |= _LIMIT_JOB_MEMORY
                extended.JobMemoryLimit = limits.memory_bytes
            if limits.process_limit is not None:
                flags |= _LIMIT_ACTIVE_PROCESS
                extended.BasicLimitInformation.ActiveProcessLimit = limits.process_limit
            extended.BasicLimitInformation.LimitFlags = flags
            if not _kernel32.SetInformationJobObject(
                self._open(),
                _JOB_EXTENDED_LIMITS,
                ctypes.byref(extended),
                ctypes.sizeof(extended),
            ):
                raise _failure("SetInformationJobObject(extended limits)")
            if limits.cpu_rate is not None:
                rate = _CpuRate(_CPU_RATE_ENABLE | _CPU_RATE_HARD_CAP, limits.cpu_rate)
                if not _kernel32.SetInformationJobObject(
                    self._open(), _JOB_CPU_RATE, ctypes.byref(rate), ctypes.sizeof(rate)
                ):
                    raise _failure("SetInformationJobObject(CPU rate)")
            self._verify(limits, flags)

        def _verify(self, limits: _JobLimits, flags: int) -> None:
            applied = self._query(_JOB_EXTENDED_LIMITS, _ExtendedLimit())
            basic = applied.BasicLimitInformation
            if int(basic.LimitFlags) & flags != flags:
                raise SandboxBackendError("Job Object limit flags were not applied")
            if limits.memory_bytes is not None and int(applied.JobMemoryLimit) != (
                limits.memory_bytes
            ):
                raise SandboxBackendError("Job Object memory budget was not applied")
            if limits.process_limit is not None and int(basic.ActiveProcessLimit) != (
                limits.process_limit
            ):
                raise SandboxBackendError("Job Object process budget was not applied")
            if limits.cpu_rate is not None:
                rate = self._query(_JOB_CPU_RATE, _CpuRate())
                expected = _CPU_RATE_ENABLE | _CPU_RATE_HARD_CAP
                if int(rate.ControlFlags) & expected != expected or int(rate.CpuRate) != (
                    limits.cpu_rate
                ):
                    raise SandboxBackendError("Job Object CPU budget was not applied")

        @override
        def assign(self, process_handle: int) -> None:
            if not _kernel32.AssignProcessToJobObject(self._open(), process_handle):
                raise _failure("AssignProcessToJobObject")

        @override
        def inheritable_assign_handle(self) -> int:
            """Return a new inheritable handle that can only assign processes."""
            duplicate = wintypes.HANDLE()
            current = _kernel32.GetCurrentProcess()
            if not _kernel32.DuplicateHandle(
                current,
                self._open(),
                current,
                ctypes.byref(duplicate),
                _JOB_OBJECT_ASSIGN_PROCESS,
                True,
                0,
            ):
                raise _failure("DuplicateHandle")
            return duplicate.value or 0

        @override
        def active_processes(self) -> int:
            return int(self._query(_JOB_BASIC_ACCOUNTING, _Accounting()).ActiveProcesses)

        @override
        def peak_memory(self) -> int:
            return int(self._query(_JOB_EXTENDED_LIMITS, _ExtendedLimit()).PeakJobMemoryUsed)

        @override
        def terminate(self) -> None:
            if not _kernel32.TerminateJobObject(self._open(), _TERMINATED_EXIT_CODE):
                raise _failure("TerminateJobObject")

        @override
        def close(self) -> None:
            handle, self._handle = self._handle, None
            if handle is not None:
                _close_handle(handle)

    def _resume_suspended(pid: int) -> None:
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
            raise _failure("CreateToolhelp32Snapshot")
        resumed = 0
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if int(entry.th32OwnerProcessID) == pid:
                    thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if not thread:
                        raise _failure("OpenThread")
                    try:
                        if int(_kernel32.ResumeThread(thread)) == 0xFFFFFFFF:
                            raise _failure("ResumeThread")
                    finally:
                        _close_handle(thread)
                    resumed += 1
                found = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            _close_handle(snapshot)
        if resumed != 1:
            raise SandboxBackendError(
                f"expected one suspended initial thread for the workload, found {resumed}"
            )

    def _contain(job: _JobHandle, pid: int) -> None:
        handle = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not handle:
            raise _failure("OpenProcess")
        try:
            job.assign(int(handle))
        finally:
            _close_handle(int(handle))
        _resume_suspended(pid)

    def _new_job(limits: _JobLimits) -> _JobHandle:
        return _Job(limits)

    _CREATION_FLAGS = _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
else:

    def _new_job(_limits: _JobLimits) -> _JobHandle:
        raise SandboxCapabilityError("Windows Job Objects require Windows")

    def _contain(_job: _JobHandle, _pid: int) -> None:
        raise SandboxCapabilityError("Windows Job Objects require Windows")

    _CREATION_FLAGS = 0


@dataclass(frozen=True, slots=True)
class _JobLimits:
    memory_bytes: int | None = None
    cpu_rate: int | None = None
    process_limit: int | None = None

    @classmethod
    def from_policy(cls, policy: SandboxPolicy | ResourceBudget) -> _JobLimits:
        cpu_rate: int | None = None
        if policy.cpu_cores is not None:
            processors = os.cpu_count()
            if not processors:
                raise SandboxCapabilityError("the host processor count is unavailable")
            cpu_rate = min(10000, max(1, math.ceil(policy.cpu_cores * 10000 / processors)))
        return cls(policy.memory_bytes, cpu_rate, policy.process_limit)


def _windows_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise TypeError(f"{label} must be a path")
    text = os.fspath(value)
    if not isinstance(text, str):
        raise TypeError(f"{label} must be a text path")
    pure = PureWindowsPath(text)
    if not pure.drive or not pure.is_absolute():
        raise ValueError(f"{label} must be an absolute Windows path")
    return Path(text)


def _base_interpreter() -> Path:
    """Return the base interpreter; a venv redirector would add an untracked process."""
    return Path(sys.base_prefix) / Path(sys.executable).name


def _helper_path(value: str | os.PathLike[str] | None) -> Path:
    """Validate the helper interpreter; off Windows discovery fails before it is used."""
    if value is None:
        default = _base_interpreter()
        return _windows_path(default, "helper_interpreter") if sys.platform == "win32" else default
    return _windows_path(value, "helper_interpreter")


def _supported_host() -> str | None:
    if sys.platform == "win32":
        if sys.getwindowsversion().build < _MINIMUM_BUILD:
            return "nested Windows Job Objects require Windows 8 or Windows Server 2012"
        return None
    return "Windows Job Objects require Windows"


def _report(
    available: bool, error: str | None = None, *, restricted_token: bool = False
) -> BackendReport:
    def state(supported: CapabilityState) -> CapabilityState:
        return supported if available else CapabilityState.UNSUPPORTED

    if restricted_token:
        filesystem = CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "low mandatory integrity blocks writes to medium-integrity files and registry keys; "
            "the calling user's readable data stays readable and low-integrity locations "
            "stay writable",
        )
        process = CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "low integrity cannot open the calling user's medium-integrity processes for "
            "write access, but shares the user, session, desktop and system services",
        )
    else:
        filesystem = CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "workloads keep the calling user's filesystem access"
        )
        process = CapabilityEvidence(
            CapabilityState.UNSUPPORTED,
            "same user, session and desktop; other processes remain visible and reachable",
        )
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: filesystem,
        Capability.NETWORK_ISOLATION: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "Job Objects cannot disable or restrict networking"
        ),
        Capability.PROCESS_ISOLATION: process,
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "descendants cannot break away and job accounting must report zero active "
            "processes before release; processes brokered by other services are not contained",
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "the host environment is not inherited; only SYSTEMROOT and explicit values",
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            state(CapabilityState.LIMITED),
            "per-execution job budgets; disk, network and aggregate instance budgets unsupported",
        ),
        Capability.MEMORY_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "job-wide committed memory; allocations beyond the budget fail",
        ),
        Capability.CPU_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED), "hard-capped job CPU rate relative to host cores"
        ),
        Capability.PROCESS_LIMIT: CapabilityEvidence(
            state(CapabilityState.SUPPORTED),
            "active process count including the root process; threads are not counted",
        ),
        Capability.SNAPSHOTS: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "snapshots are not implemented"
        ),
        Capability.CHECKPOINTS: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "checkpoints are not implemented"
        ),
        Capability.INSTANCE_LIMITS: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "aggregate instance budgets are not implemented"
        ),
        Capability.DISK_LIMIT: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "Job Objects cannot cap disk usage"
        ),
        Capability.WORKSPACE: CapabilityEvidence(
            CapabilityState.UNSUPPORTED, "persistent instance workspaces are not implemented"
        ),
    }
    identity = (
        (
            "Workloads run as the calling user with a restricted low-integrity token: no "
            "privileges beyond change-notify, Administrators deny-only, and no writes to "
            "medium-integrity objects. Reads, network and system-service brokers remain.",
        )
        if restricted_token
        else (
            "Workloads run as the calling user with its filesystem, registry, network "
            "and IPC access.",
        )
    )
    return BackendReport(
        name="windows-job",
        available=available,
        capabilities=capabilities,
        limitations=(
            "Containment only, not a security boundary: workloads keep the caller's access.",
            *identity,
            "Job budgets and process-tree containment apply per execution.",
            "Termination is forced; Job Objects provide no graceful stop.",
        ),
        error=error,
    )


def _reject_unimplemented(policy: SandboxPolicy) -> None:
    if policy.instance_budget is not None:
        raise SandboxCapabilityError("aggregate instance budgets are not implemented")
    if policy.scratch_bytes is not None:
        raise SandboxCapabilityError("Job Objects cannot cap disk usage")
    if policy.workspace:
        raise SandboxCapabilityError("persistent instance workspaces are not implemented")


class WindowsJobBackend(Backend):
    """Run workloads on this Windows host inside per-execution Job Objects.

    With ``restricted_token=True`` each workload is started through an
    out-of-process launcher under a restricted, low-integrity version of the
    calling user's token; see the Windows backend guide for what that does and
    does not prevent.
    """

    def __init__(
        self,
        *,
        working_directory: str | os.PathLike[str],
        restricted_token: bool = False,
        startup_timeout: float = 30.0,
        helper_interpreter: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(working_directory, (str, os.PathLike)):
            raise TypeError("working_directory must be a path")
        if not isinstance(restricted_token, bool):
            raise TypeError("restricted_token must be a bool")
        if (
            isinstance(startup_timeout, bool)
            or not isinstance(startup_timeout, (int, float))
            or not math.isfinite(startup_timeout)
            or startup_timeout <= 0
        ):
            raise ValueError("startup_timeout must be a positive finite number")
        root = Path(working_directory)
        if not root.is_absolute():
            raise ValueError("working_directory must be absolute")
        self._root = root
        self._restricted_token = restricted_token
        self._startup_timeout = float(startup_timeout)
        self._helper_interpreter = _helper_path(helper_interpreter)

    @override
    async def discover(self) -> BackendReport:
        restricted = self._restricted_token
        problem = _supported_host()
        if problem is not None:
            return _report(False, problem, restricted_token=restricted)
        try:
            self._probe()
            if restricted:
                await self._probe_restricted_token()
        except (SandboxBackendError, SandboxCapabilityError) as error:
            return _report(False, str(error), restricted_token=restricted)
        return _report(True, restricted_token=restricted)

    async def _probe_restricted_token(self) -> None:
        """Launch a real workload that must observe its own low integrity level."""
        from .windows_appcontainer import _launch

        job = _new_job(_JobLimits(64 * 1024 * 1024, None, 1))
        try:
            process = await _launch(
                self._helper_interpreter,
                {
                    "mode": "restricted",
                    "argv": [
                        str(self._helper_interpreter),
                        "-I",
                        "-S",
                        "-c",
                        _INTEGRITY_PROBE,
                        str(os.getpid()),
                    ],
                    "cwd": str(self._root),
                    "environment": _environment({}),
                },
                [job],
                stdin=asyncio.subprocess.DEVNULL,
                timeout=self._startup_timeout,
            )
            try:
                async with asyncio.timeout(self._startup_timeout):
                    output, errors = await process.communicate()
            finally:
                await _discard(process)
            observed = output.strip()
            if process.returncode != 0 or not observed.startswith(b"4096 "):
                detail = (errors.strip().splitlines() or [b""])[-1][:200].decode(errors="replace")
                raise SandboxCapabilityError(
                    f"the restricted-token probe did not run at low integrity "
                    f"(exit {process.returncode}, reported {observed[:40]!r}): {detail}"
                )
            if observed != _LOW_INTEGRITY_PROBE_OUTPUT:
                raise SandboxCapabilityError(
                    "a low-integrity workload can open the caller's process for termination "
                    "on this host, so the restricted token protects no processes; "
                    "User Account Control may be disabled"
                )
        except (SandboxExecutionError, TimeoutError) as error:
            raise SandboxCapabilityError(
                f"restricted-token launch probe failed: {error}"
            ) from error
        finally:
            if not job.closed and job.active_processes():
                job.terminate()
            job.close()

    def _probe(self) -> None:
        if not self._root.is_dir():
            raise SandboxCapabilityError("working_directory does not exist")
        _new_job(_JobLimits(64 * 1024 * 1024, 5000, 1)).close()

    @override
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        problem = _supported_host()
        if problem is not None:
            raise SandboxCapabilityError(problem)
        _reject_unimplemented(policy)
        if policy.network is not NetworkAccess.ENABLED:
            raise SandboxCapabilityError(
                "Job Objects cannot restrict networking; set network=NetworkAccess.ENABLED "
                "to acknowledge unrestricted network access"
            )
        if policy.read_only_root:
            raise SandboxCapabilityError(
                "Job Objects cannot make the filesystem read-only; set read_only_root=False "
                "to acknowledge the calling user's filesystem access"
            )
        limits = _JobLimits.from_policy(policy)
        budgets = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.cpu_rate,
            Capability.PROCESS_LIMIT: limits.process_limit,
        }
        report = _report(True, restricted_token=self._restricted_token)
        for capability in policy.required_capabilities:
            if capability in budgets:
                if budgets[capability] is None:
                    raise SandboxCapabilityError(
                        f"required capability {capability.value} needs an explicit budget"
                    )
                continue
            evidence = report.capabilities[capability]
            if evidence.state is not CapabilityState.SUPPORTED:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} is {evidence.state.value}: "
                    f"{evidence.detail}"
                )
        self._probe()
        if self._restricted_token:
            await self._probe_restricted_token()
        return _WindowsJobSession(
            self._root,
            limits,
            restricted_token=self._restricted_token,
            startup_timeout=self._startup_timeout,
            helper_interpreter=self._helper_interpreter,
        )


def _environment(explicit: Mapping[str, str]) -> dict[str, str]:
    environment: dict[str, str] = {}
    system_root = os.environ.get("SYSTEMROOT")
    if system_root and not any(key.upper() == "SYSTEMROOT" for key in explicit):
        environment["SYSTEMROOT"] = system_root
    environment.update(explicit)
    return environment


class _WindowsJobSession(BackendSession):
    backend_name = "windows-job"

    def __init__(
        self,
        root: Path,
        limits: _JobLimits,
        *,
        restricted_token: bool = False,
        startup_timeout: float = 30.0,
        helper_interpreter: Path,
    ) -> None:
        self._root = root
        self._limits = limits
        self._restricted_token = restricted_token
        self._startup_timeout = startup_timeout
        self._helper_interpreter = helper_interpreter
        self._jobs: dict[str, _JobHandle] = {}
        self._descendants_terminated: set[str] = set()

    def _working_directory(self, request: ExecutionRequest) -> Path:
        directory = self._root.joinpath(*PurePosixPath(request.cwd).parts[1:])
        if not directory.is_dir():
            raise SandboxExecutionError("the requested working directory does not exist")
        return directory

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        executable = PureWindowsPath(request.argv[0])
        if not executable.drive or not executable.is_absolute():
            raise SandboxExecutionError(
                "the Windows backend requires an absolute executable path; "
                "search-path resolution is not performed"
            )
        self._working_directory(request)
        boundary = Boundary(
            uuid.uuid4().hex, f"isolattice-{execution_id}", sandbox_id, execution_id
        )
        self._jobs[boundary.boundary_id] = _new_job(self._limits)
        register(boundary)
        return boundary

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        job = self._jobs[boundary.boundary_id]
        boundary.start_requested = True
        if self._restricted_token:
            from .windows_appcontainer import _launch

            return await _launch(
                self._helper_interpreter,
                {
                    "mode": "restricted",
                    "argv": list(request.argv),
                    "cwd": str(self._working_directory(request)),
                    "environment": _environment(request.environment),
                },
                [job],
                stdin=asyncio.subprocess.PIPE
                if request.stdin is not None
                else asyncio.subprocess.DEVNULL,
                timeout=self._startup_timeout,
            )
        try:
            process = await asyncio.create_subprocess_exec(
                *request.argv,
                stdin=asyncio.subprocess.PIPE
                if request.stdin is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_directory(request),
                env=_environment(request.environment),
                creationflags=_CREATION_FLAGS,
            )
        except OSError as error:
            raise SandboxExecutionError(
                f"could not start the workload: {type(error).__name__}"
            ) from error
        try:
            _contain(job, process.pid)
        except BaseException:
            await _discard(process)
            raise
        return process

    @override
    async def workload_exited(self, boundary: Boundary) -> None:
        # Surviving descendants hold inherited pipes open; the job ends with its root.
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
            raise SandboxBackendError("the boundary is not owned by this Windows session")
        driver = _JobDriver(boundary, job)
        report = await terminate_boundary(
            driver, replace(policy, graceful_timeout=0), force_requested
        )
        del self._jobs[boundary.boundary_id]
        notes = list(report.notes)
        descendants = boundary.boundary_id in self._descendants_terminated
        self._descendants_terminated.discard(boundary.boundary_id)
        if descendants:
            notes.append("descendant processes that outlived the root workload were terminated")
        if driver.terminated or descendants:
            notes.append(_NO_GRACEFUL_STOP)
        if driver.peak_memory is not None:
            notes.append(f"peak job committed memory: {driver.peak_memory} bytes")
        return replace(report, notes=tuple(notes))


async def _discard(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        async with asyncio.timeout(5):
            await process.wait()
    except TimeoutError as error:
        raise SandboxBackendError("an uncontained workload process could not be reaped") from error


class _JobDriver(TerminationDriver):
    signal_releases = False

    def __init__(self, boundary: Boundary, job: _JobHandle) -> None:
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested
        self._job = job
        self.terminated = False
        self.peak_memory: int | None = None

    @override
    async def observe(self) -> Observation:
        if self._job.closed:
            return Observation(BoundaryState.ABSENT)
        if self._job.active_processes():
            return Observation(BoundaryState.RUNNING)
        return Observation(BoundaryState.EXITED)

    @override
    async def signal(self, *, force: bool) -> None:
        if force:
            self._job.terminate()
            self.terminated = True

    @override
    async def remove(self) -> None:
        if self._job.active_processes():
            raise SandboxBackendError("the Job Object still contains active processes")
        self.peak_memory = self._job.peak_memory()
        self._job.close()

    @override
    async def exists(self) -> bool:
        return not self._job.closed
