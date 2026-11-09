import asyncio
from collections import deque
from typing import override

from hypothesis import given, settings
from hypothesis import strategies as st

from isolattice import (
    ExecutionRequest,
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
    TerminationVerificationError,
)
from isolattice.backends.podman import _Limits
from isolattice.runtime import _capture
from isolattice.termination import (
    BoundaryState,
    Observation,
    TerminationDriver,
    terminate_boundary,
)


@given(
    chunks=st.lists(st.binary(max_size=100), max_size=30),
    limit=st.integers(min_value=0, max_value=500),
)
def test_capture_exactly_respects_byte_limit(chunks: list[bytes], limit: int) -> None:
    captured = bytearray()
    truncated = False
    for chunk in chunks:
        truncated |= _capture(captured, chunk, limit)
    full_output = b"".join(chunks)
    assert bytes(captured) == full_output[:limit]
    assert truncated is (len(full_output) > limit)


@given(
    environment=st.dictionaries(
        st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,12}", fullmatch=True),
        st.text(
            alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\0\r\n"),
            max_size=30,
        ),
        max_size=12,
    )
)
def test_environment_request_copies_and_preserves_every_entry(environment: dict[str, str]) -> None:
    expected = environment.copy()
    request = ExecutionRequest(argv=("/bin/echo",), environment=environment)
    environment.clear()
    assert dict(request.environment) == expected


@given(quota=st.integers(min_value=1000, max_value=1000000))
def test_cpu_budget_is_never_rounded_or_relaxed(quota: int) -> None:
    limits = _Limits.from_policy(SandboxPolicy(cpu_cores=quota / 100000))
    assert limits.cpu_quota == quota
    arguments = limits.arguments()
    assert arguments[arguments.index("--cpu-quota") + 1] == str(quota)
    assert arguments[arguments.index("--cpu-period") + 1] == "100000"


class ObservationDriver(TerminationDriver):
    signal_releases = False

    boundary_id = "property-boundary"
    start_requested = False

    def __init__(self, states: list[BoundaryState]) -> None:
        self.pending = deque(states)
        self.last = states[-1]
        self.observed: list[BoundaryState] = []
        self.removed = False

    @override
    async def observe(self) -> Observation:
        state = self.pending.popleft() if self.pending else self.last
        self.observed.append(state)
        return Observation(state)

    @override
    async def signal(self, *, force: bool) -> None:
        pass

    @override
    async def remove(self) -> None:
        assert self.observed[-2:] in (
            [BoundaryState.CREATED, BoundaryState.CREATED],
            [BoundaryState.EXITED, BoundaryState.EXITED],
        )
        self.removed = True

    @override
    async def exists(self) -> bool:
        return not self.removed


@settings(max_examples=100, deadline=None)
@given(
    states=st.lists(
        st.sampled_from(
            [
                BoundaryState.CREATED,
                BoundaryState.RUNNING,
                BoundaryState.EXITED,
                BoundaryState.UNKNOWN,
                BoundaryState.ABSENT,
            ]
        ),
        min_size=1,
        max_size=12,
    ),
    attempts=st.integers(min_value=1, max_value=4),
)
def test_termination_never_succeeds_without_stable_empty_and_release(
    states: list[BoundaryState], attempts: int
) -> None:
    async def check() -> None:
        driver = ObservationDriver(states)
        policy = TerminationPolicy(
            attempts=attempts,
            graceful_timeout=0,
            force_timeout=0.000001,
            verification_interval=0.000001,
            retry_delay=0,
            total_timeout=2,
        )
        outcome: TerminationReport | TerminationVerificationError
        try:
            outcome = await terminate_boundary(driver, policy, asyncio.Event())
        except TerminationVerificationError as error:
            outcome = error
        if isinstance(outcome, TerminationVerificationError):
            assert outcome.report is not None
            assert outcome.report.attempts <= attempts
            assert not outcome.report.resources_released
        else:
            assert driver.removed
            assert outcome.resources_released
            assert outcome.state == "terminated"

    asyncio.run(check())
