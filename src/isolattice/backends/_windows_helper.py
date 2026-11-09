"""Out-of-sandbox launcher for the Windows AppContainer and restricted-token modes.

Runs as ``python -I -S _windows_helper.py CONFIG_HANDLE STATUS_HANDLE`` with
only standard-library imports. It reads a JSON configuration from the
inherited CONFIG_HANDLE pipe and creates the workload inside the configured
AppContainer and Job Objects in one ``CreateProcessW`` call:

* ``PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`` with only the configured
  capability SIDs, so the workload has a lowbox token without network or
  device capabilities;
* optionally ``PROCESS_CREATION_ALL_APPLICATION_PACKAGES_OPT_OUT`` for a less
  privileged AppContainer (LPAC);
* ``PROC_THREAD_ATTRIBUTE_JOB_LIST`` so the process is inside its jobs before
  its first instruction runs;
* ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST`` so only the three standard handles are
  inherited.

In ``restricted`` mode there is no AppContainer. The helper derives a primary
token from its own with ``CreateRestrictedToken`` (all privileges except
``SeChangeNotifyPrivilege`` removed, Administrators deny-only), lowers it to
low mandatory integrity, starts the workload suspended with
``CreateProcessAsUserW`` and the same job and handle lists, and resumes it only
after reading back a low integrity level from the workload's own token.

The helper closes its job handles immediately afterwards, so it never keeps a
job alive. It reports a setup failure as a message on STATUS_HANDLE with exit
status 125; on success the status pipe is closed without data. It then waits
and exits with the workload's exit code.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import msvcrt
import os
import subprocess
import sys
from ctypes import wintypes
from typing import Any

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_NEW_PROCESS_GROUP = 0x00000200
    _CREATE_NO_WINDOW = 0x08000000
    _STARTF_USESTDHANDLES = 0x00000100
    _HANDLE_LIST = 0x00020002
    _SECURITY_CAPABILITIES = 0x00020009
    _JOB_LIST = 0x0002000D
    _ALL_APPLICATION_PACKAGES_POLICY = 0x0002000F
    _ALL_APPLICATION_PACKAGES_OPT_OUT = 0x01
    _SE_GROUP_ENABLED = 0x00000004
    _INFINITE = 0xFFFFFFFF
    _WAIT_OBJECT_0 = 0
    _SETUP_FAILED = 125
    _CREATE_SUSPENDED = 0x00000004
    _TOKEN_ASSIGN_PRIMARY = 0x0001
    _TOKEN_DUPLICATE = 0x0002
    _TOKEN_QUERY = 0x0008
    _TOKEN_ADJUST_DEFAULT = 0x0080
    _DISABLE_MAX_PRIVILEGE = 0x1
    _TOKEN_INTEGRITY_LEVEL = 25
    _SE_GROUP_INTEGRITY = 0x00000020
    _ADMINISTRATORS_SID = "S-1-5-32-544"
    _LOW_INTEGRITY_SID = "S-1-16-4096"
    _LOW_INTEGRITY_RID = 0x1000

    class _StartupInfo(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        )

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = (("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p))

    class _ProcessInformation(ctypes.Structure):
        _fields_ = (
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        )

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))

    class _SecurityCapabilities(ctypes.Structure):
        _fields_ = (
            ("AppContainerSid", ctypes.c_void_p),
            ("Capabilities", ctypes.c_void_p),
            ("CapabilityCount", wintypes.DWORD),
            ("Reserved", wintypes.DWORD),
        )

    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _kernel32.DeleteProcThreadAttributeList.restype = None
    _kernel32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessInformation),
    )
    _kernel32.GetStdHandle.restype = wintypes.HANDLE
    _kernel32.GetStdHandle.argtypes = (wintypes.DWORD,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.ExitProcess.restype = None
    _kernel32.ExitProcess.argtypes = (wintypes.UINT,)
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.CreateRestrictedToken.restype = wintypes.BOOL
    _advapi32.CreateRestrictedToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.SetTokenInformation.restype = wintypes.BOOL
    _advapi32.SetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.GetLengthSid.argtypes = (ctypes.c_void_p,)
    _advapi32.GetSidSubAuthorityCount.restype = ctypes.c_void_p
    _advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
    _advapi32.GetSidSubAuthority.restype = ctypes.c_void_p
    _advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    _advapi32.CreateProcessAsUserW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessInformation),
    )

    class _SetupError(Exception):
        pass

    def _check(result: object, operation: str) -> None:
        if not result:
            raise _SetupError(f"{operation} failed with Windows error {ctypes.get_last_error()}")

    def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
        entries = []
        for key, value in sorted(environment.items(), key=lambda item: item[0].upper()):
            if not key or "=" in key or "\0" in key or "\0" in value:
                raise _SetupError("invalid environment entry")
            entries.append(f"{key}={value}")
        return ctypes.create_unicode_buffer("\0".join(entries) + "\0\0")

    class _Attributes:
        def __init__(self, count: int) -> None:
            size = ctypes.c_size_t()
            _kernel32.InitializeProcThreadAttributeList(None, count, 0, ctypes.byref(size))
            self._buffer = ctypes.create_string_buffer(size.value)
            _check(
                _kernel32.InitializeProcThreadAttributeList(
                    self._buffer, count, 0, ctypes.byref(size)
                ),
                "InitializeProcThreadAttributeList",
            )
            self._values: list[object] = []

        @property
        def address(self) -> int:
            return ctypes.addressof(self._buffer)

        def update(self, attribute: int, value: ctypes._CData) -> None:
            self._values.append(value)
            _check(
                _kernel32.UpdateProcThreadAttribute(
                    self._buffer,
                    0,
                    attribute,
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                    None,
                    None,
                ),
                f"UpdateProcThreadAttribute({attribute:#x})",
            )

        def close(self) -> None:
            _kernel32.DeleteProcThreadAttributeList(self._buffer)

    def _restricted_low_integrity_token() -> int:
        """Return a primary token derived from ours: no privileges, admins deny-only, low IL."""
        token = wintypes.HANDLE()
        _check(
            _advapi32.OpenProcessToken(
                _kernel32.GetCurrentProcess(),
                _TOKEN_QUERY | _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY | _TOKEN_ADJUST_DEFAULT,
                ctypes.byref(token),
            ),
            "OpenProcessToken",
        )
        administrators = ctypes.c_void_p()
        low = ctypes.c_void_p()
        restricted = wintypes.HANDLE()
        try:
            _check(
                _advapi32.ConvertStringSidToSidW(_ADMINISTRATORS_SID, ctypes.byref(administrators)),
                "SID parse",
            )
            _check(
                _advapi32.ConvertStringSidToSidW(_LOW_INTEGRITY_SID, ctypes.byref(low)), "SID parse"
            )
            deny_only = (_SidAndAttributes * 1)(_SidAndAttributes(administrators.value, 0))
            _check(
                _advapi32.CreateRestrictedToken(
                    token,
                    _DISABLE_MAX_PRIVILEGE,
                    1,
                    deny_only,
                    0,
                    None,
                    0,
                    None,
                    ctypes.byref(restricted),
                ),
                "CreateRestrictedToken",
            )
            label = _SidAndAttributes(low.value, _SE_GROUP_INTEGRITY)
            if not _advapi32.SetTokenInformation(
                restricted,
                _TOKEN_INTEGRITY_LEVEL,
                ctypes.byref(label),
                ctypes.sizeof(label) + _advapi32.GetLengthSid(low),
            ):
                error = ctypes.get_last_error()
                _kernel32.CloseHandle(restricted)
                raise _SetupError(f"SetTokenInformation failed with Windows error {error}")
        finally:
            _kernel32.CloseHandle(token)
            for value in (administrators, low):
                if value.value:
                    _kernel32.LocalFree(value)
        return restricted.value or 0

    def _integrity_rid(process: int) -> int:
        token = wintypes.HANDLE()
        _check(
            _advapi32.OpenProcessToken(process, _TOKEN_QUERY, ctypes.byref(token)),
            "OpenProcessToken",
        )
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            buffer = ctypes.create_string_buffer(size.value)
            _check(
                _advapi32.GetTokenInformation(
                    token, _TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)
                ),
                "GetTokenInformation",
            )
            sid = _SidAndAttributes.from_buffer(buffer).Sid
            count = ctypes.cast(
                _advapi32.GetSidSubAuthorityCount(sid), ctypes.POINTER(ctypes.c_ubyte)
            )
            last = _advapi32.GetSidSubAuthority(sid, count.contents.value - 1)
            return ctypes.cast(last, ctypes.POINTER(wintypes.DWORD)).contents.value
        finally:
            _kernel32.CloseHandle(token)

    def _launch(config: dict[str, Any]) -> int:
        argv = [str(item) for item in config["argv"]]
        jobs = [int(item) for item in config["jobs"]]
        mode = str(config.get("mode", "appcontainer"))
        if mode not in ("appcontainer", "restricted"):
            raise _SetupError("unknown launch mode")
        sids: list[ctypes.c_void_p] = []
        attributes = _Attributes(4)
        token = 0
        try:
            if mode == "appcontainer":
                names = [str(item) for item in config["capabilities"]]
                sid = ctypes.c_void_p()
                _check(
                    _advapi32.ConvertStringSidToSidW(str(config["sid"]), ctypes.byref(sid)),
                    "SID parse",
                )
                sids.append(sid)
                granted = (_SidAndAttributes * max(1, len(names)))()
                for index, name in enumerate(names):
                    capability = ctypes.c_void_p()
                    _check(
                        _advapi32.ConvertStringSidToSidW(name, ctypes.byref(capability)),
                        "SID parse",
                    )
                    sids.append(capability)
                    granted[index] = _SidAndAttributes(capability.value, _SE_GROUP_ENABLED)
                capabilities = _SecurityCapabilities(
                    sid.value, ctypes.addressof(granted) if names else None, len(names), 0
                )
                attributes.update(_SECURITY_CAPABILITIES, capabilities)
                if config["less_privileged"]:
                    attributes.update(
                        _ALL_APPLICATION_PACKAGES_POLICY,
                        wintypes.DWORD(_ALL_APPLICATION_PACKAGES_OPT_OUT),
                    )
            else:
                token = _restricted_low_integrity_token()
            attributes.update(_JOB_LIST, (wintypes.HANDLE * len(jobs))(*jobs))
            standard = [_kernel32.GetStdHandle(code) for code in (-10, -11, -12)]
            if any(handle in (None, 0, ctypes.c_void_p(-1).value) for handle in standard):
                raise _SetupError("the launcher has no standard handles")
            inherited = list(dict.fromkeys(standard))
            attributes.update(_HANDLE_LIST, (wintypes.HANDLE * len(inherited))(*inherited))
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput = standard[:2]
            startup.StartupInfo.hStdError = standard[2]
            startup.lpAttributeList = attributes.address
            environment = _environment_block(dict(config["environment"]))
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            information = _ProcessInformation()
            flags = (
                _EXTENDED_STARTUPINFO_PRESENT
                | _CREATE_UNICODE_ENVIRONMENT
                | _CREATE_NEW_PROCESS_GROUP
                | _CREATE_NO_WINDOW
            )
            if token:
                _check(
                    _advapi32.CreateProcessAsUserW(
                        token,
                        argv[0],
                        command_line,
                        None,
                        None,
                        True,
                        flags | _CREATE_SUSPENDED,
                        environment,
                        str(config["cwd"]),
                        ctypes.byref(startup),
                        ctypes.byref(information),
                    ),
                    "CreateProcessAsUserW",
                )
            else:
                _check(
                    _kernel32.CreateProcessW(
                        argv[0],
                        command_line,
                        None,
                        None,
                        True,
                        flags,
                        environment,
                        str(config["cwd"]),
                        ctypes.byref(startup),
                        ctypes.byref(information),
                    ),
                    "CreateProcessW",
                )
        finally:
            attributes.close()
            for value in sids:
                _kernel32.LocalFree(value)
            if token:
                _kernel32.CloseHandle(token)
        if mode == "restricted":
            try:
                # Never run the workload unless its token is verifiably low integrity.
                observed = _integrity_rid(int(information.hProcess))
                if observed != _LOW_INTEGRITY_RID:
                    raise _SetupError(
                        f"the workload token has integrity level {observed:#x}, not low "
                        f"({_LOW_INTEGRITY_RID:#x}); a host privilege-management agent may "
                        "have replaced it"
                    )
                if _kernel32.ResumeThread(information.hThread) == 0xFFFFFFFF:
                    raise _SetupError(
                        f"ResumeThread failed with Windows error {ctypes.get_last_error()}"
                    )
            except BaseException:
                _kernel32.TerminateProcess(information.hProcess, _SETUP_FAILED)
                _kernel32.CloseHandle(information.hThread)
                _kernel32.CloseHandle(information.hProcess)
                raise
        _kernel32.CloseHandle(information.hThread)
        return int(information.hProcess)

    def main() -> None:
        status_fd = -1
        try:
            config_handle, status_handle = (int(value) for value in sys.argv[1:3])
            status_fd = msvcrt.open_osfhandle(status_handle, os.O_WRONLY)
            with open(msvcrt.open_osfhandle(config_handle, os.O_RDONLY), "rb") as stream:
                config = json.loads(stream.read(1024 * 1024))
            try:
                process = _launch(config)
            finally:
                for job in config.get("jobs", ()):
                    _kernel32.CloseHandle(int(job))
        except Exception as error:
            message = str(error) if isinstance(error, _SetupError) else type(error).__name__
            if status_fd >= 0:
                with contextlib.suppress(OSError):
                    os.write(status_fd, f"workload launch failed: {message}".encode())
            _kernel32.ExitProcess(_SETUP_FAILED)
            return
        os.close(status_fd)
        if _kernel32.WaitForSingleObject(process, _INFINITE) != _WAIT_OBJECT_0:
            _kernel32.ExitProcess(_SETUP_FAILED)
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
            _kernel32.ExitProcess(_SETUP_FAILED)
        _kernel32.ExitProcess(code.value)

    if __name__ == "__main__":
        main()
else:
    raise ImportError("the Windows launcher runs only on Windows")
