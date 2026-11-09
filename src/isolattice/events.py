"""Structured lifecycle events and pluggable event sinks."""

from __future__ import annotations

import datetime
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import override

__all__ = ["EventKind", "EventSink", "LoggingEventSink", "SandboxEvent"]


class EventKind(StrEnum):
    """Lifecycle transitions the runtime reports."""

    SANDBOX_CREATED = "sandbox.created"
    SANDBOX_CLOSED = "sandbox.closed"
    SANDBOX_CLEANUP_FAILED = "sandbox.cleanup-failed"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_FINISHED = "execution.finished"
    EXECUTION_FAILED = "execution.failed"
    TERMINATION_UNKNOWN = "termination.unknown"
    LATE_CLEANUP_FINISHED = "late-cleanup.finished"
    LATE_CLEANUP_FAILED = "late-cleanup.failed"
    ARTIFACT_PUBLISHED = "artifact.published"
    ARTIFACT_RESTORED = "artifact.restored"


_WARNINGS = frozenset(
    {
        EventKind.SANDBOX_CLEANUP_FAILED,
        EventKind.TERMINATION_UNKNOWN,
        EventKind.LATE_CLEANUP_FINISHED,
        EventKind.LATE_CLEANUP_FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    """One lifecycle event.

    Events never carry argv, environment, stdin or output, so they are safe to
    forward to shared telemetry. ``detail`` holds small scalar facts only.
    """

    kind: EventKind
    sandbox_id: str
    backend: str
    execution_id: str | None = None
    detail: Mapping[str, str | int | float | bool | None] = field(
        default_factory=lambda: MappingProxyType({})
    )
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EventKind):
            raise TypeError("kind must be an EventKind")
        if not isinstance(self.detail, Mapping):
            raise TypeError("detail must be a mapping")
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    @property
    def is_warning(self) -> bool:
        """Whether the event reports unverified or delayed cleanup."""
        return self.kind in _WARNINGS


class EventSink(ABC):
    """Receives lifecycle events synchronously from the event loop.

    ``emit`` must not block or perform I/O that can stall; buffer and export
    asynchronously instead. The runtime logs and suppresses sink exceptions so
    telemetry failures never interrupt containment or cleanup.
    """

    @abstractmethod
    def emit(self, event: SandboxEvent) -> None:
        """Record one event."""


class LoggingEventSink(EventSink):
    """Write events to a :mod:`logging` logger, at WARNING for cleanup concerns."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        """Use ``logger``, or the ``isolattice`` logger when omitted."""
        if logger is not None and not isinstance(logger, logging.Logger):
            raise TypeError("logger must be a logging.Logger")
        self._logger = logger if logger is not None else logging.getLogger("isolattice")

    @override
    def emit(self, event: SandboxEvent) -> None:
        """Log the event with its fields as structured ``extra`` data."""
        self._logger.log(
            logging.WARNING if event.is_warning else logging.INFO,
            "%s sandbox=%s execution=%s backend=%s %s",
            event.kind.value,
            event.sandbox_id,
            event.execution_id or "-",
            event.backend,
            " ".join(f"{name}={value}" for name, value in sorted(event.detail.items())),
            extra={"isolattice_event": event},
        )
