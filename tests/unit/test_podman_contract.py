from __future__ import annotations

import asyncio
import json
import os
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    PodmanBackend,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxError,
    SandboxExecutionError,
    SandboxPolicy,
    TerminationVerificationError,
)
from isolattice.backend import Boundary
from isolattice.backends import _cli, podman


def engine() -> podman._Engine:
    return podman._Engine(
        "podman", "explicit-test-connection", 1, "b" * 64, frozenset({"CAP_KILL"})
    )


def boundary() -> Boundary:
    return Boundary("a" * 64, "owned", "sandbox", "execution")


def exception_tree(error: BaseException) -> list[BaseException]:
    result = [error]
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            result.extend(exception_tree(child))
    if error.__cause__ is not None:
        result.extend(exception_tree(error.__cause__))
    return result


def configuration() -> dict[str, object]:
    return {
        "Id": "a" * 64,
        "Image": "b" * 64,
        "Mounts": [],
        "Config": {
            "Labels": {
                "io.isolattice.sandbox": "sandbox",
                "io.isolattice.execution": "execution",
            },
            "User": "65534:65534",
            "WorkingDir": "/",
            "Volumes": None,
            "Env": [],
            "Entrypoint": ["/bin/echo"],
            "Cmd": ["hello"],
            "Tty": False,
            "OpenStdin": False,
            "Healthcheck": {"Test": ["NONE"]},
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "PidMode": "private",
            "IpcMode": "private",
            "UTSMode": "private",
            "CgroupMode": "private",
            "Privileged": False,
            "CapAdd": [],
            "CapDrop": ["CAP_KILL"],
            "SecurityOpt": ["no-new-privileges"],
            "Devices": [],
            "Binds": [],
            "LogConfig": {"Type": "none"},
            "RestartPolicy": {"Name": "no"},
        },
    }


@pytest.mark.parametrize(
    ("section", "key", "invalid"),
    [
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "UTSMode", "host"),
        ("HostConfig", "CgroupMode", "host"),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "CapAdd", ["CAP_SYS_ADMIN"]),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "SecurityOpt", []),
        ("HostConfig", "Devices", ["device"]),
        ("HostConfig", "Binds", ["/host:/host"]),
        ("HostConfig", "LogConfig", {"Type": "journald"}),
        ("HostConfig", "RestartPolicy", {"Name": "always"}),
        ("Config", "User", "0:0"),
        ("Config", "WorkingDir", "/other"),
        ("Config", "Volumes", {"/host": {}}),
        ("Config", "Env", ["HOST_SECRET=leaked"]),
        ("Config", "Entrypoint", ["/bin/other"]),
        ("Config", "Cmd", ["changed"]),
        ("Config", "Tty", True),
        ("Config", "OpenStdin", True),
        ("Config", "Healthcheck", {"Test": ["CMD", "probe"]}),
        ("", "Image", "c" * 64),
        ("", "Mounts", ["unexpected"]),
    ],
)
def test_effective_configuration_mismatch_fails_closed(
    section: str, key: str, invalid: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())
        data = deepcopy(configuration())
        target = data if not section else data[section]
        assert isinstance(target, dict)
        target[key] = invalid

        async def inspect(_: Boundary) -> dict[str, object]:
            return data

        monkeypatch.setattr(session, "_inspect", inspect)
        with pytest.raises(SandboxCapabilityError):
            await session._validate(boundary(), ExecutionRequest(argv=("/bin/echo", "hello")))

    asyncio.run(check())


def test_valid_effective_configuration_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())

        async def inspect(_: Boundary) -> dict[str, object]:
            return configuration()

        monkeypatch.setattr(session, "_inspect", inspect)
        await session._validate(boundary(), ExecutionRequest(argv=("/bin/echo", "hello")))

    asyncio.run(check())


@pytest.mark.parametrize("operation", ["signal", "remove"])
def test_foreign_container_is_never_signalled_or_removed(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())
        driver = podman._PodmanDriver(engine(), session, boundary())
        invoked = False

        async def inspect(_: Boundary) -> dict[str, object]:
            raise SandboxBackendError("ownership mismatch")

        async def invoke(*args: object, **kwargs: object) -> _cli.CommandResult:
            nonlocal invoked
            invoked = True
            return _cli.CommandResult(b"", b"", 0)

        monkeypatch.setattr(session, "_inspect", inspect)
        monkeypatch.setattr(podman, "_invoke", invoke)
        action = driver.signal(force=True) if operation == "signal" else driver.remove()
        with pytest.raises(SandboxBackendError, match="ownership"):
            await action
        assert not invoked

    asyncio.run(check())


@pytest.mark.parametrize("reconciliation_fails", [False, True])
def test_uncertain_creation_reports_the_unique_name(
    reconciliation_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())

        async def invoke(*args: object, **kwargs: object) -> _cli.CommandResult:
            raise _cli.CommandError("injected transport failure", possibly_accepted=True)

        async def reconcile(name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
            if reconciliation_fails:
                raise SandboxBackendError("engine unavailable")
            return None

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_reconcile", reconcile)
        registered: list[Boundary] = []
        with pytest.raises(
            TerminationVerificationError, match="isolattice-sandbox-execution"
        ) as raised:
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo", "hello")),
                "sandbox",
                "execution",
                registered.append,
            )
        assert registered == []
        errors = exception_tree(raised.value)
        assert any("injected transport failure" in str(error) for error in errors)
        if reconciliation_fails:
            assert any("engine unavailable" in str(error) for error in errors)

    asyncio.run(check())


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_control_output_overflow_closes_client_pipes(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        executable = (
            str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
        )
        processes: list[asyncio.subprocess.Process] = []
        original_reap = _cli.reap_client

        def command(_: podman._Engine, *args: str) -> tuple[str, ...]:
            code = (
                f"import sys\nout=sys.{stream}.buffer\nchunk=b'x'*65536\n"
                "while True:\n out.write(chunk)\n out.flush()"
            )
            return (executable, "-c", code)

        async def reap(process: asyncio.subprocess.Process, product: str) -> None:
            processes.append(process)
            await original_reap(process, product)

        monkeypatch.setattr(podman._Engine, "command", command)
        monkeypatch.setattr(_cli, "reap_client", reap)
        try:
            with pytest.raises(SandboxBackendError, match="control output exceeded"):
                await podman._invoke(engine(), "overflow probe")
            assert len(processes) == 1
            process = processes[0]
            assert process.returncode is not None
            assert process.stdout is not None
            assert process.stdout.at_eof()
            assert process.stderr is not None
            assert process.stderr.at_eof()
        finally:
            for process in processes:
                for reader in (process.stdout, process.stderr):
                    if reader is not None:
                        async with asyncio.timeout(5):
                            await reader.read()

    asyncio.run(check())


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("reconciliation", ["found", "missing", "fails"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_oversized_control_output_reconciles_creation(
    stream: str, reconciliation: str, cleanup_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())
        executable = (
            str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
        )
        reconciled: list[str] = []
        original_reap = _cli.reap_client

        def command(_: podman._Engine, *args: str) -> tuple[str, ...]:
            return (executable, "-c", f"import sys; sys.{stream}.buffer.write(b'x' * 17)")

        async def reconcile(name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
            reconciled.append(name)
            if reconciliation == "fails":
                raise SandboxBackendError("injected reconciliation failure")
            if reconciliation == "missing":
                return None
            return Boundary("a" * 64, name, sandbox_id, execution_id)

        async def reap(process: asyncio.subprocess.Process, product: str) -> None:
            await original_reap(process, product)
            if cleanup_fails:
                raise SandboxBackendError("injected client cleanup failure")

        monkeypatch.setattr(_cli, "MAX_CONTROL_OUTPUT", 16)
        monkeypatch.setattr(podman._Engine, "command", command)
        monkeypatch.setattr(_cli, "reap_client", reap)
        monkeypatch.setattr(session, "_reconcile", reconcile)
        registered: list[Boundary] = []
        with pytest.raises((SandboxError, BaseExceptionGroup)) as raised:
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo", "hello")),
                "sandbox",
                "execution",
                registered.append,
            )

        errors = exception_tree(raised.value)
        assert any("control output exceeded" in str(error) for error in errors)
        assert reconciled == ["isolattice-sandbox-execution"]
        if cleanup_fails:
            assert any("injected client cleanup failure" in str(error) for error in errors)
        if reconciliation == "found":
            assert [item.boundary_id for item in registered] == ["a" * 64]
        else:
            assert registered == []
            assert any(
                isinstance(error, TerminationVerificationError)
                and "isolattice-sandbox-execution" in str(error)
                for error in errors
            )
        if reconciliation == "fails":
            assert any("injected reconciliation failure" in str(error) for error in errors)

    asyncio.run(check())


def test_creation_spawn_failure_does_not_attempt_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())
        reconciled = False

        async def invoke(*args: object, **kwargs: object) -> _cli.CommandResult:
            raise _cli.CommandError("injected spawn failure", possibly_accepted=False)

        async def reconcile(name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
            nonlocal reconciled
            reconciled = True
            return None

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_reconcile", reconcile)
        with pytest.raises(_cli.CommandError, match="injected spawn failure"):
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo", "hello")),
                "sandbox",
                "execution",
                lambda _: None,
            )
        assert not reconciled

    asyncio.run(check())


@pytest.mark.parametrize("reconciliation", ["found", "missing", "fails"])
@pytest.mark.parametrize("grouped", [False, True])
def test_interrupted_creation_preserves_cancellation_and_ownership_uncertainty(
    reconciliation: str, grouped: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())

        async def invoke(*args: object, **kwargs: object) -> _cli.CommandResult:
            cancelled = asyncio.CancelledError("injected cancellation")
            if grouped:
                raise BaseExceptionGroup(
                    "injected cleanup failure",
                    [cancelled, SandboxBackendError("injected client cleanup failure")],
                )
            raise cancelled

        async def reconcile(name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
            if reconciliation == "fails":
                raise SandboxBackendError("injected reconciliation failure")
            if reconciliation == "missing":
                return None
            return Boundary("a" * 64, name, sandbox_id, execution_id)

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_reconcile", reconcile)
        registered: list[Boundary] = []
        with pytest.raises((asyncio.CancelledError, BaseExceptionGroup)) as raised:
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo", "hello")),
                "sandbox",
                "execution",
                registered.append,
            )
        errors = exception_tree(raised.value)
        assert any(isinstance(error, asyncio.CancelledError) for error in errors)
        if grouped:
            assert any("injected client cleanup failure" in str(error) for error in errors)
        if reconciliation == "found":
            assert [item.boundary_id for item in registered] == ["a" * 64]
        else:
            assert registered == []
            assert any(
                isinstance(error, TerminationVerificationError)
                and "isolattice-sandbox-execution" in str(error)
                for error in errors
            )
        if reconciliation == "fails":
            assert any("injected reconciliation failure" in str(error) for error in errors)

    asyncio.run(check())


def test_interrupted_reconciliation_preserves_the_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())

        async def invoke(*args: object, **kwargs: object) -> _cli.CommandResult:
            raise SandboxBackendError("injected post-acceptance failure")

        async def reconcile(name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
            raise asyncio.CancelledError("injected reconciliation cancellation")

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_reconcile", reconcile)
        registered: list[Boundary] = []
        with pytest.raises(BaseExceptionGroup) as raised:
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo", "hello")),
                "sandbox",
                "execution",
                registered.append,
            )
        errors = exception_tree(raised.value)
        assert registered == []
        assert any("injected post-acceptance failure" in str(error) for error in errors)
        assert any(isinstance(error, asyncio.CancelledError) for error in errors)
        assert any(
            isinstance(error, TerminationVerificationError)
            and "isolattice-sandbox-execution" in str(error)
            for error in errors
        )

    asyncio.run(check())


def test_environment_file_is_removed_and_not_exposed_in_client_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        session = podman._PodmanSession(engine())
        observed_file: Path | None = None

        async def invoke(
            engine_value: podman._Engine, operation: str, *args: str, **kwargs: object
        ) -> _cli.CommandResult:
            nonlocal observed_file
            assert operation == "container creation"
            assert "sensitive-value" not in repr(args)
            observed_file = Path(args[args.index("--env-file") + 1])
            assert observed_file.is_file()
            return _cli.CommandResult(("a" * 64).encode(), b"", 0)

        async def validate(_: Boundary, request: ExecutionRequest) -> None:
            raise SandboxCapabilityError("injected configuration mismatch")

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_validate", validate)
        registered: list[Boundary] = []
        with pytest.raises(SandboxCapabilityError):
            await session.create_boundary(
                ExecutionRequest(argv=("/bin/echo",), environment={"TOKEN": "sensitive-value"}),
                "sandbox",
                "execution",
                registered.append,
            )
        assert len(registered) == 1
        assert observed_file is not None
        assert not await asyncio.to_thread(observed_file.exists)

    asyncio.run(check())


def resource_configuration(limits: podman._Limits, request: ExecutionRequest) -> dict[str, object]:
    data = configuration()
    host = data["HostConfig"]
    config = data["Config"]
    assert isinstance(host, dict)
    assert isinstance(config, dict)
    host.update(
        Memory=67108864,
        MemorySwap=67108864,
        CpuPeriod=100000,
        CpuQuota=25000,
        PidsLimit=8,
        OomKillDisable=False,
    )
    command = limits.command(request, "execution")
    config["Entrypoint"] = [command[0]]
    config["Cmd"] = list(command[1:])
    return data


@pytest.mark.parametrize(
    ("key", "invalid"),
    [
        ("Memory", 134217728),
        ("MemorySwap", -1),
        ("CpuPeriod", 200000),
        ("CpuQuota", 50000),
        ("PidsLimit", 0),
        ("OomKillDisable", True),
    ],
)
def test_resource_configuration_mismatch_is_rejected(
    key: str, invalid: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        limits = podman._Limits(67108864, 25000, 8)
        session = podman._PodmanSession(engine(), limits)
        request = ExecutionRequest(argv=("/bin/echo", "hello"))
        data = resource_configuration(limits, request)
        host = data["HostConfig"]
        assert isinstance(host, dict)
        host[key] = invalid

        async def inspect(_: Boundary) -> dict[str, object]:
            return data

        monkeypatch.setattr(session, "_inspect", inspect)
        with pytest.raises(SandboxCapabilityError, match="resource"):
            await session._validate(boundary(), request)

    asyncio.run(check())


def test_resource_flags_and_bootstrap_are_inspected_before_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def check() -> None:
        limits = podman._Limits(67108864, 25000, 8)
        session = podman._PodmanSession(engine(), limits)
        request = ExecutionRequest(argv=("/bin/echo", "hello; not a shell command"))
        observed: list[str] = []

        async def invoke(
            _: podman._Engine, operation: str, *args: str, **kwargs: object
        ) -> _cli.CommandResult:
            assert operation == "container creation"
            observed.extend(args)
            return _cli.CommandResult(b"a" * 64, b"", 0)

        async def inspect(_: Boundary) -> dict[str, object]:
            return resource_configuration(limits, request)

        monkeypatch.setattr(podman, "_invoke", invoke)
        monkeypatch.setattr(session, "_inspect", inspect)
        registered: list[Boundary] = []
        created = await session.create_boundary(request, "sandbox", "execution", registered.append)
        assert registered == [created]
        for flag, value in {
            "--memory": "67108864",
            "--memory-swap": "67108864",
            "--cpu-period": "100000",
            "--cpu-quota": "25000",
            "--pids-limit": "8",
        }.items():
            assert observed[observed.index(flag) + 1] == value
        command = limits.command(request, "execution")
        assert command[0:2] == ("/bin/sh", "-c")
        assert command[-2:] == request.argv
        assert "/sys/fs/cgroup/memory.max" in command[2]
        assert "/sys/fs/cgroup/memory.swap.max" in command[2]
        assert "/sys/fs/cgroup/cpu.max" in command[2]
        assert "/sys/fs/cgroup/pids.max" in command[2]

    asyncio.run(check())


@pytest.mark.parametrize(
    "values",
    [
        {"memory_bytes": 1024},
        {"memory_bytes": 2**63},
        {"cpu_cores": 0.001},
        {"cpu_cores": 0.333333},
        {"cpu_cores": 1e15},
        {"process_limit": 2**63},
    ],
)
def test_unrepresentable_resource_budgets_are_rejected(values: dict[str, Any]) -> None:
    policy = SandboxPolicy(**values)
    with pytest.raises(SandboxCapabilityError):
        podman._Limits.from_policy(policy)


@pytest.mark.parametrize("version", ["v1", "v2"])
@pytest.mark.parametrize("controllers", [frozenset(), frozenset({"memory", "cpu", "pids"})])
def test_resource_capabilities_require_v2_and_delegated_controllers(
    version: str, controllers: frozenset[str]
) -> None:
    configured = replace(engine(), cgroup_version=version, controllers=controllers)
    report = podman._report(True, engine=configured)
    expected = (
        CapabilityState.SUPPORTED
        if version == "v2" and controllers
        else CapabilityState.UNSUPPORTED
    )
    for capability in (Capability.MEMORY_LIMIT, Capability.CPU_LIMIT, Capability.PROCESS_LIMIT):
        assert report.capabilities[capability].state is expected
    assert report.capabilities[Capability.RESOURCE_LIMITS].state is not CapabilityState.SUPPORTED


@pytest.mark.parametrize("missing", ["memory", "cpu", "pids"])
def test_missing_controller_rejects_resource_policy(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        backend = PodmanBackend(connection="explicit", image="local")

        async def probe() -> podman._Engine:
            return replace(
                engine(),
                cgroup_version="v2",
                controllers=frozenset({"memory", "cpu", "pids"} - {missing}),
            )

        monkeypatch.setattr(backend, "_probe", probe)
        with pytest.raises(SandboxCapabilityError, match=missing):
            await backend.prepare(
                SandboxPolicy(
                    memory_bytes=67108864,
                    cpu_cores=0.25,
                    process_limit=8,
                ),
                "sandbox",
            )

    asyncio.run(check())


@pytest.mark.parametrize("allow_rootful", [False, True])
def test_rootful_engine_requires_explicit_opt_in(
    allow_rootful: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        backend = PodmanBackend(
            connection="explicit",
            image="local",
            allow_rootful=allow_rootful,
            executable="podman",
        )

        async def invoke(
            _: podman._Engine, operation: str, *args: str, **kwargs: object
        ) -> _cli.CommandResult:
            if operation == "security discovery":
                host = {
                    "security": {
                        "rootless": False,
                        "seccompEnabled": True,
                        "capabilities": "CAP_KILL",
                    },
                    "cgroupVersion": "v2",
                    "cgroupControllers": ["memory", "cpu", "pids"],
                }
                return _cli.CommandResult(json.dumps(host).encode(), b"", 0)
            assert operation == "local image discovery"
            assert allow_rootful
            return _cli.CommandResult(b"b" * 64, b"", 0)

        monkeypatch.setattr(podman, "_invoke", invoke)
        if allow_rootful:
            configured = await backend._probe()
            assert not configured.rootless
        else:
            with pytest.raises(SandboxCapabilityError, match="rootless"):
                await backend._probe()

    asyncio.run(check())


@pytest.mark.parametrize("reply", ["valid", "wrong", "missing", "timeout", "cancelled"])
def test_resource_startup_handshake_and_failed_client_cleanup(
    reply: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        limits = podman._Limits(cpu_quota=25000)
        session = podman._PodmanSession(
            replace(engine(), command_timeout=1 if reply == "timeout" else 5), limits
        )
        executable = (
            str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
        )
        marker = limits.marker("execution")
        processes: list[asyncio.subprocess.Process] = []
        started = asyncio.Event()
        original_spawn = asyncio.create_subprocess_exec

        def command(_: podman._Engine, *args: str) -> tuple[str, ...]:
            if reply == "valid":
                code = f"import sys; sys.stdout.buffer.write({marker!r} + b'PAYLOAD')"
            elif reply == "wrong":
                code = f"import sys; sys.stdout.buffer.write(b'x' * {len(marker)})"
            elif reply == "missing":
                code = "pass"
            else:
                code = "import time; time.sleep(30)"
            return (executable, "-c", code)

        async def spawn(
            *args: str, stdin: int, stdout: int, stderr: int, limit: int
        ) -> asyncio.subprocess.Process:
            process = await original_spawn(
                *args, stdin=stdin, stdout=stdout, stderr=stderr, limit=limit
            )
            processes.append(process)
            started.set()
            return process

        monkeypatch.setattr(podman._Engine, "command", command)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(
            session.start_boundary(boundary(), ExecutionRequest(argv=("/bin/echo", "hello")))
        )
        if reply == "cancelled":
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif reply == "valid":
            process = await task
            stdout, _ = await process.communicate()
            assert stdout == b"PAYLOAD"
        else:
            with pytest.raises((SandboxCapabilityError, SandboxExecutionError)):
                await task
        assert len(processes) == 1
        assert processes[0].returncode is not None
        assert processes[0].stdout is not None
        assert processes[0].stdout.at_eof()
        assert processes[0].stderr is not None
        assert processes[0].stderr.at_eof()

    asyncio.run(check())


def test_control_client_spawn_is_inside_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        cancelled = asyncio.Event()

        async def spawn(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("unreachable")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(_cli.CommandError, match="client startup") as raised:
            await podman._invoke(replace(engine(), command_timeout=0.02), "creation")
        assert raised.value.possibly_accepted
        assert cancelled.is_set()

    asyncio.run(check())


def test_control_spawn_and_io_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    async def check() -> None:
        executable = (
            str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
        )
        original_spawn = asyncio.create_subprocess_exec

        def command(_: podman._Engine, *args: str) -> tuple[str, ...]:
            return (executable, "-c", "import time; time.sleep(0.25); print('completed')")

        async def spawn(
            *args: str, stdin: int, stdout: int, stderr: int, limit: int
        ) -> asyncio.subprocess.Process:
            await asyncio.sleep(0.1)
            return await original_spawn(
                *args, stdin=stdin, stdout=stdout, stderr=stderr, limit=limit
            )

        monkeypatch.setattr(podman._Engine, "command", command)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        with pytest.raises(_cli.CommandError, match="deadline"):
            await podman._invoke(replace(engine(), command_timeout=0.3), "control operation")

    asyncio.run(check())
