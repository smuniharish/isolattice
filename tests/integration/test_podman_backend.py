"""Opt-in real-engine tests; a skipped test is not enforcement evidence."""

import asyncio
import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
)
from isolattice._async import raise_combined, settle
from isolattice.backends import podman
from isolattice.backends._cli import CommandResult

pytestmark = pytest.mark.podman


async def command(
    *args: str,
    environment: dict[str, str] | None = None,
    timeout: float = 30,  # noqa: ASYNC109 - also bounds killing the client
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=environment
    )
    communication = asyncio.create_task(process.communicate())
    try:
        async with asyncio.timeout(timeout):
            stdout, stderr = await asyncio.shield(communication)
    except BaseException as error:

        async def cleanup() -> None:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            async with asyncio.timeout(5):
                await communication

        outcome = await settle(asyncio.create_task(cleanup()))
        raise_combined("test client and cleanup failed", error, outcome.error, outcome.interrupted)
        raise AssertionError("unreachable") from None
    assert process.returncode is not None
    return process.returncode, stdout, stderr


def fast_termination() -> TerminationPolicy:
    return TerminationPolicy(
        graceful_timeout=0.2,
        force_timeout=4,
        verification_interval=0.1,
        retry_delay=0.1,
        total_timeout=45,
    )


def test_real_restricted_execution_io_and_cleanup(
    podman_config: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, image = podman_config
    monkeypatch.setenv("ISOLATTICE_HOST_SECRET", f"host-only-{uuid.uuid4().hex}")

    async def check() -> None:
        backend = PodmanBackend(connection=connection, image=image)
        policy = SandboxPolicy(execution_timeout=60, termination=fast_termination())
        script = (
            'printf "USER=%s\\n" "$(id -u)"; '
            'IFS= read -r line; printf "INPUT=%s\\n" "$line"; '
            'printf "EXPLICIT=%s\\n" "$ISOLATTICE_EXPLICIT"; '
            'printf "HOST_SECRET=%s\\n" "${ISOLATTICE_HOST_SECRET-unset}"; '
            "if touch /isolattice-readonly-probe 2>/dev/null; "
            "then echo WRITE_ALLOWED; else echo WRITE_DENIED; fi; "
            "routes=$(/sbin/ip route) || exit 98; "
            'if [ -z "$routes" ]; then echo NO_ROUTE; else echo HAS_ROUTE; fi; '
            """awk '/^CapEff:/ { print "CAPS=" $2 }' /proc/self/status; """
            "printf warning >&2; exit 7"
        )
        async with Sandbox(backend=backend) as sandbox:
            (report,) = await sandbox.discover()
            assert report.available, report.error
            assert (
                report.capabilities[Capability.PROCESS_ISOLATION].state is CapabilityState.LIMITED
            )
            assert (
                report.capabilities[Capability.RESOURCE_LIMITS].state
                is not CapabilityState.SUPPORTED
            )
            async with await sandbox.create(policy=policy) as instance:
                result = await instance.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", script),
                        stdin=b"provided-input\n",
                        environment={
                            "ISOLATTICE_EXPLICIT": "value=accepted",
                            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                        },
                    )
                )
                assert result.exit_code == 7
                assert b"USER=65534\n" in result.stdout
                assert b"INPUT=provided-input\n" in result.stdout
                assert b"EXPLICIT=value=accepted\n" in result.stdout
                assert b"HOST_SECRET=unset\n" in result.stdout
                assert b"WRITE_DENIED\n" in result.stdout
                assert b"NO_ROUTE\n" in result.stdout
                assert b"CAPS=0000000000000000\n" in result.stdout
                assert result.stderr == b"warning"
                assert result.termination.state == "terminated"
                assert result.termination.resources_released
                code, _, _ = await command(
                    "podman",
                    f"--connection={connection}",
                    "container",
                    "exists",
                    result.termination.boundary_id,
                )
                assert code == 1

    asyncio.run(check())


def test_real_concurrent_execution_and_bounded_capture(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config

    async def check() -> None:
        backend = PodmanBackend(connection=connection, image=image)
        policy = SandboxPolicy(
            execution_timeout=60,
            stdout_limit=128,
            stderr_limit=4,
            output_queue_chunks=1,
            termination=fast_termination(),
        )
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy) as instance,
            await sandbox.create(policy=policy) as separate_instance,
        ):
            large, other, separate = await asyncio.gather(
                instance.run(
                    ExecutionRequest(
                        argv=(
                            "/bin/sh",
                            "-c",
                            "head -c 100000 /dev/zero | tr '\\000' x; printf 123456 >&2",
                        ),
                        environment={"PATH": "/usr/bin:/bin"},
                    )
                ),
                instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "printf independent"))),
                separate_instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "printf separate"))),
            )
            assert large.exit_code == 0
            assert large.stdout == b"x" * 128
            assert large.stderr == b"1234"
            assert large.stdout_truncated
            assert large.stderr_truncated
            assert other.stdout == b"independent"
            assert separate.stdout == b"separate"
            assert separate.sandbox_id != other.sandbox_id
            assert large.termination.boundary_id != other.termination.boundary_id
            assert large.termination.resources_released
            assert other.termination.resources_released
            assert separate.termination.resources_released

    asyncio.run(check())


def test_real_forced_termination_of_parent_and_child(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config
    machine = os.environ.get("ISOLATTICE_TEST_PODMAN_MACHINE")

    async def check() -> None:
        backend = PodmanBackend(connection=connection, image=image)
        policy = SandboxPolicy(execution_timeout=60, termination=fast_termination())
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            handle = await instance.start(
                ExecutionRequest(argv=("/bin/sh", "-c", "sleep 60 & echo READY; wait"))
            )
            stream = handle.output()
            try:
                async with asyncio.timeout(45):
                    while b"READY" not in (await anext(stream)).data:
                        pass
            finally:
                await stream.aclose()
            assert handle._boundary is not None
            boundary = handle._boundary.boundary_id
            code, top, _ = await command(
                "podman", f"--connection={connection}", "top", boundary, "hpid", "pid", "args"
            )
            assert code == 0
            lines = top.decode().splitlines()
            assert len(lines) >= 3
            host_pids = [line.split()[0] for line in lines[1:]]
            assert all(pid.isdecimal() for pid in host_pids)
            report = await handle.cancel(force=True)
            assert report is not None
            assert report.state == "terminated"
            assert report.resources_released
            code, _, _ = await command(
                "podman", f"--connection={connection}", "container", "exists", boundary
            )
            assert code == 1
            if machine:
                code, remaining, _ = await command(
                    "podman",
                    "machine",
                    "ssh",
                    machine,
                    "ps",
                    "-p",
                    ",".join(host_pids),
                    "-o",
                    "pid=,args=",
                )
                assert code in (0, 1)
                assert not remaining.strip(), "recorded engine-host processes still exist"

    asyncio.run(check())


def test_real_timeout_reports_verified_cleanup(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config

    async def check() -> None:
        backend = PodmanBackend(connection=connection, image=image)
        policy = SandboxPolicy(execution_timeout=12, termination=fast_termination())
        async with (
            Sandbox(backend=backend) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            with pytest.raises(SandboxTimeoutError) as raised:
                await instance.run(
                    ExecutionRequest(argv=("/bin/sh", "-c", "trap '' TERM; sleep 60 & wait"))
                )
            report = raised.value.report
            assert report is not None
            assert report.state == "terminated"
            assert report.resources_released
            code, _, _ = await command(
                "podman",
                f"--connection={connection}",
                "container",
                "exists",
                report.boundary_id,
            )
            assert code == 1

    asyncio.run(check())


def test_unavailable_explicit_connection_does_not_fall_back(podman_config: tuple[str, str]) -> None:
    _, image = podman_config

    async def check() -> None:
        backend = PodmanBackend(connection=f"isolattice-missing-{uuid.uuid4().hex}", image=image)
        report = await backend.discover()
        assert not report.available
        assert report.error
        async with Sandbox(backend=backend) as sandbox:
            with pytest.raises(SandboxBackendError):
                await sandbox.create(policy=SandboxPolicy())

    asyncio.run(check())


EXAMPLE_CASES = [
    (
        "01-getting-started",
        "podman_hello.py",
        ("Hello from Isolattice", "Owned container released: True"),
        "podman",
    ),
    (
        "02-execution",
        "podman_io.py",
        (
            "stdin/environment/cwd: verified",
            "Bounded capture: abcdefgh",
            "Concurrent boundaries: distinct",
            "Owned container released: True",
        ),
        "podman",
    ),
    (
        "04-resource-management",
        "podman_limits.py",
        (
            "Kernel memory.max: 67108864",
            "Kernel memory.swap.max: 0",
            "Kernel cpu.max: 25000 100000",
            "Kernel pids.max: 8",
            "Owned container released: True",
        ),
        "podman-resources",
    ),
    (
        "04-resource-management",
        "podman_instance_budget.py",
        (
            "Single 40 MiB fill: fits the 64 MiB instance budget",
            "Two concurrent fills: refused by the shared budget",
            "Owned containers released: True",
            "Owned pod removed: True",
        ),
        "podman-rootful",
    ),
    (
        "12-snapshots",
        "podman_workspace_snapshot.py",
        (
            "Second execution reads: state from run 1",
            "New instance reads: state from run 1",
            "Owned volumes removed: True",
        ),
        "podman",
    ),
    (
        "12-snapshots",
        "podman_container_snapshot.py",
        (
            "Snapshot of the running workload:",
            "New instance reads: model weights v1",
            "Restored root is: read-only",
            "Owned containers and imported image removed: True",
        ),
        "podman",
    ),
    (
        "13-checkpoints",
        "podman_checkpoint.py",
        (
            "Checkpointed after the counter reached 5",
            "Restored execution continued from",
            "to 99",
            "Restored exit code: 0",
        ),
        "podman-rootful",
    ),
    (
        "06-async-and-cancellation",
        "podman_stream_cancel.py",
        ("Termination: terminated", "Owned container released: True"),
        "podman",
    ),
    (
        "07-capabilities-and-backends",
        "podman_capabilities.py",
        (
            "Backend: podman",
            "memory_limit:",
            "cpu_limit:",
            "process_limit:",
            "share the host kernel",
        ),
        "podman",
    ),
    (
        "14-customization",
        "podman_custom_extensions.py",
        (
            "Store conformance: passed",
            "Snapshot stored in SQLite: True",
            "Restored workspace reads: kept in SQLite",
            "Snapshot deleted: True",
            "Engine clients started through the custom runner:",
        ),
        "podman",
    ),
    (
        "15-observability",
        "podman_event_sink.py",
        (
            "Event: sandbox.created",
            "Event: execution.finished",
            "Event: sandbox.closed",
            "Workload output in events: False",
        ),
        "podman",
    ),
    (
        "18-failure-scenarios",
        "podman_failures.py",
        (
            "Unsupported network policy: rejected",
            "Timeout cleanup: terminated",
            "Owned container released: True",
        ),
        "podman",
    ),
]


def test_every_runnable_example_has_a_live_test_case() -> None:
    root = Path(__file__).resolve().parents[2] / "examples"
    expected = {root / directory / filename for directory, filename, _, _ in EXAMPLE_CASES}
    actual = set(root.rglob("podman_*.py"))
    assert actual == expected, "every Python example must appear in the live test matrix"
    assert len(EXAMPLE_CASES) == len(expected), "duplicate example test entries"


@pytest.mark.parametrize(
    ("directory", "filename", "expected", "example_config"),
    EXAMPLE_CASES,
    indirect=["example_config"],
)
def test_runnable_examples(
    example_config: tuple[str, str, bool],
    directory: str,
    filename: str,
    expected: tuple[str, ...],
) -> None:
    connection, image, allow_rootful = example_config
    root = Path(__file__).resolve().parents[2]
    script = root / "examples" / directory / filename
    executable = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    options = ("--allow-rootful",) if allow_rootful else ()
    code, stdout, stderr = asyncio.run(
        command(
            executable,
            str(script),
            "--connection",
            connection,
            "--image",
            image,
            *options,
            environment=environment,
            timeout=120,
        )
    )
    assert code == 0, (stdout, stderr)
    assert stderr == b"", stderr
    for text in expected:
        assert text in stdout.decode()


def test_cancel_after_real_allocation_reconciles_and_removes_container(
    podman_config: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    connection, image = podman_config

    async def check() -> None:
        original = podman._invoke
        allocated = asyncio.Event()
        gate = asyncio.Event()
        identifiers: list[str] = []

        async def invoke(
            engine: podman._Engine, operation: str, *args: str, accepted: tuple[int, ...] = (0,)
        ) -> CommandResult:
            result = await original(engine, operation, *args, accepted=accepted)
            if operation == "container creation":
                identifiers.append(result.stdout.decode().strip())
                allocated.set()
                await gate.wait()
            return result

        monkeypatch.setattr(podman, "_invoke", invoke)
        async with (
            Sandbox(backend=PodmanBackend(connection=connection, image=image)) as sandbox,
            await sandbox.create(
                policy=SandboxPolicy(
                    execution_timeout=60,
                    termination=fast_termination(),
                )
            ) as instance,
        ):
            handle = await instance.start(ExecutionRequest(argv=("/bin/sh", "-c", "exit 97")))
            async with asyncio.timeout(45):
                await allocated.wait()
            cancelling = asyncio.create_task(handle.cancel(force=True))
            try:
                done, _ = await asyncio.wait((cancelling,), timeout=30)
                assert done, "cancellation waited for an unnecessary allocation shield"
                report = await cancelling
            finally:
                gate.set()
            assert report is not None
            assert report.resources_released
            assert identifiers == [report.boundary_id]
            code, _, _ = await command(
                "podman",
                f"--connection={connection}",
                "container",
                "exists",
                report.boundary_id,
            )
            assert code == 1

    asyncio.run(check())


def test_resource_policy_matches_live_delegation(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config

    async def check() -> None:
        backend = PodmanBackend(connection=connection, image=image)
        report = await backend.discover()
        async with Sandbox(backend=backend) as sandbox:
            requested = SandboxPolicy(memory_bytes=67108864, execution_timeout=60)
            if report.capabilities[Capability.MEMORY_LIMIT].state is CapabilityState.SUPPORTED:
                async with await sandbox.create(policy=requested) as instance:
                    result = await instance.run(
                        ExecutionRequest(argv=("/bin/cat", "/sys/fs/cgroup/memory.max"))
                    )
                    assert result.exit_code == 0
                    assert result.stdout == b"67108864\n"
                    assert result.termination.resources_released
            else:
                with pytest.raises(SandboxCapabilityError, match="memory"):
                    await sandbox.create(policy=requested)

    asyncio.run(check())
