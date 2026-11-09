"""Opt-in real Docker API tests; a skipped test is not enforcement evidence."""

import asyncio
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    DockerBackend,
    ExecutionRequest,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
)
from isolattice.backends import _bootstrap, docker

pytestmark = pytest.mark.docker
# Creates empty files in /tmp until the kernel refuses another inode.
INODE_FILL = (
    "i=0; while [ $i -lt 64 ] && touch /tmp/f$i 2>/dev/null; do i=$((i+1)); done; echo files=$i"
)

DockerConfig = tuple[str, str, str | None, bool]


def fast_termination() -> TerminationPolicy:
    return TerminationPolicy(
        graceful_timeout=0.2,
        force_timeout=4,
        verification_interval=0.1,
        retry_delay=0.1,
        total_timeout=60,
    )


def backend_for(config: DockerConfig) -> DockerBackend:
    host, image, executable, rootful = config
    return DockerBackend(
        host=host, image=image, executable=executable, allow_rootful=rootful, command_timeout=60
    )


async def command(
    *args: str,
    timeout: float = 120,  # noqa: ASYNC109 - also bounds killing the client
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await process.communicate()
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    assert process.returncode is not None
    return process.returncode, stdout, stderr


async def labelled_containers(config: DockerConfig) -> set[str]:
    host, _, executable, _ = config
    client = executable or shutil.which("docker")
    assert client is not None
    code, stdout, _ = await command(
        client,
        f"--host={host}",
        "ps",
        "--all",
        "--no-trunc",
        "--filter",
        f"label={docker._OWNER_LABEL}",
        "--format",
        "{{.ID}}",
    )
    assert code == 0
    return set(stdout.decode().split())


def test_real_restricted_execution_io_and_cleanup(
    docker_config: DockerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISOLATTICE_HOST_SECRET", f"host-only-{uuid.uuid4().hex}")
    script = (
        'printf "USER=%s\\n" "$(id -u)"; '
        'IFS= read -r line; printf "INPUT=%s\\n" "$line"; '
        'printf "EXPLICIT=%s\\n" "$ISOLATTICE_EXPLICIT"; '
        'printf "HOST_SECRET=%s\\n" "${ISOLATTICE_HOST_SECRET-unset}"; '
        "if touch /isolattice-readonly-probe 2>/dev/null; "
        "then echo WRITE_ALLOWED; else echo WRITE_DENIED; fi; "
        "routes=$(/sbin/ip route) || exit 98; "
        'if [ -z "$routes" ]; then echo NO_ROUTE; else echo HAS_ROUTE; fi; '
        """awk '/^(CapEff|CapBnd|NoNewPrivs|Seccomp):/ { print $1 $2 }' /proc/self/status; """
        'printf "CWD=%s\\n" "$(pwd)"; '
        "printf warning >&2; exit 7"
    )

    async def check() -> None:
        before = await labelled_containers(docker_config)
        policy = SandboxPolicy(execution_timeout=120, termination=fast_termination())
        async with Sandbox(backend=backend_for(docker_config)) as sandbox:
            (report,) = await sandbox.discover()
            assert report.available, report.error
            assert report.name == "docker"
            for capability in (
                Capability.FILESYSTEM_ISOLATION,
                Capability.PROCESS_ISOLATION,
                Capability.DISK_LIMIT,
            ):
                assert report.capabilities[capability].state is CapabilityState.LIMITED
            for capability in (
                Capability.WORKSPACE,
                Capability.SNAPSHOTS,
                Capability.CHECKPOINTS,
                Capability.INSTANCE_LIMITS,
            ):
                assert report.capabilities[capability].state is CapabilityState.UNSUPPORTED
            async with await sandbox.create(policy=policy) as instance:
                result = await instance.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", script),
                        stdin=b"provided-input\n",
                        environment={"ISOLATTICE_EXPLICIT": "value=accepted"},
                        cwd="/etc",
                    )
                )
        assert result.exit_code == 7
        for line in (
            b"USER=65534\n",
            b"INPUT=provided-input\n",
            b"EXPLICIT=value=accepted\n",
            b"HOST_SECRET=unset\n",
            b"WRITE_DENIED\n",
            b"NO_ROUTE\n",
            b"CapEff:0000000000000000\n",
            b"CapBnd:0000000000000000\n",
            b"NoNewPrivs:1\n",
            b"Seccomp:2\n",
            b"CWD=/etc\n",
        ):
            assert line in result.stdout, result.stdout
        assert _bootstrap.READY_PREFIX.encode() not in result.stdout
        assert result.stderr == b"warning"
        assert result.termination.state == "terminated"
        assert result.termination.resources_released
        assert result.termination.boundary_id not in await labelled_containers(docker_config)
        assert await labelled_containers(docker_config) == before

    asyncio.run(check())


def test_real_kernel_limits_and_scratch_are_enforced(docker_config: DockerConfig) -> None:
    script = (
        "for file in memory.max memory.swap.max cpu.max pids.max; do "
        'IFS= read -r value < "/sys/fs/cgroup/$file"; printf "%s=%s\\n" "$file" "$value"; done; '
        "if dd if=/dev/zero of=/tmp/fill bs=65536 count=64 2>/dev/null; "
        "then echo SCRATCH_UNBOUNDED; else echo SCRATCH_FULL; fi; "
        "( i=0; while [ $i -lt 32 ]; do sleep 30 & i=$((i+1)); done ) 2>/dev/null; "
        "while IFS=' ' read -r key value; do "
        '[ "$key" = max ] && printf "rejected_forks=%s\\n" "$value"; '
        "done < /sys/fs/cgroup/pids.events"
    )

    async def check() -> None:
        policy = SandboxPolicy(
            memory_bytes=64 * 1024 * 1024,
            cpu_cores=0.25,
            process_limit=8,
            scratch_bytes=1024 * 1024,
            required_capabilities=(
                Capability.MEMORY_LIMIT,
                Capability.CPU_LIMIT,
                Capability.PROCESS_LIMIT,
            ),
            execution_timeout=120,
            termination=fast_termination(),
        )
        async with (
            Sandbox(backend=backend_for(docker_config)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", script)))
        output = result.stdout.decode()
        assert "memory.max=67108864\n" in output
        assert "memory.swap.max=0\n" in output
        assert "cpu.max=25000 100000\n" in output
        assert "pids.max=8\n" in output
        assert "SCRATCH_FULL\n" in output
        rejected = int(output.split("rejected_forks=", 1)[1].split()[0])
        assert rejected > 0, output
        assert result.termination.state == "terminated"
        assert result.termination.resources_released

    asyncio.run(check())


def test_scratch_inode_count_is_capped(docker_config: DockerConfig) -> None:
    async def check() -> None:
        policy = SandboxPolicy(
            scratch_bytes=1024 * 1024,
            scratch_inodes=16,
            execution_timeout=120,
            termination=fast_termination(),
        )
        configured = backend_for(docker_config)
        before = await labelled_containers(docker_config)
        if (await configured._probe()).podman:
            async with Sandbox(backend=configured) as sandbox:
                with pytest.raises(SandboxCapabilityError, match="inode count"):
                    await sandbox.create(policy=policy)
            assert await labelled_containers(docker_config) == before
            return
        async with (
            Sandbox(backend=configured) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", INODE_FILL)))
        assert result.exit_code == 0, result.stderr
        assert 0 < int(result.stdout.decode().split("files=", 1)[1]) < 16, result.stdout
        assert result.termination.resources_released
        assert await labelled_containers(docker_config) == before

    asyncio.run(check())


def test_prelude_rejects_unexpected_kernel_state_and_cleans_up(
    docker_config: DockerConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container whose kernel view disagrees with the plan must never exec the workload."""

    def root_expected(self: docker._Limits) -> _bootstrap.KernelChecks:
        return _bootstrap.KernelChecks(uid=0, gid=0)

    async def check() -> None:
        before = await labelled_containers(docker_config)
        policy = SandboxPolicy(execution_timeout=120, termination=fast_termination())
        async with (
            Sandbox(backend=backend_for(docker_config)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            monkeypatch.setattr(docker._Limits, "checks", root_expected)
            with pytest.raises(SandboxCapabilityError, match="prelude"):
                await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "echo WORKLOAD_RAN")))
        assert await labelled_containers(docker_config) == before

    asyncio.run(check())


def test_real_timeout_reports_verified_cleanup(docker_config: DockerConfig) -> None:
    async def check() -> None:
        policy = SandboxPolicy(execution_timeout=20, termination=fast_termination())
        async with (
            Sandbox(backend=backend_for(docker_config)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            with pytest.raises(SandboxTimeoutError) as raised:
                await instance.run(
                    ExecutionRequest(argv=("/bin/sh", "-c", "trap '' TERM; sleep 120 & wait"))
                )
        report = raised.value.report
        assert report is not None
        assert report.state == "terminated"
        assert report.resources_released
        assert report.boundary_id not in await labelled_containers(docker_config)

    asyncio.run(check())


def test_unsupported_policies_fail_closed(docker_config: DockerConfig) -> None:
    async def check() -> None:
        async with Sandbox(backend=backend_for(docker_config)) as sandbox:
            for policy in (
                SandboxPolicy(workspace=True),
                SandboxPolicy(
                    scratch_bytes=1024 * 1024,
                    required_capabilities=(Capability.DISK_LIMIT,),
                ),
                SandboxPolicy(
                    required_capabilities=(Capability.FILESYSTEM_ISOLATION,),
                ),
            ):
                with pytest.raises(SandboxCapabilityError):
                    await sandbox.create(policy=policy)

    asyncio.run(check())


def test_rootful_engine_requires_explicit_opt_in(docker_config: DockerConfig) -> None:
    host, image, executable, rootful = docker_config
    if not rootful:
        pytest.skip("the configured Docker endpoint is rootless")

    async def check() -> None:
        backend = DockerBackend(host=host, image=image, executable=executable)
        report = await backend.discover()
        assert not report.available
        assert report.error is not None
        assert "not rootless" in report.error

    asyncio.run(check())


def test_missing_endpoint_does_not_fall_back(docker_config: DockerConfig) -> None:
    _, image, executable, rootful = docker_config
    missing = (
        f"npipe:////./pipe/isolattice-missing-{uuid.uuid4().hex}"
        if os.name == "nt"
        else f"unix:///nonexistent/isolattice-{uuid.uuid4().hex}.sock"
    )

    async def check() -> None:
        backend = DockerBackend(
            host=missing,
            image=image,
            executable=executable,
            allow_rootful=rootful,
            command_timeout=30,
        )
        report = await backend.discover()
        assert not report.available
        assert report.error

    asyncio.run(check())


EXAMPLE_CASES = [
    (
        "09-container-integrations",
        "docker_isolated_run.py",
        (
            "Backend: docker",
            "Docker API served by",
            "Unsupported workspace: rejected",
            "uid=65534",
            "caps=0000000000000000",
            "memory.max=67108864",
            "pids.max=16",
            "root=read-only",
            "stdin=from the host",
            "greeting=hello",
            "Owned container released: True",
        ),
    ),
]


def test_every_docker_example_has_a_live_test_case() -> None:
    root = Path(__file__).resolve().parents[2] / "examples"
    expected = {root / directory / filename for directory, filename, _ in EXAMPLE_CASES}
    assert set(root.rglob("docker_*.py")) == expected


@pytest.mark.parametrize(("directory", "filename", "expected"), EXAMPLE_CASES)
def test_runnable_docker_examples(
    docker_config: DockerConfig, directory: str, filename: str, expected: tuple[str, ...]
) -> None:
    host, image, executable, rootful = docker_config
    root = Path(__file__).resolve().parents[2]
    interpreter = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
    options = [
        *(("--executable", executable) if executable else ()),
        *(("--allow-rootful",) if rootful else ()),
    ]
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))

    async def run() -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            interpreter,
            str(root / "examples" / directory / filename),
            "--host",
            host,
            "--image",
            image,
            *options,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        async with asyncio.timeout(300):
            stdout, stderr = await process.communicate()
        assert process.returncode is not None
        return process.returncode, stdout, stderr

    code, stdout, stderr = asyncio.run(run())
    assert code == 0, (stdout, stderr)
    assert stderr == b"", stderr
    for text in expected:
        assert text in stdout.decode()
