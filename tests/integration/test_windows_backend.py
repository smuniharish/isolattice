"""Live Windows Job Object tests; they run on every Windows host without extra setup."""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    NetworkAccess,
    Sandbox,
    SandboxCapabilityError,
    SandboxExecutionError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
    WindowsJobBackend,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")

PYTHON = str(Path(sys.base_prefix) / "python.exe")
ROOT = Path(__file__).resolve().parents[2]


def policy(**overrides: Any) -> SandboxPolicy:
    base = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        read_only_root=False,
        execution_timeout=30,
        termination=TerminationPolicy(verification_interval=0.05, retry_delay=0.05),
    )
    return replace(base, **overrides)


def python(code: str, **options: Any) -> ExecutionRequest:
    return ExecutionRequest(argv=(PYTHON, "-c", code), **options)


def exited(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return True
        try:
            return bool(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    raise AssertionError("Windows only")


def wait_exited(pid: int, seconds: float = 10) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if exited(pid):
            return True
        time.sleep(0.05)
    return exited(pid)


SPAWN_TREE = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
)


def test_discovery_reports_evidence_without_isolation_claims(tmp_path: Path) -> None:
    report = asyncio.run(WindowsJobBackend(working_directory=tmp_path).discover())
    assert report.available, report.error
    for capability in (
        Capability.FILESYSTEM_ISOLATION,
        Capability.NETWORK_ISOLATION,
        Capability.PROCESS_ISOLATION,
    ):
        assert report.capabilities[capability].state is CapabilityState.UNSUPPORTED
    for capability in (Capability.MEMORY_LIMIT, Capability.CPU_LIMIT, Capability.PROCESS_LIMIT):
        assert report.capabilities[capability].state is CapabilityState.SUPPORTED
    assert any("not a security boundary" in item for item in report.limitations)


def test_unenforceable_policies_fail_closed(tmp_path: Path) -> None:
    async def check() -> None:
        async with Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox:
            # Containment-only: a policy that waives isolation is honoured as requested.
            unisolated = SandboxPolicy(network=NetworkAccess.ENABLED, read_only_root=False)
            async with await sandbox.create(policy=unisolated):
                pass
            for rejected in (
                SandboxPolicy(read_only_root=False),
                SandboxPolicy(network=NetworkAccess.ENABLED),
                policy(required_capabilities=(Capability.NETWORK_ISOLATION,)),
                policy(required_capabilities=(Capability.MEMORY_LIMIT,)),
            ):
                with pytest.raises(SandboxCapabilityError):
                    await sandbox.create(policy=rejected)
        missing = WindowsJobBackend(working_directory=tmp_path / "missing")
        assert not (await missing.discover()).available

    asyncio.run(check())


def test_io_environment_and_working_directory(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    os.environ["ISOLATTICE_HOST_ONLY"] = "secret"

    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy(stdout_limit=4096)) as instance,
        ):
            result = await instance.run(
                python(
                    "import json, os, sys; data = sys.stdin.buffer.read(); "
                    "print(json.dumps([data.decode(), os.getcwd(), sorted(os.environ)])); "
                    "sys.stderr.write('warn'); sys.exit(3)",
                    stdin=b"input",
                    environment={"GREETING": "hi"},
                    cwd="/work",
                )
            )
        assert result.exit_code == 3
        assert result.stderr == b"warn"
        data, cwd, names = json.loads(result.stdout)
        assert data == "input"
        assert Path(cwd) == tmp_path / "work"
        assert {name.upper() for name in names} == {"GREETING", "SYSTEMROOT"}
        assert not result.stdout_truncated
        assert result.termination.resources_released

    try:
        asyncio.run(check())
    finally:
        del os.environ["ISOLATTICE_HOST_ONLY"]


def test_output_is_truncated_at_the_policy_limit(tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy(stdout_limit=8, stderr_limit=4)) as instance,
        ):
            result = await instance.run(
                python("import sys; print('x' * 100); sys.stderr.write('y' * 100)")
            )
        assert result.stdout == b"x" * 8
        assert result.stdout_truncated
        assert result.stderr == b"y" * 4
        assert result.stderr_truncated

    asyncio.run(check())


def test_relative_executable_and_missing_cwd_are_rejected(tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxExecutionError):
                await instance.run(ExecutionRequest(argv=("python.exe", "-V")))
            with pytest.raises(SandboxExecutionError):
                await instance.run(python("print(1)", cwd="/missing"))

    asyncio.run(check())


def test_descendants_are_terminated_after_the_root_exits(tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(python(SPAWN_TREE))
        assert result.exit_code == 0
        assert result.termination.state == "terminated"
        assert result.termination.resources_released
        assert any("no graceful stop" in note for note in result.termination.notes)
        assert any("outlived the root" in note for note in result.termination.notes)
        assert wait_exited(int(result.stdout.strip()))

    asyncio.run(check())


def test_breakaway_from_the_job_is_denied(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys\n"
        "try:\n"
        "    subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=0x01000000).wait()\n"
        "except OSError as error:\n"
        "    print('DENIED', error.winerror)\n"
    )

    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(python(code))
        assert result.stdout.startswith(b"DENIED"), result

    asyncio.run(check())


def test_timeout_and_forced_cancel_verify_tree_termination(tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxTimeoutError) as raised:
                await instance.run(python(SPAWN_TREE + "import time; time.sleep(60)", timeout=2))
            report = raised.value.report
            assert report is not None
            assert report.resources_released

            handle = await instance.start(python(SPAWN_TREE + "import time; time.sleep(60)"))
            output = handle.output()
            first = await anext(output)
            cancelled = await handle.cancel(force=True)
            await output.aclose()
            assert cancelled is not None
            assert cancelled.resources_released
            assert wait_exited(int(first.data.strip()))

    asyncio.run(check())


def test_memory_budget_fails_allocations(tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy(memory_bytes=96 * 1024 * 1024)) as instance,
        ):
            small = await instance.run(python("b = bytearray(16 * 1024 * 1024)"))
            large = await instance.run(python("b = bytearray(256 * 1024 * 1024)"))
        assert small.exit_code == 0
        assert large.exit_code != 0
        assert b"MemoryError" in large.stderr
        assert any("peak job committed memory" in note for note in large.termination.notes)

    asyncio.run(check())


def test_process_budget_counts_the_whole_tree(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys\n"
        "children = []\n"
        "try:\n"
        "    for _ in range(5):\n"
        "        children.append(subprocess.Popen([sys.executable, '-c', 'import time; "
        "time.sleep(5)']))\n"
        "except OSError:\n"
        "    pass\n"
        "print(len(children))\n"
    )

    async def check() -> None:
        async with (
            Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox,
            await sandbox.create(policy=policy(process_limit=3)) as instance,
        ):
            result = await instance.run(python(code))
        assert result.stdout.strip() == b"2"
        assert result.termination.resources_released

    asyncio.run(check())


def test_cpu_budget_hard_caps_the_job(tmp_path: Path) -> None:
    def spin(seconds: float) -> str:
        # Stops after the wall-clock window or 1.5 seconds of CPU, whichever comes first.
        return (
            "import time\n"
            f"end = time.monotonic() + {seconds}\n"
            "while time.monotonic() < end and time.process_time() < 1.5:\n"
            "    pass\n"
            "print(time.process_time())\n"
        )

    async def check() -> None:
        async with Sandbox(backend=WindowsJobBackend(working_directory=tmp_path)) as sandbox:
            async with await sandbox.create(policy=policy(cpu_cores=0.25)) as instance:
                throttled = await instance.run(python(spin(2)))
            # The uncapped control proves the loop is CPU-bound even on a loaded host.
            async with await sandbox.create(policy=policy()) as instance:
                unthrottled = await instance.run(python(spin(20)))
        assert float(throttled.stdout) < 1.0, throttled.stdout
        assert float(unthrottled.stdout) >= 1.5, unthrottled.stdout

    asyncio.run(check())


def test_parent_crash_kills_the_contained_tree(tmp_path: Path) -> None:
    driver = (
        "import asyncio, os, sys\n"
        "from pathlib import Path\n"
        "from isolattice import ExecutionRequest, NetworkAccess, Sandbox, SandboxPolicy, "
        "WindowsJobBackend\n"
        "async def main():\n"
        "    backend = WindowsJobBackend(working_directory=Path(sys.argv[1]))\n"
        "    policy = SandboxPolicy("
        "network=NetworkAccess.ENABLED, read_only_root=False)\n"
        "    sandbox = Sandbox(backend=backend)\n"
        "    instance = await sandbox.create(policy=policy)\n"
        "    handle = await instance.start(ExecutionRequest(argv=(sys.argv[2], '-c', "
        "sys.argv[3])))\n"
        "    async for chunk in handle.output():\n"
        "        print(chunk.data.decode().strip(), flush=True)\n"
        "        os._exit(9)\n"
        "asyncio.run(main())\n"
    )
    completed = subprocess.run(
        [PYTHON, "-c", driver, str(tmp_path), PYTHON, SPAWN_TREE + "import time; time.sleep(60)"],
        capture_output=True,
        timeout=60,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        check=False,
    )
    assert completed.returncode == 9, completed
    assert wait_exited(int(completed.stdout.strip()))


RESTRICTED_PROBE = r"""
import json, os, sys, winreg
checks = {}
def attempt(name, action):
    try:
        action()
        checks[name] = "allowed"
    except OSError:
        checks[name] = "denied"
def write():
    with open(os.path.join(sys.argv[1], "probe.txt"), "w") as stream:
        stream.write("x")
attempt("write", write)
attempt("registry", lambda: winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\IsolatticeTest"))
print(json.dumps(checks))
"""


def restricted_backend(root: Path) -> WindowsJobBackend:
    return WindowsJobBackend(working_directory=root, restricted_token=True)


def test_restricted_token_fails_closed_or_runs_at_low_integrity(tmp_path: Path) -> None:
    async def check() -> None:
        backend = restricted_backend(tmp_path)
        report = await backend.discover()
        async with Sandbox(backend=backend) as sandbox:
            if not report.available:
                # A host agent that re-elevates children must never yield a workload.
                assert report.error is not None
                assert "integrity" in report.error
                with pytest.raises(SandboxCapabilityError, match="integrity"):
                    await sandbox.create(policy=policy())
                pytest.skip(report.error)
            assert report.capabilities[Capability.FILESYSTEM_ISOLATION].state is (
                CapabilityState.LIMITED
            )
            async with await sandbox.create(policy=policy(process_limit=4)) as instance:
                result = await instance.run(
                    ExecutionRequest(argv=(PYTHON, "-I", "-c", RESTRICTED_PROBE, str(tmp_path)))
                )
                assert result.exit_code == 0, result.stderr
                assert json.loads(result.stdout) == {"write": "denied", "registry": "denied"}
                assert not (tmp_path / "probe.txt").exists()
                handle = await instance.start(python(SPAWN_TREE + "import time; time.sleep(60)"))
                output = handle.output()
                first = await anext(output)
                cancelled = await handle.cancel(force=True)
                await output.aclose()
                assert cancelled is not None
                assert cancelled.resources_released
                assert wait_exited(int(first.data.strip()))

    asyncio.run(check())


EXAMPLES = [
    (
        "04-resource-management",
        "windows_job_limits.py",
        (
            "Memory budget: allocation beyond 128 MiB failed",
            "Process budget: 1 of 4 child processes admitted",
            "Timeout cleanup: terminated",
            "Descendant process terminated: True",
        ),
    ),
    (
        "07-capabilities-and-backends",
        "windows_job_capabilities.py",
        (
            "Backend: windows-job (available: True)",
            "filesystem_isolation: unsupported",
            "memory_limit: supported",
            "not a security boundary",
            "Default restricted policy: rejected",
            "Required filesystem isolation: rejected",
        ),
    ),
    (
        "08-native-backends",
        "windows_appcontainer.py",
        (
            "Backend: windows-appcontainer (available: True)",
            "filesystem_isolation: limited",
            "network_isolation: supported",
            "User profile: denied",
            "Registry writes: denied",
            "Network: denied",
            "Host environment: not inherited",
            "Memory budget: allocation beyond 128 MiB failed",
            "Timeout cleanup: terminated",
            "Workspace snapshot: restored into a new instance",
            "Required full filesystem isolation: rejected",
        ),
    ),
    (
        "08-native-backends",
        "windows_restricted_token.py",
        (
            "Backend: windows-job (available: True)",
            "filesystem_isolation: limited",
            "Integrity level: low (0x1000)",
            "Write working directory: denied",
            "Create HKCU registry key: denied",
            "Open this process for termination: denied",
            "Timeout cleanup: terminated",
            "Descendant process terminated: True",
        ),
    ),
]


def test_every_windows_example_has_a_live_test_case() -> None:
    expected = {ROOT / "examples" / directory / name for directory, name, _ in EXAMPLES}
    assert set((ROOT / "examples").rglob("windows_*.py")) == expected


@pytest.mark.parametrize(("directory", "filename", "expected"), EXAMPLES)
def test_windows_examples(directory: str, filename: str, expected: tuple[str, ...]) -> None:
    completed = subprocess.run(
        [PYTHON, str(ROOT / "examples" / directory / filename)],
        capture_output=True,
        timeout=120,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        check=False,
    )
    if b"cannot load inside an AppContainer" in completed.stderr:
        pytest.skip(completed.stderr.decode().strip())
    if b"a host privilege-management agent may have replaced it" in completed.stderr:
        pytest.skip(completed.stderr.decode().strip())
    assert completed.returncode == 0, completed
    assert completed.stderr == b"", completed.stderr
    for text in expected:
        assert text in completed.stdout.decode()
