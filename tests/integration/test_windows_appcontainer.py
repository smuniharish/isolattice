"""Live Windows AppContainer tests; they run on every Windows 10 1703+ host without setup.

Workloads use the base interpreter, which must be readable by restricted application
packages (true for python.org installs under Program Files).
"""

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    ExecutionResult,
    LocalArtifactStore,
    NetworkAccess,
    ResourceBudget,
    Sandbox,
    SandboxCapabilityError,
    SandboxError,
    SandboxExecutionError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
    WindowsAppContainerBackend,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires Windows")

PYTHON = str(Path(sys.base_prefix) / "python.exe")
SYSTEM = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
CMD = str(SYSTEM / "cmd.exe")
ROOT = Path(__file__).resolve().parents[2]
PACKAGES = Path(os.environ.get("LOCALAPPDATA", "")) / "Packages"


def profiles() -> set[str]:
    return {path.name for path in PACKAGES.glob("isolattice.*")}


@pytest.fixture(autouse=True)
def no_leaked_profiles() -> Iterator[None]:
    before = profiles()
    yield
    assert profiles() <= before


@pytest.fixture(scope="module")
def interpreter() -> str:
    async def probe() -> bytes:
        async with (
            Sandbox(backend=WindowsAppContainerBackend()) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            return (await instance.run(python("print('ok')"))).stdout

    if asyncio.run(probe()).strip() != b"ok":
        pytest.skip(f"{PYTHON} is not readable by restricted application packages")
    return PYTHON


def policy(**overrides: Any) -> SandboxPolicy:
    base = SandboxPolicy(
        execution_timeout=60,
        termination=TerminationPolicy(verification_interval=0.05, retry_delay=0.05),
    )
    return replace(base, **overrides)


def python(code: str, **options: Any) -> ExecutionRequest:
    return ExecutionRequest(argv=(PYTHON, "-I", "-c", code), **options)


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


def run(request: ExecutionRequest, **options: object) -> ExecutionResult:
    backend_options: dict[str, Any] = {
        key: options.pop(key) for key in ("less_privileged", "read_only_paths") if key in options
    }

    async def check() -> ExecutionResult:
        backend = WindowsAppContainerBackend(**backend_options)
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy(**options)) as instance,
        ):
            return await instance.run(request)

    return asyncio.run(check())


def _tool(name: str) -> str:
    path = shutil.which(name)
    assert path is not None, name
    return path


def sddl(path: Path) -> str:
    return subprocess.run(
        [_tool("powershell"), "-NoProfile", "-Command", f"(Get-Acl -LiteralPath '{path}').Sddl"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


SPAWN_TREE = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
)


@pytest.mark.parametrize("less_privileged", [True, False])
def test_discovery_reports_limited_isolation(less_privileged: bool) -> None:
    backend = WindowsAppContainerBackend(less_privileged=less_privileged)
    report = asyncio.run(backend.discover())
    assert report.available, report.error
    assert report.name == "windows-appcontainer"
    states = {name: evidence.state for name, evidence in report.capabilities.items()}
    assert states[Capability.NETWORK_ISOLATION] is CapabilityState.SUPPORTED
    assert states[Capability.FILESYSTEM_ISOLATION] is CapabilityState.LIMITED
    assert states[Capability.PROCESS_ISOLATION] is CapabilityState.LIMITED
    assert states[Capability.CHECKPOINTS] is CapabilityState.UNSUPPORTED
    assert states[Capability.DISK_LIMIT] is CapabilityState.UNSUPPORTED
    assert any("share the host kernel" in item for item in report.limitations)
    restricted = "RESTRICTED" in report.capabilities[Capability.FILESYSTEM_ISOLATION].detail
    assert restricted is less_privileged


def test_unenforceable_policies_fail_closed(tmp_path: Path) -> None:
    async def check() -> None:
        async with Sandbox(backend=WindowsAppContainerBackend()) as sandbox:
            for rejected in (
                policy(network=NetworkAccess.ENABLED),
                policy(read_only_root=False),
                policy(workspace=True, scratch_bytes=1 << 20),
                policy(required_capabilities=(Capability.FILESYSTEM_ISOLATION,)),
                policy(required_capabilities=(Capability.CHECKPOINTS,)),
                policy(required_capabilities=(Capability.MEMORY_LIMIT,)),
            ):
                with pytest.raises(SandboxCapabilityError):
                    await sandbox.create(policy=rejected)
        missing = WindowsAppContainerBackend(read_only_paths=(tmp_path / "missing",))
        report = await missing.discover()
        assert not report.available
        assert "does not exist" in (report.error or "")

    asyncio.run(check())


def test_io_environment_and_working_directory(interpreter: str) -> None:
    os.environ["ISOLATTICE_HOST_ONLY"] = "secret"
    code = (
        "import json, os, sys; data = sys.stdin.buffer.read(); "
        "print(json.dumps([data.decode(), os.getcwd(), dict(os.environ)])); "
        "sys.stderr.write('warn'); sys.exit(3)"
    )
    try:
        result = run(python(code, stdin=b"input", environment={"GREETING": "hi"}))
    finally:
        del os.environ["ISOLATTICE_HOST_ONLY"]
    assert result.exit_code == 3
    assert result.stderr == b"warn"
    data, cwd, environment = json.loads(result.stdout)
    assert data == "input"
    assert Path(cwd).name.startswith("x-")
    names = {name.upper() for name in environment}
    assert {"GREETING", "SYSTEMROOT", "LOCALAPPDATA"} <= names
    assert "ISOLATTICE_HOST_ONLY" not in names
    assert "\\Packages\\isolattice." in environment.get("TEMP", "")


def test_relative_executable_and_bad_cwd_are_rejected(interpreter: str) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=WindowsAppContainerBackend()) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            for request in (
                ExecutionRequest(argv=("python.exe", "-V")),
                python("pass", cwd="/missing"),
                python("pass", cwd="/C:"),
            ):
                with pytest.raises(SandboxExecutionError):
                    await instance.run(request)

    asyncio.run(check())


def test_user_files_and_registry_are_denied(interpreter: str, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("host secret")
    code = (
        "import json, os, sys, winreg\n"
        "results = {}\n"
        "for name, action in [\n"
        f"    ('read', lambda: open({str(secret)!r}).read()),\n"
        f"    ('profile', lambda: os.listdir({str(Path.home())!r})),\n"
        f"    ('write', lambda: open({str(tmp_path / 'planted.txt')!r}, 'w').close()),\n"
        "    ('hkcu', lambda: winreg.CreateKey(winreg.HKEY_CURRENT_USER, 'Software\\\\Iso')),\n"
        "]:\n"
        "    try:\n"
        "        action()\n"
        "        results[name] = 'allowed'\n"
        "    except OSError as error:\n"
        "        results[name] = type(error).__name__\n"
        "print(json.dumps(results))\n"
    )
    result = run(python(code))
    outcome = json.loads(result.stdout)
    assert set(outcome.values()) == {"PermissionError"}, outcome
    assert not (tmp_path / "planted.txt").exists()


def test_loopback_network_is_denied(interpreter: str) -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        server.settimeout(0.5)
        port = server.getsockname()[1]
        code = (
            "import socket\n"
            "try:\n"
            f"    socket.create_connection(('127.0.0.1', {port}), timeout=3).close()\n"
            "    print('CONNECTED')\n"
            "except OSError as error:\n"
            "    print('DENIED', type(error).__name__)\n"
        )
        result = run(python(code))
        with pytest.raises(TimeoutError):
            server.accept()
    assert result.stdout.startswith(b"DENIED"), result


def test_host_processes_cannot_be_opened(interpreter: str) -> None:
    code = (
        "import ctypes\n"
        "kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)\n"
        "kernel32.OpenProcess.restype = ctypes.c_void_p\n"
        "handles = [kernel32.OpenProcess(access, False, "
        f"{os.getpid()}) for access in (0x0001, 0x0010, 0x0020)]\n"
        "print(sum(1 for handle in handles if handle))\n"
    )
    result = run(python(code))
    assert result.stdout.strip() == b"0", result


def test_scheduled_tasks_and_wmi_are_denied() -> None:
    schedule = run(
        ExecutionRequest(
            argv=(
                CMD,
                "/d",
                "/c",
                "schtasks /create /tn IsolatticeEscape /sc once /st 23:59 /tr cmd.exe /f",
            )
        )
    )
    assert schedule.exit_code != 0
    probe = subprocess.run(
        [_tool("schtasks"), "/query", "/tn", "IsolatticeEscape"], capture_output=True, check=False
    )
    assert probe.returncode != 0

    powershell = str(SYSTEM / "WindowsPowerShell" / "v1.0" / "powershell.exe")
    wmi = run(
        ExecutionRequest(
            argv=(
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "try { Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                "-Arguments @{CommandLine='cmd.exe /c exit'} -ErrorAction Stop; 'CREATED' } "
                "catch { 'DENIED' }",
            )
        ),
        less_privileged=False,
    )
    assert b"DENIED" in wmi.stdout, wmi
    assert b"CREATED" not in wmi.stdout


def test_workspace_persists_and_snapshots_restore(interpreter: str, tmp_path: Path) -> None:
    key = "workspace.tar"
    store = LocalArtifactStore(tmp_path)
    workspace = policy(workspace=True, required_capabilities=(Capability.SNAPSHOTS,))
    write = "import pathlib; p = pathlib.Path('d'); p.mkdir(); (p / 'file').write_text('one')"
    read = "print(open('d/file').read())"

    async def check() -> None:
        async with Sandbox(backend=WindowsAppContainerBackend(), artifact_store=store) as sandbox:
            async with await sandbox.create(policy=workspace) as first:
                assert first.workspace_path == "/workspace"
                assert (await first.run(python(write, cwd="/workspace"))).exit_code == 0
                again = await first.run(python(read, cwd="/workspace"))
                manifest = await first.snapshot(key)
            async with await sandbox.create(policy=workspace, workspace_snapshot=key) as second:
                restored = await second.run(python(read, cwd="/workspace"))
        assert again.stdout.strip() == b"one"
        assert manifest.backend == "windows-appcontainer"
        assert restored.stdout.strip() == b"one"

    asyncio.run(check())


def test_workload_locked_files_are_removed(interpreter: str, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    code = (
        "import os, pathlib, stat, subprocess\n"
        "p = pathlib.Path('locked'); p.mkdir(); f = p / 'file'; f.write_text('x')\n"
        "os.chmod(f, stat.S_IREAD)\n"
        "subprocess.run(['icacls', str(p), '/inheritance:r', '/deny', '*S-1-1-0:(OI)(CI)F'], "
        "capture_output=True)\n"
        "print('locked')\n"
    )

    async def check() -> None:
        backend = WindowsAppContainerBackend(state_directory=state)
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy(workspace=True)) as instance,
        ):
            result = await instance.run(python(code, cwd="/workspace"))
            assert result.stdout.strip() == b"locked"

    asyncio.run(check())
    assert list(state.iterdir()) == []


def test_read_only_paths_are_granted_then_revoked(interpreter: str, tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "data.txt").write_text("shared data")
    before = sddl(shared)
    code = (
        "import json\n"
        "results = []\n"
        f"results.append(open({str(shared / 'data.txt')!r}).read())\n"
        "try:\n"
        f"    open({str(shared / 'new.txt')!r}, 'w').close()\n"
        "    results.append('written')\n"
        "except PermissionError:\n"
        "    results.append('denied')\n"
        "print(json.dumps(results))\n"
    )

    async def check() -> None:
        backend = WindowsAppContainerBackend(read_only_paths=(shared,))
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            assert "S-1-15-2-" in sddl(shared)
            result = await instance.run(python(code))
        assert json.loads(result.stdout) == ["shared data", "denied"]

    asyncio.run(check())
    after = sddl(shared)
    assert "S-1-15-2-" not in after
    assert after.split("D:", 1)[1].lstrip("AI") == before.split("D:", 1)[1].lstrip("AI")


def test_memory_process_and_cpu_budgets(interpreter: str) -> None:
    spawn = (
        "import subprocess, sys\n"
        "children = []\n"
        "try:\n"
        "    for _ in range(5):\n"
        "        children.append(subprocess.Popen([sys.executable, '-I', '-c', "
        "'import time; time.sleep(5)']))\n"
        "except OSError:\n"
        "    pass\n"
        "print(len(children))\n"
    )
    burn = (
        "import time\n"
        "end = time.monotonic() + 2\n"
        "while time.monotonic() < end:\n"
        "    pass\n"
        "print(time.process_time())\n"
    )

    async def check() -> None:
        async with Sandbox(backend=WindowsAppContainerBackend()) as sandbox:
            async with await sandbox.create(
                policy=policy(memory_bytes=96 << 20, process_limit=3, cpu_cores=0.25)
            ) as instance:
                small = await instance.run(python("b = bytearray(16 << 20)"))
                large = await instance.run(python("b = bytearray(256 << 20)"))
                spawned = await instance.run(python(spawn))
                throttled = await instance.run(python(burn))
            async with await sandbox.create(policy=policy()) as instance:
                unthrottled = await instance.run(python(burn))
        assert small.exit_code == 0
        assert large.exit_code != 0
        assert b"MemoryError" in large.stderr
        assert spawned.stdout.strip() == b"2"
        assert float(throttled.stdout) < 1.0, throttled.stdout
        assert float(unthrottled.stdout) > 1.5, unthrottled.stdout

    asyncio.run(check())


def test_instance_budget_is_shared_across_executions(interpreter: str) -> None:
    hold = python("import time; b = bytearray(48 << 20); print('held', flush=True); time.sleep(4)")
    sleeper = python("import time; print('up', flush=True); time.sleep(4)")

    async def check() -> None:
        async with Sandbox(backend=WindowsAppContainerBackend()) as sandbox:
            async with await sandbox.create(
                policy=policy(
                    max_concurrent_executions=4,
                    instance_budget=ResourceBudget(memory_bytes=80 << 20),
                    required_capabilities=(Capability.INSTANCE_LIMITS,),
                )
            ) as instance:
                alone = await instance.run(hold)
                handles = [await instance.start(hold), await instance.start(hold)]
                together = await asyncio.gather(*(handle.wait() for handle in handles))
            async with await sandbox.create(
                policy=policy(
                    max_concurrent_executions=4,
                    instance_budget=ResourceBudget(process_limit=2),
                )
            ) as instance:
                running = [await instance.start(sleeper), await instance.start(sleeper)]
                for handle in running:
                    await anext(handle.output())
                with pytest.raises(SandboxError):
                    await (await instance.start(sleeper)).wait()
                finished = await asyncio.gather(*(handle.wait() for handle in running))
        assert alone.exit_code == 0
        codes = sorted(result.exit_code for result in together)
        assert codes[0] == 0, codes
        assert codes[1] != 0, codes
        assert any(b"MemoryError" in result.stderr for result in together)
        assert [result.exit_code for result in finished] == [0, 0]

    asyncio.run(check())


def test_descendants_breakaway_timeout_and_cancel(interpreter: str) -> None:
    breakaway = (
        "import subprocess, sys\n"
        "try:\n"
        "    subprocess.Popen([sys.executable, '-c', 'pass'], creationflags=0x01000000).wait()\n"
        "    print('ESCAPED')\n"
        "except OSError as error:\n"
        "    print('DENIED', error.winerror)\n"
    )

    async def check() -> None:
        async with (
            Sandbox(backend=WindowsAppContainerBackend()) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            tree = await instance.run(python(SPAWN_TREE))
            assert tree.exit_code == 0
            assert tree.termination.resources_released
            assert any("outlived the root" in note for note in tree.termination.notes)
            assert wait_exited(int(tree.stdout.strip()))

            denied = await instance.run(python(breakaway))
            assert denied.stdout.startswith(b"DENIED"), denied

            with pytest.raises(SandboxTimeoutError) as raised:
                await instance.run(python(SPAWN_TREE + "import time; time.sleep(60)", timeout=3))
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


def test_parent_crash_kills_the_tree(interpreter: str, tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    driver = (
        "import asyncio, os, sys\n"
        "from pathlib import Path\n"
        "from isolattice import ExecutionRequest, Sandbox, SandboxPolicy, "
        "WindowsAppContainerBackend\n"
        "async def main():\n"
        "    backend = WindowsAppContainerBackend(state_directory=Path(sys.argv[1]))\n"
        "    sandbox = Sandbox(backend=backend)\n"
        "    instance = await sandbox.create(policy=SandboxPolicy())\n"
        "    handle = await instance.start(ExecutionRequest(argv=(sys.argv[2], '-I', '-c', "
        "sys.argv[3])))\n"
        "    async for chunk in handle.output():\n"
        "        print(chunk.data.decode().strip(), flush=True)\n"
        "        os._exit(9)\n"
        "asyncio.run(main())\n"
    )
    before = profiles()
    completed = subprocess.run(
        [PYTHON, "-c", driver, str(state), PYTHON, SPAWN_TREE + "import time; time.sleep(60)"],
        capture_output=True,
        timeout=120,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        check=False,
    )
    assert completed.returncode == 9, completed
    assert wait_exited(int(completed.stdout.strip()))

    from isolattice.backends.windows_appcontainer import _delete_profile, _scratch_tree

    orphans = profiles() - before
    assert len(orphans) == 1, "a crashed host leaves its profile for reconciliation"
    for name in orphans:
        _delete_profile(name)
    for directory in state.iterdir():
        _scratch_tree(directory)
