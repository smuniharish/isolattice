"""Run a workload in a less privileged Windows AppContainer inside nested Job Objects.

Requires Windows 10 1703 or later and a Python installation readable by restricted
application packages (python.org installs under Program Files are)::

    python examples/08-native-backends/windows_appcontainer.py

AppContainers share the host kernel; kernel exploits are outside its threat model.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from isolattice import (
    Capability,
    ExecutionRequest,
    LocalArtifactStore,
    NetworkAccess,
    ResourceBudget,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
    WindowsAppContainerBackend,
)

PYTHON = str(Path(sys.base_prefix) / "python.exe")
# The loader reports these when the interpreter's files are not readable by
# restricted application packages, for example under the user profile.
_UNLOADABLE = {0xC0000135, 0xC0000022}

_PROBE = """
import json, os, socket, sys, winreg
checks = {}
def attempt(name, action):
    try:
        action()
        checks[name] = "allowed"
    except OSError:
        checks[name] = "denied"
attempt("home", lambda: os.listdir(sys.argv[1]))
attempt("registry", lambda: winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\\\Iso"))
attempt("network", lambda: socket.create_connection(("1.1.1.1", 53), 2).close())
checks["environment"] = [name for name in ("PATH", "USERNAME", "USERPROFILE") if name in os.environ]
print(json.dumps(checks))
"""

_TREE = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(600)'])\n"
    "time.sleep(600)\n"
)


def python(code: str, *args: str, **options: Any) -> ExecutionRequest:
    return ExecutionRequest(argv=(PYTHON, "-I", "-c", code, *args), **options)


async def main() -> None:
    backend = WindowsAppContainerBackend()
    report = await backend.discover()
    print(f"Backend: {report.name} (available: {report.available})")
    if not report.available:
        raise RuntimeError(f"the AppContainer backend is unavailable: {report.error}")
    for capability in (Capability.FILESYSTEM_ISOLATION, Capability.NETWORK_ISOLATION):
        print(f"{capability.value}: {report.capabilities[capability].state.value}")

    policy = SandboxPolicy(
        network=NetworkAccess.NONE,
        memory_bytes=128 * 1024 * 1024,
        process_limit=8,
        workspace=True,
        instance_budget=ResourceBudget(memory_bytes=256 * 1024 * 1024, process_limit=16),
        execution_timeout=60,
        required_capabilities=(
            Capability.NETWORK_ISOLATION,
            Capability.MEMORY_LIMIT,
            Capability.PROCESS_LIMIT,
            Capability.INSTANCE_LIMITS,
            Capability.SNAPSHOTS,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="isolattice-") as directory:
        store = LocalArtifactStore(directory)
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            key = "workspace.tar"
            async with await sandbox.create(policy=policy) as instance:
                probe = await instance.run(python(_PROBE, str(Path.home())))
                if probe.exit_code & 0xFFFFFFFF in _UNLOADABLE:
                    raise SystemExit(
                        f"{PYTHON} cannot load inside an AppContainer "
                        f"(status {probe.exit_code & 0xFFFFFFFF:#x}); "
                        "use a Python installed under Program Files"
                    )
                checks = json.loads(probe.stdout)
                expected = {
                    "home": "denied",
                    "registry": "denied",
                    "network": "denied",
                    "environment": [],
                }
                if checks != expected:
                    raise RuntimeError(f"unexpected sandbox access: {checks}")
                print("User profile: denied")
                print("Registry writes: denied")
                print("Network: denied")
                print("Host environment: not inherited")

                memory = await instance.run(python("b = bytearray(512 * 1024 * 1024)"))
                if memory.exit_code == 0 or b"MemoryError" not in memory.stderr:
                    raise RuntimeError("the memory budget was not enforced")
                print("Memory budget: allocation beyond 128 MiB failed")

                try:
                    await instance.run(python(_TREE, timeout=2))
                except SandboxTimeoutError as error:
                    stopped = error.report
                else:
                    raise RuntimeError("the workload was expected to exceed its deadline")
                if stopped is None or not stopped.resources_released:
                    raise RuntimeError("process-tree termination was not verified")
                print(f"Timeout cleanup: {stopped.state}")

                await instance.run(python("open('result', 'w').write('kept')", cwd="/workspace"))
                await instance.snapshot(key)
            async with await sandbox.create(policy=policy, workspace_snapshot=key) as restored:
                result = await restored.run(
                    python("print(open('result').read())", cwd="/workspace")
                )
                if result.stdout.strip() != b"kept":
                    raise RuntimeError("the workspace snapshot was not restored")
                print("Workspace snapshot: restored into a new instance")

            try:
                await sandbox.create(
                    policy=SandboxPolicy(
                        required_capabilities=(Capability.FILESYSTEM_ISOLATION,),
                    )
                )
            except SandboxCapabilityError:
                print("Required full filesystem isolation: rejected")
            else:
                raise RuntimeError("a limited capability was accepted as required")


if __name__ == "__main__":
    asyncio.run(main())
