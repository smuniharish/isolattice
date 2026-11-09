"""Platform-independent contract tests for the Linux namespace backend."""

import asyncio
import math
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    Capability,
    CapabilityState,
    LinuxNamespaceBackend,
    NetworkAccess,
    ResourceBudget,
    SandboxCapabilityError,
    SandboxPolicy,
)
from isolattice.backends import linux
from isolattice.backends.linux import _cpu_max, _Host, _Limits, _report

CGROUP = "/sys/fs/cgroup/user.slice/delegated"


@given(st.floats(min_value=0.001, max_value=512, allow_nan=False, allow_infinity=False))
def test_cpu_quota_never_undershoots_the_request(cores: float) -> None:
    quota, period = (int(part) for part in _cpu_max(cores).split())
    assert period == 100_000
    assert quota >= 1_000
    assert quota >= math.floor(cores * period)
    assert quota <= max(1_000, cores * period + 1)


@pytest.mark.parametrize(
    ("limits", "controllers"),
    [
        (_Limits(), set()),
        (_Limits(memory_bytes=1), {"memory"}),
        (_Limits(cpu_cores=0.5), {"cpu"}),
        (_Limits(process_limit=4), {"pids"}),
        (_Limits(1, 1.0, 1), {"memory", "cpu", "pids"}),
    ],
)
def test_limits_require_matching_controllers(limits: _Limits, controllers: set[str]) -> None:
    assert limits.controllers() == controllers


def test_limits_from_budget() -> None:
    budget = ResourceBudget(memory_bytes=10, cpu_cores=0.5, process_limit=3)
    assert _Limits.from_budget(budget) == _Limits(10, 0.5, 3)


def test_limits_apply_and_verify_against_a_fake_cgroup(tmp_path: Path) -> None:
    for name in ("memory.max", "memory.swap.max", "cpu.max", "pids.max"):
        (tmp_path / name).write_text("max\n")
    limits = _Limits(64 << 20, 0.25, 7)
    limits.apply(tmp_path)
    limits.verify(tmp_path)
    assert (tmp_path / "memory.swap.max").read_text() == "0"
    assert (tmp_path / "cpu.max").read_text() == "25000 100000"
    (tmp_path / "pids.max").write_text("max\n")
    with pytest.raises(Exception, match=r"pids\.max=7"):
        limits.verify(tmp_path)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"cgroup_root": 3}, TypeError),
        ({"cgroup_root": "relative"}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": "/usr"}, TypeError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("usr",)}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("/",)}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("/usr/../etc",)}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("/tmp/data",)}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("/proc",)}, ValueError),
        ({"cgroup_root": CGROUP, "read_only_paths": ("/workspace",)}, ValueError),
        ({"cgroup_root": CGROUP, "startup_timeout": 0}, ValueError),
    ],
)
def test_constructor_rejects_unsafe_configuration(
    kwargs: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        LinuxNamespaceBackend(**kwargs)


def test_unavailable_host_reports_nothing_as_supported() -> None:
    report = _report(_Host("Landlock is unavailable or disabled"))
    assert not report.available
    assert report.error == "Landlock is unavailable or disabled"
    assert all(
        evidence.state is CapabilityState.UNSUPPORTED for evidence in report.capabilities.values()
    )


def test_probe_failure_makes_the_backend_unavailable() -> None:
    report = _report(_Host(None, 6, frozenset(_Limits(1, 1, 1).controllers())), "probe failed")
    assert not report.available
    assert report.error == "probe failed"
    assert report.capabilities[Capability.FILESYSTEM_ISOLATION].state is (
        CapabilityState.UNSUPPORTED
    )


def test_resource_capabilities_follow_delegated_controllers() -> None:
    report = _report(_Host(None, 6, frozenset({"pids"})))
    assert report.available
    states = {capability: report.capabilities[capability].state for capability in Capability}
    assert states[Capability.PROCESS_LIMIT] is CapabilityState.SUPPORTED
    assert states[Capability.MEMORY_LIMIT] is CapabilityState.UNSUPPORTED
    assert states[Capability.CPU_LIMIT] is CapabilityState.UNSUPPORTED
    assert states[Capability.CHECKPOINTS] is CapabilityState.UNSUPPORTED
    assert "Landlock denies TCP" in report.capabilities[Capability.NETWORK_ISOLATION].detail
    without = _report(_Host(None, 1, frozenset()))
    assert without.capabilities[Capability.INSTANCE_LIMITS].state is CapabilityState.UNSUPPORTED
    assert "Landlock" not in without.capabilities[Capability.NETWORK_ISOLATION].detail
    assert any("share the host kernel" in item for item in report.limitations)


def _prepare(policy: SandboxPolicy, host: _Host, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux, "_inspect_host", lambda root: host)

    async def no_probe(self: LinuxNamespaceBackend, host: _Host) -> None:
        raise AssertionError("prepare must reject before probing")

    monkeypatch.setattr(LinuxNamespaceBackend, "_probe", no_probe)
    backend = LinuxNamespaceBackend(cgroup_root=CGROUP)
    asyncio.run(backend.prepare(policy, "sandbox"))


HEALTHY = _Host(None, 6, frozenset({"pids"}))


@pytest.mark.parametrize(
    ("policy", "host", "message"),
    [
        (SandboxPolicy(), _Host("Linux namespaces require Linux"), "require Linux"),
        (
            SandboxPolicy(network=NetworkAccess.ENABLED),
            HEALTHY,
            "network=NONE",
        ),
        (
            SandboxPolicy(read_only_root=False),
            HEALTHY,
            "read-only root",
        ),
        (
            SandboxPolicy(
                workspace=True,
                scratch_bytes=1,
                required_capabilities=(Capability.DISK_LIMIT,),
            ),
            HEALTHY,
            "no quota",
        ),
        (
            SandboxPolicy(required_capabilities=(Capability.CHECKPOINTS,)),
            HEALTHY,
            "checkpoints is unsupported",
        ),
        (
            SandboxPolicy(memory_bytes=1 << 20),
            HEALTHY,
            "not delegated: memory",
        ),
        (
            SandboxPolicy(
                instance_budget=ResourceBudget(cpu_cores=1.0),
            ),
            HEALTHY,
            "not delegated: cpu",
        ),
    ],
)
def test_prepare_fails_closed_before_touching_the_host(
    policy: SandboxPolicy, host: _Host, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SandboxCapabilityError, match=message):
        _prepare(policy, host, monkeypatch)
