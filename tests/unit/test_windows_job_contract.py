"""Platform-independent contract tests for the Windows Job restricted-token option."""

import asyncio
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import Capability, CapabilityState, WindowsJobBackend
from isolattice.backends.windows import _report


@pytest.mark.parametrize("restricted_token", [True, False])
def test_report_never_claims_isolation(restricted_token: bool) -> None:
    report = _report(True, restricted_token=restricted_token)
    states = {name: evidence.state for name, evidence in report.capabilities.items()}
    assert set(states) == set(Capability)
    expected = CapabilityState.LIMITED if restricted_token else CapabilityState.UNSUPPORTED
    assert states[Capability.FILESYSTEM_ISOLATION] is expected
    assert states[Capability.PROCESS_ISOLATION] is expected
    assert states[Capability.NETWORK_ISOLATION] is CapabilityState.UNSUPPORTED
    assert CapabilityState.SUPPORTED not in {
        states[Capability.FILESYSTEM_ISOLATION],
        states[Capability.PROCESS_ISOLATION],
        states[Capability.VERIFIED_TERMINATION],
    }
    assert any("not a security boundary" in item for item in report.limitations)
    if restricted_token:
        assert any("low-integrity" in item for item in report.limitations)


def test_unavailable_restricted_report_supports_nothing() -> None:
    report = _report(False, "probe failed", restricted_token=True)
    assert not report.available
    assert report.error == "probe failed"
    assert {evidence.state for evidence in report.capabilities.values()} == {
        CapabilityState.UNSUPPORTED
    }


def test_restricted_token_must_be_a_bool(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        WindowsJobBackend(working_directory=tmp_path, restricted_token=1)  # pyrefly: ignore[bad-argument-type]


@given(
    st.one_of(
        st.floats(max_value=0),
        st.just(float("inf")),
        st.just(float("nan")),
        st.booleans(),
    )
)
def test_invalid_startup_timeouts_are_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="startup_timeout must be a positive finite number"):
        WindowsJobBackend(working_directory=Path.cwd(), startup_timeout=timeout)


def test_restricted_discovery_is_unavailable_off_windows(tmp_path: Path) -> None:
    import sys

    if sys.platform == "win32":
        pytest.skip("covered by the live Windows tests")
    report = asyncio.run(
        WindowsJobBackend(working_directory=tmp_path, restricted_token=True).discover()
    )
    assert not report.available
