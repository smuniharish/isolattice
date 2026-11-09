"""Run a Job Object workload under a restricted, low-integrity Windows token.

The workload keeps the calling user's identity but loses its privileges, sees
Administrators as deny-only and runs at low mandatory integrity, so it cannot
write to the user's medium-integrity files, registry keys or processes::

    python examples/08-native-backends/windows_restricted_token.py

It can still read the user's files, use the network and reach system services.
AppContainers share the host kernel; kernel exploits are outside its threat model.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from isolattice import (
    Capability,
    ExecutionRequest,
    NetworkAccess,
    Sandbox,
    SandboxPolicy,
    SandboxTimeoutError,
    WindowsJobBackend,
)

_PROBE = r"""
import ctypes, json, os, sys, winreg
from ctypes import wintypes
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
)
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
token = wintypes.HANDLE()
advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token))
size = wintypes.DWORD()
advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
label = ctypes.create_string_buffer(size.value)
advapi32.GetTokenInformation(token, 25, label, size, ctypes.byref(size))
sid = ctypes.c_void_p(ctypes.c_void_p.from_buffer(label).value)
count = advapi32.GetSidSubAuthorityCount(sid).contents.value
checks = {"integrity": hex(advapi32.GetSidSubAuthority(sid, count - 1).contents.value)}
def attempt(name, action):
    try:
        action()
        checks[name] = "allowed"
    except OSError:
        checks[name] = "denied"
def write_file():
    with open(os.path.join(sys.argv[1], "probe.txt"), "w") as stream:
        stream.write("written by the workload")
def open_parent():
    handle = kernel32.OpenProcess(0x0001, False, int(sys.argv[2]))  # PROCESS_TERMINATE
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess")
    kernel32.CloseHandle(handle)
attempt("read", lambda: os.listdir(sys.argv[1]))
attempt("write", write_file)
attempt("registry", lambda: winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\IsolatticeProbe"))
attempt("terminate_parent", open_parent)
print(json.dumps(checks))
"""

_TREE = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(600)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(600)\n"
)


def _process_exited(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return True
        try:
            return bool(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    raise RuntimeError("this example requires Windows")


async def main(python: str, working_directory: Path) -> None:
    backend = WindowsJobBackend(working_directory=working_directory, restricted_token=True)
    report = await backend.discover()
    print(f"Backend: {report.name} (available: {report.available})")
    if not report.available:
        raise SystemExit(f"the restricted-token option is unavailable: {report.error}")
    for capability in (Capability.FILESYSTEM_ISOLATION, Capability.PROCESS_ISOLATION):
        print(f"{capability.value}: {report.capabilities[capability].state.value}")

    policy = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        read_only_root=False,
        memory_bytes=256 * 1024 * 1024,
        execution_timeout=30,
        required_capabilities=(Capability.MEMORY_LIMIT,),
    )
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        probe = await instance.run(
            ExecutionRequest(
                argv=(python, "-I", "-c", _PROBE, str(working_directory), str(os.getpid()))
            )
        )
        if probe.exit_code != 0:
            raise RuntimeError(probe.stderr.decode(errors="replace"))
        checks = json.loads(probe.stdout)
        expected = {
            "integrity": "0x1000",
            "read": "allowed",
            "write": "denied",
            "registry": "denied",
            "terminate_parent": "denied",
        }
        if checks != expected:
            raise RuntimeError(f"unexpected restricted-token behaviour: {checks}")
        if (working_directory / "probe.txt").exists():
            raise RuntimeError("the workload wrote to a medium-integrity directory")
        print("Integrity level: low (0x1000)")
        print("Read working directory: allowed")
        print("Write working directory: denied")
        print("Create HKCU registry key: denied")
        print("Open this process for termination: denied")

        handle = await instance.start(ExecutionRequest(argv=(python, "-I", "-c", _TREE), timeout=3))
        grandchild = b""
        async for chunk in handle.output():
            if chunk.stream == "stdout":
                grandchild += chunk.data
        try:
            await handle.wait()
        except SandboxTimeoutError as error:
            termination = error.report
        else:
            raise RuntimeError("the workload was expected to exceed its deadline")
        if termination is None or not termination.resources_released:
            raise RuntimeError("process-tree termination was not verified")
        if not _process_exited(int(grandchild.strip())):
            raise RuntimeError("a descendant process escaped termination")
        print(f"Timeout cleanup: {termination.state}")
        print("Descendant process terminated: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=str(Path(sys.base_prefix) / "python.exe"),
        help="Absolute path of a Python interpreter (not a venv redirector) for the workload",
    )
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="isolattice-") as directory:
        asyncio.run(main(arguments.python, Path(directory)))
