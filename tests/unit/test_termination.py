import asyncio
from collections import deque
from typing import override

import pytest

from isolattice import SandboxBackendError, TerminationPolicy, TerminationVerificationError
from isolattice.termination import (
    BoundaryState,
    Observation,
    TerminationDriver,
    terminate_boundary,
)


class Driver(TerminationDriver):
    signal_releases = False

    def __init__(self, states: list[BoundaryState], *, start_requested: bool = False) -> None:
        self.boundary_id = "owned-boundary"
        self.start_requested = start_requested
        self.states = deque(states)
        self.last = states[-1]
        self.signals: list[bool] = []
        self.removals = 0
        self.present = True
        self.fail_signal = False
        self.fail_removal = False
        self.existence_failures = 0

    @override
    async def observe(self) -> Observation:
        state = self.states.popleft() if self.states else self.last
        return Observation(state)

    @override
    async def signal(self, *, force: bool) -> None:
        self.signals.append(force)
        if self.fail_signal:
            raise SandboxBackendError("signal rejected")

    @override
    async def remove(self) -> None:
        self.removals += 1
        if self.fail_removal:
            raise SandboxBackendError("removal rejected")
        self.present = False

    @override
    async def exists(self) -> bool:
        if self.existence_failures:
            self.existence_failures -= 1
            raise SandboxBackendError("existence unknown")
        return self.present


def policy(**values: float | int) -> TerminationPolicy:
    return TerminationPolicy(
        attempts=int(values.get("attempts", 3)),
        graceful_timeout=float(values.get("graceful_timeout", 0.001)),
        force_timeout=float(values.get("force_timeout", 0.001)),
        verification_interval=float(values.get("verification_interval", 0.001)),
        retry_delay=float(values.get("retry_delay", 0)),
        total_timeout=float(values.get("total_timeout", 1)),
    )


def test_graceful_stop_requires_two_exited_observations_and_release() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.EXITED, BoundaryState.EXITED])
        report = await terminate_boundary(driver, policy(), asyncio.Event())
        assert report.state == "terminated"
        assert report.resources_released
        assert report.attempts == 1
        assert report.observations == 3
        assert driver.signals == [False]
        assert driver.removals == 1

    asyncio.run(check())


def test_unknown_is_not_success_and_three_means_three_total_attempts() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.UNKNOWN])
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(), asyncio.Event())
        report = raised.value.report
        assert report is not None
        assert report.attempts == 3
        assert report.observations == 3
        assert report.state == "unknown"
        assert not report.resources_released
        assert driver.signals == []
        assert driver.removals == 0

    asyncio.run(check())


def test_force_skips_grace_and_verifies_stability() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.EXITED, BoundaryState.EXITED])
        force = asyncio.Event()
        force.set()
        report = await terminate_boundary(driver, policy(), force)
        assert report.state == "terminated"
        assert driver.signals == [True]

    asyncio.run(check())


def test_stability_regression_is_retried() -> None:
    async def check() -> None:
        driver = Driver(
            [
                BoundaryState.EXITED,
                BoundaryState.RUNNING,
                BoundaryState.RUNNING,
                BoundaryState.EXITED,
                BoundaryState.EXITED,
            ]
        )
        report = await terminate_boundary(driver, policy(), asyncio.Event())
        assert report.attempts == 2
        assert driver.signals == [False]

    asyncio.run(check())


def test_failed_signal_is_recorded_if_boundary_then_exits() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.EXITED, BoundaryState.EXITED])
        driver.fail_signal = True
        report = await terminate_boundary(driver, policy(), asyncio.Event())
        assert report.state == "terminated"
        assert "signal rejected" in report.notes

    asyncio.run(check())


def test_failed_removal_never_reports_success() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.EXITED])
        driver.fail_removal = True
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(), asyncio.Event())
        report = raised.value.report
        assert report is not None
        assert report.attempts == 3
        assert not report.resources_released
        assert driver.removals == 3

    asyncio.run(check())


def test_absence_without_owned_removal_is_unknown() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.ABSENT])
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(), asyncio.Event())
        assert raised.value.report is not None
        assert raised.value.report.state == "unknown"
        assert driver.removals == 0

    asyncio.run(check())


def test_pending_start_is_prevented_by_atomic_nonforced_removal() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.CREATED], start_requested=True)
        report = await terminate_boundary(driver, policy(), asyncio.Event())
        assert report.state == "terminated"
        assert report.observations == 1
        assert driver.removals == 1

    asyncio.run(check())


def test_release_verification_can_recover_after_acknowledged_removal() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.EXITED, BoundaryState.EXITED, BoundaryState.ABSENT])
        driver.existence_failures = 1
        report = await terminate_boundary(driver, policy(), asyncio.Event())
        assert report.state == "terminated"
        assert report.attempts == 2
        assert driver.removals == 1
        assert "existence unknown" in report.notes

    asyncio.run(check())


def test_total_cleanup_deadline_reports_unknown() -> None:
    class SlowDriver(Driver):
        @override
        async def observe(self) -> Observation:
            await asyncio.sleep(1)
            return Observation(BoundaryState.EXITED)

    async def check() -> None:
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(
                SlowDriver([BoundaryState.EXITED]),
                policy(total_timeout=0.01),
                asyncio.Event(),
            )
        assert raised.value.report is not None
        assert raised.value.report.state == "unknown"
        assert not raised.value.report.resources_released

    asyncio.run(check())


def test_signal_releasing_driver_accepts_absence_after_acknowledged_stop() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.ABSENT])
        driver.signal_releases = True
        report = await terminate_boundary(driver, policy(attempts=1), asyncio.Event())
        assert report.state == "terminated"
        assert report.resources_released
        assert driver.signals == [False]
        assert driver.removals == 1

    asyncio.run(check())


def test_signal_releasing_driver_still_requires_absence_confirmation() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.ABSENT])
        driver.signal_releases = True
        driver.existence_failures = 10
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(attempts=2), asyncio.Event())
        assert raised.value.report is not None
        assert raised.value.report.state == "unknown"

    asyncio.run(check())


@pytest.mark.parametrize("releases", [False, True])
def test_absence_without_a_stop_or_removal_is_not_success(releases: bool) -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.ABSENT])
        driver.signal_releases = releases
        with pytest.raises(TerminationVerificationError):
            await terminate_boundary(driver, policy(), asyncio.Event())
        assert driver.signals == []
        assert driver.removals == 0

    asyncio.run(check())


def test_rejected_stop_does_not_count_as_release() -> None:
    async def check() -> None:
        driver = Driver([BoundaryState.RUNNING, BoundaryState.ABSENT])
        driver.signal_releases = True
        driver.fail_signal = True
        with pytest.raises(TerminationVerificationError):
            await terminate_boundary(driver, policy(), asyncio.Event())
        assert driver.removals == 0

    asyncio.run(check())


def test_observation_error_is_unknown_and_recorded() -> None:
    class FailingDriver(Driver):
        @override
        async def observe(self) -> Observation:
            raise SandboxBackendError("engine unreachable")

    async def check() -> None:
        driver = FailingDriver([BoundaryState.RUNNING])
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(attempts=2), asyncio.Event())
        report = raised.value.report
        assert report is not None
        assert report.state == "unknown"
        assert report.observations == 2
        assert report.notes.count("engine unreachable") == 2
        assert driver.signals == []

    asyncio.run(check())


def test_boundary_present_after_acknowledged_removal_is_not_released() -> None:
    class LingeringDriver(Driver):
        @override
        async def remove(self) -> None:
            self.removals += 1

    async def check() -> None:
        driver = LingeringDriver([BoundaryState.EXITED])
        with pytest.raises(TerminationVerificationError) as raised:
            await terminate_boundary(driver, policy(), asyncio.Event())
        report = raised.value.report
        assert report is not None
        assert report.state == "unknown"
        assert not report.resources_released
        assert "boundary still exists after the backend acknowledged removal" in report.notes
        assert driver.removals == 1

    asyncio.run(check())
