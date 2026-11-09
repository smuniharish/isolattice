"""Live Linux namespace backend tests.

Run inside a delegated cgroup, for example::

    scripts/linux/isolattice-delegated-run.sh sh -c \
        'ISOLATTICE_TEST_LINUX_CGROUP_ROOT=$ISOLATTICE_LINUX_CGROUP_ROOT python3 -m pytest \
        tests/integration/test_linux_backend.py'
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    LinuxNamespaceBackend,
    LocalArtifactStore,
    NetworkAccess,
    ResourceBudget,
    Sandbox,
    SandboxCapabilityError,
    SandboxExecutionError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux")

PYTHON = "/usr/bin/python3"
ROOT = Path(__file__).resolve().parents[2]
# Creates empty files in /tmp until the kernel refuses another inode.
INODE_FILL = (
    "i=0; while [ $i -lt 64 ] && touch /tmp/f$i 2>/dev/null; do i=$((i+1)); done; echo files=$i"
)


def policy(**overrides: Any) -> SandboxPolicy:
    base = SandboxPolicy(
        execution_timeout=30,
        termination=TerminationPolicy(
            graceful_timeout=2, verification_interval=0.05, retry_delay=0.05
        ),
    )
    return replace(base, **overrides)


def shell(script: str, **options: Any) -> ExecutionRequest:
    return ExecutionRequest(argv=("/bin/sh", "-c", script), **options)


def python(code: str, **options: Any) -> ExecutionRequest:
    return ExecutionRequest(argv=(PYTHON, "-c", textwrap.dedent(code)), **options)


def subgroups(cgroup: str | Path) -> list[Path]:
    return sorted(entry for entry in Path(cgroup).iterdir() if entry.is_dir())


def owned_children(cgroup_root: str) -> list[str]:
    return [entry.name for entry in subgroups(cgroup_root)]


@pytest.fixture
def backend(linux_cgroup_root: str, tmp_path: Path) -> LinuxNamespaceBackend:
    state = tmp_path / "state"
    state.mkdir()
    return LinuxNamespaceBackend(cgroup_root=linux_cgroup_root, state_directory=state)


def test_discovery_reports_enforced_capabilities(backend: LinuxNamespaceBackend) -> None:
    report = asyncio.run(backend.discover())
    assert report.available, report.error
    assert report.name == "linux-namespaces"
    for capability in (
        Capability.FILESYSTEM_ISOLATION,
        Capability.NETWORK_ISOLATION,
        Capability.PROCESS_ISOLATION,
        Capability.VERIFIED_TERMINATION,
        Capability.ENVIRONMENT_ISOLATION,
        Capability.MEMORY_LIMIT,
        Capability.CPU_LIMIT,
        Capability.PROCESS_LIMIT,
        Capability.INSTANCE_LIMITS,
        Capability.DISK_LIMIT,
        Capability.WORKSPACE,
        Capability.SNAPSHOTS,
    ):
        assert report.capabilities[capability].state is CapabilityState.SUPPORTED, capability
    assert report.capabilities[Capability.CHECKPOINTS].state is CapabilityState.UNSUPPORTED
    assert any("share the host kernel" in item for item in report.limitations)


def test_workload_identity_has_no_privileges(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(
                shell("id -u; id -g; hostname; cat /proc/self/status; echo $$")
            )
        assert result.exit_code == 0, result.stderr
        lines = result.stdout.decode().splitlines()
        assert lines[:3] == ["65534", "65534", "isolattice"]
        status = dict(line.split(":\t", 1) for line in lines[3:-1] if ":\t" in line)
        for field in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
            assert int(status[field], 16) == 0, field
        assert status["NoNewPrivs"].strip() == "1"
        assert status["Seccomp"].strip() == "2"
        assert lines[-1] == "1", "the workload must be the PID-namespace init"
        assert result.termination.resources_released

    asyncio.run(check())


def test_filesystem_is_read_only_and_private(
    backend: LinuxNamespaceBackend, tmp_path: Path
) -> None:
    marker = tmp_path / "host-secret"
    marker.write_text("secret")
    host_tmp = Path("/tmp") / f"isolattice-host-{os.getpid()}"
    host_tmp.write_text("host tmp")
    script = f"""
        for path in {marker} {host_tmp} {Path.home()} /sys/fs/cgroup /run /var /root; do
            test -e "$path" && echo "VISIBLE $path"
        done
        for path in /etc/isolattice /usr/isolattice /isolattice /dev/isolattice; do
            ( : > "$path" ) 2>/dev/null && echo "WRITABLE $path"
        done
        echo data > /tmp/scratch && cat /tmp/scratch
        ls /dev
        """
    try:

        async def check() -> None:
            async with (
                Sandbox(backend=backend) as sandbox,
                await sandbox.create(policy=policy()) as instance,
            ):
                result = await instance.run(shell(script))
            output = result.stdout.decode()
            assert "VISIBLE" not in output, output
            assert "WRITABLE" not in output, output
            assert "data" in output
            devices = set(output.split()[1:])
            assert {"null", "zero", "full", "random", "urandom"} <= devices
            assert not devices & {"sda", "mem", "kmsg", "vda", "sdb"}

        asyncio.run(check())
    finally:
        host_tmp.unlink()


def test_landlock_denies_writes_even_where_permissions_allow(
    linux_cgroup_root: str, tmp_path: Path
) -> None:
    # pytest's tmp_path is under /tmp, which the sandbox mounts privately; expose a
    # directory outside every sandbox-managed path instead.
    with tempfile.TemporaryDirectory(dir="/var/tmp", prefix="isolattice-exposed-") as host:
        exposed = Path(host) / "exposed"
        exposed.mkdir()
        (exposed / "file").write_text("readable")
        exposed.chmod(0o777)
        _check_landlock_denies_writes(linux_cgroup_root, tmp_path, exposed)


def _check_landlock_denies_writes(linux_cgroup_root: str, tmp_path: Path, exposed: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    backend = LinuxNamespaceBackend(
        cgroup_root=linux_cgroup_root,
        read_only_paths=(*("/usr", "/bin", "/lib", "/lib64", "/etc"), str(exposed)),
        state_directory=state,
    )

    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(
                shell(f"cat {exposed}/file; echo x > {exposed}/new 2>/dev/null || echo DENIED")
            )
        assert result.stdout == b"readableDENIED\n"
        assert not (exposed / "new").exists()

    asyncio.run(check())


def test_network_is_isolated(backend: LinuxNamespaceBackend) -> None:
    code = """
        import socket
        with open("/proc/net/dev") as handle:
            print(sorted(line.split(":")[0].strip() for line in handle.readlines()[2:]))
        for host, port in (("1.1.1.1", 53), ("127.0.0.1", 9)):
            try:
                socket.create_connection((host, port), timeout=2)
                print("CONNECTED", host)
            except OSError as error:
                print("blocked", type(error).__name__)
        try:
            socket.socket().bind(("127.0.0.1", 0))
            print("BOUND")
        except OSError as error:
            print("bind blocked", type(error).__name__)
        """

    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(python(code))
        output = result.stdout.decode()
        assert output.splitlines()[0] == "['lo']", output
        assert "CONNECTED" not in output, output
        assert "BOUND" not in output, output
        assert output.count("blocked") == 3, output

    asyncio.run(check())


def test_seccomp_denies_namespace_and_mount_syscalls(backend: LinuxNamespaceBackend) -> None:
    code = """
        import ctypes, os
        libc = ctypes.CDLL(None, use_errno=True)
        for name, flags in (("user", os.CLONE_NEWUSER), ("mount", os.CLONE_NEWNS)):
            try:
                os.unshare(flags)
                print("UNSHARED", name)
            except OSError as error:
                print(name, error.errno)
        print("mount", libc.mount(b"none", b"/tmp", b"tmpfs", 0, None), ctypes.get_errno())
        print("keyctl", libc.syscall(250, 0, 0, 0, 0, 0), ctypes.get_errno())
        print("bpf", libc.syscall(321, 0, 0, 0), ctypes.get_errno())
        print("io_uring", libc.syscall(425, 1, 0), ctypes.get_errno())
        print("fork", os.waitstatus_to_exitcode(os.waitpid(os.fork() or os._exit(3), 0)[1]))
        """

    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(python(code))
        lines = result.stdout.decode().splitlines()
        assert lines[:2] == ["user 1", "mount 1"], lines
        assert lines[2:6] == ["mount -1 1", "keyctl -1 1", "bpf -1 1", "io_uring -1 1"], lines
        assert lines[6] == "fork 3", "ordinary process creation must still work"

    asyncio.run(check())


def test_environment_stdin_and_working_directory(
    backend: LinuxNamespaceBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISOLATTICE_HOST_ONLY", "leak")

    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(
                shell(
                    "env | sort; pwd; cat",
                    environment={"GREETING": "hi"},
                    stdin=b"from stdin",
                    cwd="/usr",
                )
            )
            missing: SandboxExecutionError | int
            try:
                missing = (await instance.run(shell("true", cwd="/nonexistent"))).exit_code
            except SandboxExecutionError as error:
                missing = error
        lines = result.stdout.decode().splitlines()
        assert "GREETING=hi" in lines
        assert "ISOLATTICE_HOST_ONLY=leak" not in lines
        assert {line.split("=", 1)[0] for line in lines if "=" in line} <= {
            "GREETING",
            "PATH",
            "PWD",
        }
        assert lines[-2:] == ["/usr", "from stdin"]
        if isinstance(missing, SandboxExecutionError):
            assert "nonexistent" in str(missing) or "FileNotFound" in str(missing)
        else:
            assert missing != 0

    asyncio.run(check())


def test_missing_executable_is_a_start_error(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxExecutionError, match="not found"):
                await instance.run(ExecutionRequest(argv=("no-such-program",)))
            with pytest.raises(SandboxExecutionError, match="FileNotFoundError"):
                await instance.run(ExecutionRequest(argv=("/missing/program",)))

    asyncio.run(check())


def test_descendants_die_with_the_root(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(shell("sleep 300 & sleep 300 & echo started"))
        assert result.stdout == b"started\n"
        assert result.termination.state == "terminated"
        assert result.termination.resources_released

    asyncio.run(check())


def test_timeout_graceful_and_forced_termination(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with Sandbox(backend=backend) as sandbox:
            async with await sandbox.create(policy=policy()) as instance:
                with pytest.raises(SandboxTimeoutError) as raised:
                    await instance.run(shell("sleep 300 & sleep 300", timeout=1))
                report = raised.value.report
                assert report is not None
                assert report.resources_released
                assert report.state == "terminated"

                graceful = await instance.start(
                    shell(
                        "trap 'echo graceful; exit 0' TERM; echo ready; while :; do sleep 0.1; done"
                    )
                )
                output = graceful.output()
                assert (await anext(output)).data == b"ready\n"
                cancelled = await graceful.cancel()
                assert cancelled is not None
                assert cancelled.resources_released
                assert not any("cgroup.kill" in note for note in cancelled.notes)
                await output.aclose()

                forced = await instance.start(shell("trap '' TERM; echo ready; sleep 300"))
                output = forced.output()
                await anext(output)
                killed = await forced.cancel(force=True)
                assert killed is not None
                assert killed.resources_released
                assert any("cgroup.kill" in note for note in killed.notes), killed.notes
                await output.aclose()
            assert owned_children(backend._cgroup_root.as_posix()) == []

    asyncio.run(check())


def test_memory_cpu_and_process_budgets(backend: LinuxNamespaceBackend) -> None:
    burn = """
        import time
        end = time.monotonic() + 2
        while time.monotonic() < end:
            pass
        print(time.process_time())
        """
    forks = """
        import os, time
        children = []
        for _ in range(8):
            try:
                pid = os.fork()
            except OSError:
                break
            if pid == 0:
                time.sleep(3)
                os._exit(0)
            children.append(pid)
        print(len(children))
        """

    async def check() -> None:
        async with Sandbox(backend=backend) as sandbox:
            async with await sandbox.create(policy=policy(memory_bytes=64 << 20)) as instance:
                small = await instance.run(python("b = bytearray(16 << 20)"))
                large = await instance.run(python("b = bytearray(256 << 20); print(len(b))"))
            async with await sandbox.create(policy=policy(cpu_cores=0.25)) as instance:
                throttled = await instance.run(python(burn))
            async with await sandbox.create(policy=policy(process_limit=4)) as instance:
                limited = await instance.run(python(forks))
        assert small.exit_code == 0
        assert large.exit_code == 137, (large.exit_code, large.stderr)
        assert float(throttled.stdout) < 1.0, throttled.stdout
        assert limited.exit_code == 0, limited.stderr
        assert int(limited.stdout) == 3, limited.stdout

    asyncio.run(check())


def test_scratch_space_is_capped(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(
                policy=policy(scratch_bytes=2 << 20, required_capabilities=(Capability.DISK_LIMIT,))
            ) as instance,
        ):
            result = await instance.run(
                shell("dd if=/dev/zero of=/tmp/fill bs=1M count=8 2>&1; echo rc=$?")
            )
        assert b"No space left" in result.stdout, result.stdout
        assert result.stdout.strip().endswith(b"rc=1")

    asyncio.run(check())


def test_scratch_inode_count_is_capped(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(
                policy=policy(
                    scratch_bytes=1 << 20,
                    scratch_inodes=16,
                    required_capabilities=(Capability.DISK_LIMIT,),
                )
            ) as instance,
        ):
            result = await instance.run(shell(INODE_FILL))
        assert result.exit_code == 0, result.stderr
        assert 0 < int(result.stdout.split(b"files=", 1)[1]) < 16, result.stdout

    asyncio.run(check())


def test_instance_budget_is_shared(backend: LinuxNamespaceBackend) -> None:
    fill = shell("dd if=/dev/zero of=/tmp/fill bs=1M count=40 2>/dev/null || exit 3; sleep 3")

    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(
                policy=policy(
                    instance_budget=ResourceBudget(memory_bytes=64 << 20, process_limit=16),
                    scratch_bytes=48 << 20,
                    required_capabilities=(Capability.INSTANCE_LIMITS,),
                )
            ) as instance,
        ):
            alone = await instance.run(fill)
            handles = [await instance.start(fill), await instance.start(fill)]
            results = await asyncio.gather(*(handle.wait() for handle in handles))
        assert alone.exit_code == 0
        codes = sorted(result.exit_code for result in results)
        assert codes != [0, 0], codes
        assert set(codes) <= {0, 3, 137}, codes

    asyncio.run(check())


def test_workspace_persists_and_snapshots_restore(
    backend: LinuxNamespaceBackend, tmp_path: Path
) -> None:
    key = "workspace.tar"
    workspace = policy(workspace=True, required_capabilities=(Capability.SNAPSHOTS,))
    store = LocalArtifactStore(tmp_path)

    async def check() -> None:
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=workspace) as first:
                assert first.workspace_path == "/workspace"
                await first.run(
                    shell("mkdir -p d && echo one > d/file && ln -s d/file link", cwd="/workspace")
                )
                again = await first.run(shell("cat /workspace/link"))
                manifest = await first.snapshot(key)
            async with await sandbox.create(policy=workspace, workspace_snapshot=key) as second:
                restored = await second.run(
                    shell("cat /workspace/d/file; readlink /workspace/link")
                )
        assert again.stdout == b"one\n"
        assert manifest.backend == "linux-namespaces"
        assert restored.stdout == b"one\nd/file\n"
        assert list((tmp_path / "state").iterdir()) == []

    asyncio.run(check())


def test_unenforceable_policies_fail_closed(backend: LinuxNamespaceBackend) -> None:
    async def check() -> None:
        async with Sandbox(backend=backend) as sandbox:
            for bad, message in (
                (policy(network=NetworkAccess.ENABLED), "network=NONE"),
                (policy(read_only_root=False), "read-only root"),
                (policy(required_capabilities=(Capability.CHECKPOINTS,)), "checkpoints"),
                (
                    policy(
                        workspace=True,
                        scratch_bytes=1 << 20,
                        required_capabilities=(Capability.DISK_LIMIT,),
                    ),
                    "no quota",
                ),
                (policy(required_capabilities=(Capability.MEMORY_LIMIT,)), "explicit budget"),
            ):
                with pytest.raises(SandboxCapabilityError, match=message):
                    await sandbox.create(policy=bad)

    asyncio.run(check())


def test_cleanup_leaves_no_cgroups_or_state(backend: LinuxNamespaceBackend, tmp_path: Path) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy(workspace=True)) as instance,
        ):
            await instance.run(shell("chmod 000 /workspace; mkdir /tmp/x"))
            assert len(owned_children(backend._cgroup_root.as_posix())) == 1

    asyncio.run(check())
    assert owned_children(backend._cgroup_root.as_posix()) == []
    assert list((tmp_path / "state").iterdir()) == []


def test_caller_crash_kills_the_contained_tree(linux_cgroup_root: str) -> None:
    driver = textwrap.dedent(
        f"""
        import asyncio
        from isolattice import ExecutionRequest, LinuxNamespaceBackend, Sandbox, SandboxPolicy
        async def main():
            backend = LinuxNamespaceBackend(cgroup_root={linux_cgroup_root!r})
            sandbox = Sandbox(backend=backend)
            instance = await sandbox.create(policy=SandboxPolicy())
            handle = await instance.start(ExecutionRequest(
                argv=("/bin/sh", "-c", "sleep 300 & echo $! ; sleep 300")))
            async for chunk in handle.output():
                print("ready", flush=True)
                await asyncio.sleep(300)
        asyncio.run(main())
        """
    )
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    with subprocess.Popen(
        [sys.executable, "-c", driver], stdout=subprocess.PIPE, env=environment
    ) as process:
        try:
            assert process.stdout is not None
            assert process.stdout.readline() == b"ready\n"
            leaves = [
                leaf for instance in subgroups(linux_cgroup_root) for leaf in subgroups(instance)
            ]
            assert len(leaves) == 1
            members = (leaves[0] / "cgroup.procs").read_text().split()
            assert len(members) == 3, "the shell and both sleeps share the execution leaf"
            process.kill()
            process.wait()
            end = time.monotonic() + 10
            while time.monotonic() < end and (leaves[0] / "cgroup.events").read_text().count(
                "populated 1"
            ):
                time.sleep(0.05)
            assert "populated 0" in (leaves[0] / "cgroup.events").read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for instance in subgroups(linux_cgroup_root):
                for leaf in subgroups(instance):
                    leaf.rmdir()
                instance.rmdir()


EXAMPLES = [
    (
        "08-native-backends",
        "linux_namespaces.py",
        (
            "Backend: linux-namespaces (available: True)",
            "Identity: uid 65534, no capabilities, no_new_privs, seccomp filter",
            "Filesystem: host home hidden, root read-only",
            "Network: blocked",
            "Memory budget: allocation beyond 64 MiB was OOM-killed",
            "Timeout cleanup: terminated",
            "Workspace snapshot: restored into a new instance",
            "Required checkpoints: rejected",
        ),
    ),
]


def test_every_linux_example_has_a_live_test_case() -> None:
    expected = {ROOT / "examples" / directory / name for directory, name, _ in EXAMPLES}
    assert set((ROOT / "examples").rglob("linux_*.py")) == expected


@pytest.mark.parametrize(("directory", "filename", "expected"), EXAMPLES)
def test_linux_examples(
    linux_cgroup_root: str, directory: str, filename: str, expected: tuple[str, ...]
) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "examples" / directory / filename)],
        capture_output=True,
        timeout=120,
        env=dict(
            os.environ, PYTHONPATH=str(ROOT / "src"), ISOLATTICE_LINUX_CGROUP_ROOT=linux_cgroup_root
        ),
        check=False,
    )
    assert completed.returncode == 0, completed
    assert completed.stderr == b"", completed.stderr
    for text in expected:
        assert text in completed.stdout.decode()
    assert subgroups(linux_cgroup_root) == []
