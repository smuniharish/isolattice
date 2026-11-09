"""Platform-independent contract tests for the Windows AppContainer backend."""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    Capability,
    CapabilityState,
    SandboxCapabilityError,
    SandboxExecutionError,
    SandboxPolicy,
    WindowsAppContainerBackend,
)
from isolattice.backends.windows import _JobLimits, _windows_path
from isolattice.backends.windows_appcontainer import _AppContainerSession, _report


def session(workspace: Path | None = None) -> _AppContainerSession:
    created = _AppContainerSession(
        limits=_JobLimits(),
        read_only_paths=(),
        state_directory=None,
        less_privileged=True,
        startup_timeout=1.0,
        helper_interpreter=Path(sys.executable),
    )
    created._workspace = workspace
    return created


@pytest.mark.parametrize("less_privileged", [True, False])
def test_report_never_claims_full_isolation(less_privileged: bool) -> None:
    report = _report(True, less_privileged)
    states = {name: evidence.state for name, evidence in report.capabilities.items()}
    assert set(states) == set(Capability)
    for limited in (
        Capability.FILESYSTEM_ISOLATION,
        Capability.PROCESS_ISOLATION,
        Capability.VERIFIED_TERMINATION,
        Capability.ENVIRONMENT_ISOLATION,
        Capability.RESOURCE_LIMITS,
    ):
        assert states[limited] is CapabilityState.LIMITED
    assert states[Capability.CHECKPOINTS] is CapabilityState.UNSUPPORTED
    assert states[Capability.DISK_LIMIT] is CapabilityState.UNSUPPORTED
    assert any("share the host kernel" in item for item in report.limitations)


def test_unavailable_report_supports_nothing() -> None:
    report = _report(False, True, "no host")
    assert not report.available
    assert report.error == "no host"
    assert {evidence.state for evidence in report.capabilities.values()} == {
        CapabilityState.UNSUPPORTED
    }


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"read_only_paths": r"C:\data"}, TypeError),
        ({"read_only_paths": ["relative"]}, ValueError),
        ({"read_only_paths": [b"C:\\data"]}, TypeError),
        ({"state_directory": "/tmp/state"}, ValueError),
        ({"less_privileged": 1}, TypeError),
        ({"startup_timeout": True}, TypeError),
        ({"startup_timeout": 0}, ValueError),
        ({"startup_timeout": float("inf")}, ValueError),
    ],
)
def test_constructor_validation(options: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error):
        WindowsAppContainerBackend(**options)


def test_windows_paths_must_be_absolute_with_a_drive() -> None:
    assert str(_windows_path(r"C:\data", "path")).endswith("data")
    for bad in (r"\data", "data", r"C:data"):
        with pytest.raises(ValueError, match="path must be an absolute Windows path"):
            _windows_path(bad, "path")


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows behaviour")
def test_non_windows_hosts_are_unavailable_and_fail_closed() -> None:
    backend = WindowsAppContainerBackend()
    report = asyncio.run(backend.discover())
    assert not report.available
    assert "Windows" in (report.error or "")
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(backend.prepare(SandboxPolicy(), "id"))


def test_working_directory_maps_workspace_and_scratch(tmp_path: Path) -> None:
    scratch, workspace = tmp_path / "scratch", tmp_path / "workspace"
    (scratch / "sub").mkdir(parents=True)
    (workspace / "project").mkdir(parents=True)
    current = session(workspace)
    assert current._working_directory(scratch, "/") == scratch
    assert current._working_directory(scratch, "/sub") == scratch / "sub"
    assert current._working_directory(scratch, "/workspace/project") == workspace / "project"
    with pytest.raises(SandboxExecutionError):
        current._working_directory(scratch, "/missing")
    with pytest.raises(SandboxExecutionError):
        session(None)._working_directory(scratch, "/workspace")


@given(
    st.text(min_size=1, max_size=8).filter(lambda text: "/" not in text and "\x00" not in text),
    st.sampled_from(':\\*?"<>|'),
)
def test_working_directory_rejects_windows_metacharacters(prefix: str, bad: str) -> None:
    with pytest.raises(SandboxExecutionError):
        session()._working_directory(Path("scratch"), f"/{prefix}{bad}")


@given(st.sampled_from([" ", "."]))
def test_working_directory_rejects_trailing_dots_and_spaces(suffix: str) -> None:
    with pytest.raises(SandboxExecutionError):
        session()._working_directory(Path("scratch"), f"/name{suffix}")


@given(
    st.dictionaries(
        st.sampled_from(["SystemRoot", "systemroot", "LocalAppData", "PATH", "Greeting"]),
        st.text(max_size=8).filter(lambda text: "\x00" not in text),
    )
)
def test_explicit_environment_overrides_case_insensitively(explicit: dict[str, str]) -> None:
    environment = session()._environment(explicit)
    upper = {name.upper(): value for name, value in environment.items()}
    assert len(upper) == len(environment)
    assert {"SYSTEMROOT", "LOCALAPPDATA"} <= set(upper)
    expected = {name.upper(): value for name, value in explicit.items()}
    assert {name: upper[name] for name in expected} == expected
