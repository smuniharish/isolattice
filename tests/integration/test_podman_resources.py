"""Bounded live workloads verify actual cgroup v2 enforcement, not just flags."""

import asyncio
from dataclasses import replace

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    TerminationPolicy,
)
from isolattice.backend import Boundary
from isolattice.backends import podman

pytestmark = pytest.mark.podman


def backend(config: tuple[str, str, bool]) -> PodmanBackend:
    connection, image, allow_rootful = config
    return PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)


def policy() -> SandboxPolicy:
    return SandboxPolicy(
        memory_bytes=67108864,
        cpu_cores=0.25,
        process_limit=8,
        execution_timeout=60,
        termination=TerminationPolicy(verification_interval=0.1),
        required_capabilities=(
            Capability.MEMORY_LIMIT,
            Capability.CPU_LIMIT,
            Capability.PROCESS_LIMIT,
        ),
    )


def test_real_kernel_budgets_and_bootstrap_preserve_io(
    podman_resources_config: tuple[str, str, bool],
) -> None:
    async def check() -> None:
        configured = backend(podman_resources_config)
        report = await configured.discover()
        assert report.available, report.error
        for capability in (Capability.MEMORY_LIMIT, Capability.CPU_LIMIT, Capability.PROCESS_LIMIT):
            assert report.capabilities[capability].state is CapabilityState.SUPPORTED
        async with (
            Sandbox(backend=configured) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            result = await instance.run(
                ExecutionRequest(
                    argv=(
                        "/bin/sh",
                        "-c",
                        'IFS= read -r line; printf "%s\\n%s\\n%s\\n" "$line" "$MSG" "$1"; '
                        "/bin/cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max "
                        "/sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max",
                        "workload",
                        "literal; $(not-executed)",
                    ),
                    stdin=b"delivered\n",
                    environment={"MSG": "explicit"},
                )
            )
            assert result.exit_code == 0
            assert result.stdout == (
                b"delivered\nexplicit\nliteral; $(not-executed)\n67108864\n0\n25000 100000\n8\n"
            )
            assert result.stderr == b""
            assert result.termination.resources_released

    asyncio.run(check())


def test_real_memory_over_budget_is_oom_killed(
    podman_resources_config: tuple[str, str, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        original = podman._PodmanSession.exit_code
        oom: list[object] = []

        async def exit_code(
            session: podman._PodmanSession, boundary: Boundary, client_exit_code: int
        ) -> int:
            code = await original(session, boundary, client_exit_code)
            data = await session._inspect(boundary)
            state = data["State"]
            assert isinstance(state, dict)
            oom.append(state.get("OOMKilled"))
            return code

        monkeypatch.setattr(podman._PodmanSession, "exit_code", exit_code)
        async with (
            Sandbox(backend=backend(podman_resources_config)) as sandbox,
            await sandbox.create(policy=replace(policy(), memory_bytes=33554432)) as instance,
        ):
            result = await instance.run(
                ExecutionRequest(
                    argv=(
                        "/usr/bin/awk",
                        'BEGIN { value=sprintf("%100663296s",""); print length(value) }',
                    )
                )
            )
            assert result.exit_code == 137, (result.stderr, oom)
            assert oom == [True]
            assert result.termination.resources_released

    asyncio.run(check())


def test_real_process_budget_records_denied_forks(
    podman_resources_config: tuple[str, str, bool],
) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend(podman_resources_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            script = (
                """/bin/sh -c 'i=0; while [ "$i" -lt 16 ]; """
                "do /bin/sleep 2 & i=$((i+1)); done; wait'; "
                'while read -r key value; do [ "$key" != max ] || '
                """printf "DENIED=%s\\n" "$value"; """
                "done < /sys/fs/cgroup/pids.events"
            )
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", script)))
            assert result.exit_code == 0
            assert result.stdout.startswith(b"DENIED=")
            assert int(result.stdout.removeprefix(b"DENIED=").strip()) > 0
            assert result.termination.resources_released

    asyncio.run(check())


def test_real_cpu_budget_records_throttling(
    podman_resources_config: tuple[str, str, bool],
) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend(podman_resources_config)) as sandbox,
            await sandbox.create(policy=replace(policy(), cpu_cores=0.1)) as instance,
        ):
            script = (
                'i=0; while [ "$i" -lt 200000 ]; do i=$((i+1)); done; '
                'while read -r key value; do [ "$key" != nr_throttled ] || '
                'printf "THROTTLED=%s\\n" "$value"; done < /sys/fs/cgroup/cpu.stat'
            )
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", script)))
            assert result.exit_code == 0
            assert result.stdout.startswith(b"THROTTLED=")
            assert int(result.stdout.removeprefix(b"THROTTLED=").strip()) > 0
            assert result.termination.resources_released

    asyncio.run(check())


def test_workload_cannot_relax_budgets_or_write_cgroup_membership(
    podman_resources_config: tuple[str, str, bool],
) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=backend(podman_resources_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            script = (
                "if (printf max > /sys/fs/cgroup/pids.max) 2>/dev/null; then exit 96; fi; "
                'if (printf "%s" "$$" > /sys/fs/cgroup/cgroup.procs) 2>/dev/null; '
                "then exit 97; fi; "
                "IFS= read -r value < /sys/fs/cgroup/pids.max; "
                '[ "$value" = 8 ] || exit 98; printf CGROUP_WRITES_DENIED'
            )
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", script)))
            assert result.exit_code == 0
            assert result.stdout == b"CGROUP_WRITES_DENIED"
            assert result.termination.resources_released

    asyncio.run(check())


def test_real_mismatched_kernel_limit_prevents_workload_exec(
    podman_resources_config: tuple[str, str, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check() -> None:
        original_command = podman._Limits.command
        original_start = podman._PodmanSession.start_boundary
        states: list[object] = []

        def incorrect(
            limits: podman._Limits, request: ExecutionRequest, execution_id: str
        ) -> tuple[str, ...]:
            command = list(original_command(limits, request, execution_id))
            command[2] = command[2].replace("25000 100000", "25001 100000")
            return tuple(command)

        async def start(
            session: podman._PodmanSession, boundary: Boundary, request: ExecutionRequest
        ) -> asyncio.subprocess.Process:
            try:
                return await original_start(session, boundary, request)
            except SandboxCapabilityError:
                data = await session._inspect(boundary)
                state = data["State"]
                assert isinstance(state, dict)
                states.append(state.get("ExitCode"))
                raise

        monkeypatch.setattr(podman._Limits, "command", incorrect)
        monkeypatch.setattr(podman._PodmanSession, "start_boundary", start)
        async with (
            Sandbox(backend=backend(podman_resources_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxCapabilityError, match="bootstrap"):
                await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "exit 97")))
            assert states == [125], "the workload must not reach its exit 97"

    asyncio.run(check())
