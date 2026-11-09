import math
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    NetworkAccess,
    PodmanBackend,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    TerminationPolicy,
)


@pytest.mark.parametrize(
    "values",
    [
        {"attempts": 0},
        {"attempts": True},
        {"graceful_timeout": -1},
        {"force_timeout": 0},
        {"verification_interval": 0},
        {"retry_delay": math.inf},
        {"total_timeout": math.nan},
    ],
)
def test_invalid_termination_policy_rejected(values: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        TerminationPolicy(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"network": "none"},
        {"read_only_root": 1},
        {"memory_bytes": 0},
        {"cpu_cores": math.nan},
        {"process_limit": -1},
        {"required_capabilities": ("network",)},
        {"execution_timeout": 0},
        {"max_concurrent_executions": 0},
        {"output_queue_chunks": 0},
        {"stdout_limit": -1},
    ],
)
def test_invalid_sandbox_policy_rejected(values: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        SandboxPolicy(**values)


@pytest.mark.parametrize(
    "values",
    [
        {"argv": ()},
        {"argv": ("",)},
        {"argv": ("--help",)},
        {"argv": ("/bin/sh", "bad\0argument")},
        {"argv": ("/bin/sh",), "cwd": "relative"},
        {"argv": ("/bin/sh",), "cwd": "/allowed/../escape"},
        {"argv": ("/bin/sh",), "environment": {"BAD-NAME": "x"}},
        {"argv": ("/bin/sh",), "environment": {"VALUE": "line\ninjection"}},
        {"argv": ("/bin/sh",), "environment": {"VALUE": "x" * 65536}},
        {"argv": ("/bin/sh",), "stdin": "not bytes"},
        {"argv": ("/bin/sh",), "timeout": math.inf},
    ],
)
def test_invalid_execution_request_rejected(values: dict[str, Any]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ExecutionRequest(**values)


def test_request_copies_mutable_inputs() -> None:
    argv = ["/bin/echo", "hello"]
    environment = {"MESSAGE": "original"}
    request = ExecutionRequest(argv=argv, environment=environment)  # pyrefly: ignore[bad-argument-type]
    argv.append("later")
    environment["MESSAGE"] = "changed"
    assert request.argv == ("/bin/echo", "hello")
    assert request.environment["MESSAGE"] == "original"
    with pytest.raises(TypeError):
        request.environment["MESSAGE"] = "mutated"  # pyrefly: ignore[unsupported-operation]


@pytest.mark.parametrize(
    "policy",
    [
        SandboxPolicy(network=NetworkAccess.ENABLED),
        SandboxPolicy(network=NetworkAccess.ALLOWLIST),
        SandboxPolicy(
            read_only_root=False,
            scratch_bytes=1024,
            required_capabilities=(Capability.DISK_LIMIT,),
        ),
        SandboxPolicy(
            read_only_root=False,
            required_capabilities=(Capability.CHECKPOINTS,),
        ),
        SandboxPolicy(memory_bytes=1024),
        SandboxPolicy(cpu_cores=0.001),
        SandboxPolicy(process_limit=2**63),
        *(
            SandboxPolicy(required_capabilities=(capability,))
            for capability in (
                Capability.MEMORY_LIMIT,
                Capability.CPU_LIMIT,
                Capability.PROCESS_LIMIT,
            )
        ),
        SandboxPolicy(
            required_capabilities=(Capability.VERIFIED_TERMINATION,),
        ),
    ],
)
def test_unsupported_policy_is_rejected_before_backend_access(policy: SandboxPolicy) -> None:
    import asyncio

    backend = PodmanBackend(connection="not-contacted", image="not-contacted")

    async def check() -> None:
        async with Sandbox(backend=backend) as sandbox:
            with pytest.raises(SandboxCapabilityError):
                await sandbox.create(policy=policy)

    asyncio.run(check())


def test_capability_report_is_immutable() -> None:
    import asyncio

    async def check() -> None:
        backend = PodmanBackend(connection="missing", image="missing", executable="missing-podman")
        report = await backend.discover()
        assert not report.available
        assert report.error
        assert (
            report.capabilities[Capability.PROCESS_ISOLATION].state is CapabilityState.UNSUPPORTED
        )
        with pytest.raises(TypeError):
            report.capabilities[Capability.PROCESS_ISOLATION] = report.capabilities[  # pyrefly: ignore[unsupported-operation]
                Capability.NETWORK_ISOLATION
            ]

    asyncio.run(check())
