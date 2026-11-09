"""Contain a process tree and enforce budgets with native Windows Job Objects.

Job Objects are not a security boundary: the workload keeps this user's
filesystem, registry and network access. Run only trusted code.
"""

import argparse
import asyncio
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

_SPAWN_TREE = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(child.pid, flush=True)
time.sleep(60)
"""

_PROCESS_BUDGET = """
import subprocess, sys
children = []
try:
    for _ in range(4):
        children.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"]))
except OSError:
    pass
print(len(children))
for child in children:
    child.kill()
"""


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
    backend = WindowsJobBackend(working_directory=working_directory)
    policy = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        read_only_root=False,
        memory_bytes=128 * 1024 * 1024,
        cpu_cores=1,
        process_limit=2,
        execution_timeout=30,
        required_capabilities=(
            Capability.MEMORY_LIMIT,
            Capability.CPU_LIMIT,
            Capability.PROCESS_LIMIT,
        ),
    )
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        memory = await instance.run(
            ExecutionRequest(argv=(python, "-c", "b = bytearray(512 * 1024 * 1024)"))
        )
        if memory.exit_code == 0 or b"MemoryError" not in memory.stderr:
            raise RuntimeError("the job memory budget was not enforced")
        print("Memory budget: allocation beyond 128 MiB failed")

        budget = await instance.run(ExecutionRequest(argv=(python, "-c", _PROCESS_BUDGET)))
        if budget.exit_code != 0 or budget.stdout.strip() != b"1":
            raise RuntimeError("the job process budget was not enforced")
        print("Process budget: 1 of 4 child processes admitted")

        handle = await instance.start(ExecutionRequest(argv=(python, "-c", _SPAWN_TREE), timeout=3))
        grandchild = b""
        async for chunk in handle.output():
            if chunk.stream == "stdout":
                grandchild += chunk.data
        try:
            await handle.wait()
        except SandboxTimeoutError as error:
            report = error.report
        else:
            raise RuntimeError("the workload was expected to exceed its deadline")
        if report is None or not report.resources_released:
            raise RuntimeError("process-tree termination was not verified")
        if not _process_exited(int(grandchild.strip())):
            raise RuntimeError("a descendant process escaped termination")
        print(f"Timeout cleanup: {report.state}")
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
