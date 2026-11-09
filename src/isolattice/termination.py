"""Bounded, verified termination of one authoritative backend boundary.

Backends implement :class:`TerminationDriver` for their boundary and delegate the
stop, observe, escalate and release sequence to :func:`terminate_boundary`.
"""

from __future__ import annotations

import asyncio
import contextlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .errors import SandboxBackendError, TerminationVerificationError
from .models import TerminationPolicy, TerminationReport

__all__ = ["BoundaryState", "Observation", "TerminationDriver", "terminate_boundary"]


class BoundaryState(StrEnum):
    """Backend-reported state of one boundary."""

    CREATED = "created"
    RUNNING = "running"
    EXITED = "exited"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Observation:
    """One observation of a boundary's state.

    Attributes:
        state: The observed state; ``UNKNOWN`` never counts as terminated.
        exit_code: The workload exit code, when the backend reports it.
        detail: A note recorded in the termination report.
    """

    state: BoundaryState
    exit_code: int | None = None
    detail: str = ""


class TerminationDriver(ABC):
    """Identity-bound control of one boundary.

    Attributes:
        boundary_id: Immutable identity of the controlled boundary.
        start_requested: Whether a start was requested, so a ``CREATED`` boundary
            must be removed to prevent a delayed start.
        signal_releases: Whether an acknowledged stop request also deletes the
            boundary (for example a Kubernetes Pod deletion), so a later ``ABSENT``
            observation is followed by an idempotent :meth:`remove` and an
            :meth:`exists` check instead of being treated as an unexplained
            disappearance.
    """

    boundary_id: str
    start_requested: bool
    signal_releases: bool = False

    @abstractmethod
    async def observe(self) -> Observation:
        """Return the boundary's current state by its immutable identity."""

    @abstractmethod
    async def signal(self, *, force: bool) -> None:
        """Request a graceful stop, or a forced kill when ``force`` is true."""

    @abstractmethod
    async def remove(self) -> None:
        """Idempotently release the boundary's backend resources."""

    @abstractmethod
    async def exists(self) -> bool:
        """Return whether the boundary still exists after removal."""


async def terminate_boundary(
    driver: TerminationDriver,
    policy: TerminationPolicy,
    force_requested: asyncio.Event,
) -> TerminationReport:
    """Stop, observe twice, and release one immutable backend boundary.

    Args:
        driver: Control of the boundary.
        policy: Graceful, forced and total deadlines and retry budget.
        force_requested: Set to skip or cut short the graceful phase.

    Returns:
        A report of a verified-empty, released boundary.

    Raises:
        TerminationVerificationError: Emptiness or release could not be verified
            within the policy; the report records ``non_empty`` or ``unknown``.
    """
    attempts = 0
    observations = 0
    last = [BoundaryState.UNKNOWN]
    notes: list[str] = []
    removal_acknowledged = False
    stop_released = False

    def report(
        state: Literal["terminated", "non_empty", "unknown"], released: bool = False
    ) -> TerminationReport:
        return TerminationReport(
            boundary_id=driver.boundary_id,
            attempts=attempts,
            observations=observations,
            state=state,
            resources_released=released,
            notes=tuple(notes),
        )

    async def observe() -> Observation:
        nonlocal observations
        observations += 1
        try:
            result = await driver.observe()
        except SandboxBackendError as error:
            result = Observation(BoundaryState.UNKNOWN, detail=str(error))
        last[0] = result.state
        if result.detail:
            notes.append(result.detail)
        return result

    async def send_signal(*, force: bool) -> None:
        nonlocal stop_released
        try:
            await driver.signal(force=force)
        except SandboxBackendError as error:
            notes.append(str(error))
            return
        stop_released |= driver.signal_releases

    async def wait_for_exit(seconds: float, *, interruptible: bool) -> Observation:
        end = asyncio.get_running_loop().time() + seconds
        current = await observe()
        while current.state is BoundaryState.RUNNING:
            remaining = end - asyncio.get_running_loop().time()
            if remaining <= 0 or (interruptible and force_requested.is_set()):
                break
            delay = min(policy.verification_interval, remaining)
            if interruptible:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(force_requested.wait(), delay)
            else:
                await asyncio.sleep(delay)
            current = await observe()
        return current

    async def release() -> bool:
        nonlocal removal_acknowledged
        if not removal_acknowledged:
            try:
                await driver.remove()
            except SandboxBackendError as error:
                notes.append(str(error))
                return False
            removal_acknowledged = True
        try:
            present = await driver.exists()
        except SandboxBackendError as error:
            notes.append(str(error))
            last[0] = BoundaryState.UNKNOWN
            return False
        if present:
            notes.append("boundary still exists after the backend acknowledged removal")
            last[0] = BoundaryState.UNKNOWN
            return False
        return True

    try:
        async with asyncio.timeout(policy.total_timeout):
            for attempt in range(1, policy.attempts + 1):
                attempts = attempt
                current = await observe()
                if current.state is BoundaryState.ABSENT and (
                    removal_acknowledged or stop_released
                ):
                    if await release():
                        return report("terminated", True)
                elif current.state is BoundaryState.CREATED and driver.start_requested:
                    # A non-forced removal atomically prevents a delayed start by immutable ID.
                    if await release():
                        return report("terminated", True)
                else:
                    if current.state is BoundaryState.RUNNING:
                        if not force_requested.is_set():
                            await send_signal(force=False)
                            current = await wait_for_exit(
                                policy.graceful_timeout, interruptible=True
                            )
                        if current.state is BoundaryState.RUNNING:
                            await send_signal(force=True)
                            current = await wait_for_exit(policy.force_timeout, interruptible=False)
                    if current.state is BoundaryState.ABSENT and stop_released and await release():
                        return report("terminated", True)
                    if current.state in (BoundaryState.CREATED, BoundaryState.EXITED):
                        await asyncio.sleep(policy.verification_interval)
                        again = await observe()
                        if again.state is current.state and await release():
                            return report("terminated", True)
                if attempt < policy.attempts:
                    await asyncio.sleep(policy.retry_delay)
    except TimeoutError as error:
        raise TerminationVerificationError(
            "termination exceeded its total deadline; boundary state is unknown",
            report("unknown"),
        ) from error

    state: Literal["non_empty", "unknown"] = (
        "non_empty" if last[0] is BoundaryState.RUNNING else "unknown"
    )
    raise TerminationVerificationError(
        "termination could not verify an empty boundary and resource release",
        report(state),
    )
