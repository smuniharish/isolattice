"""Async sandbox ownership and bounded execution."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, Self

from . import _artifacts
from ._async import discard_remaining, raise_combined, raise_with_cleanup, settle, wait_until
from .backend import Backend, BackendSession, Boundary
from .errors import (
    SandboxArtifactError,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxExecutionError,
    SandboxStateError,
    SandboxTimeoutError,
    TerminationVerificationError,
)
from .events import EventKind, EventSink, LoggingEventSink, SandboxEvent
from .models import (
    ArtifactKind,
    ArtifactManifest,
    BackendReport,
    ExecutionRequest,
    ExecutionResult,
    OutputChunk,
    SandboxPolicy,
    TerminationReport,
    validate_artifact_key,
)
from .storage import ArtifactStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable
    from types import TracebackType

__all__ = ["ExecutionHandle", "Sandbox", "SandboxInstance"]

_CHUNK_SIZE = 65536
_EXIT_POLL_INTERVAL = 0.1
_ATTACH_EXIT_GRACE = 2.0
_ATTACH_REAP_TIMEOUT = 5.0
_DRAIN_TIMEOUT = 5.0
_MAX_ARTIFACT_BYTES = 16 * 1024**3
_MAX_ARCHIVE_MEMBERS = 1_000_000
_RESTORED_ARGV = "isolattice-restored-checkpoint"
_logger = logging.getLogger("isolattice")


def _contains_uncertain_termination(error: BaseException) -> bool:
    if isinstance(error, TerminationVerificationError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_uncertain_termination(child) for child in error.exceptions)
    return False


def _sole_error(grouped: BaseExceptionGroup[BaseException]) -> BaseException:
    # A task group wraps even a single failure; callers expect the typed error itself.
    error: BaseException = grouped
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]
    return error


def _capture(buffer: bytearray, data: bytes, limit: int) -> bool:
    remaining = max(0, limit - len(buffer))
    buffer.extend(data[:remaining])
    return len(data) > remaining


async def _root_exit(process: asyncio.subprocess.Process) -> int:
    # Python 3.12 resolves wait() only after every pipe closes, so descendants holding
    # inherited pipes would hide the root's exit; returncode is set at exit everywhere.
    waiter = asyncio.ensure_future(process.wait())
    try:
        while True:
            done, _ = await asyncio.wait((waiter,), timeout=_EXIT_POLL_INTERVAL)
            if done:
                return waiter.result()
            if (code := process.returncode) is not None:
                return code
    finally:
        if not waiter.done():
            waiter.cancel()


async def _reap_attach(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        async with asyncio.timeout(_ATTACH_EXIT_GRACE):
            await process.wait()
        return
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    try:
        async with asyncio.timeout(_ATTACH_REAP_TIMEOUT):
            await process.wait()
    except TimeoutError as error:
        raise SandboxBackendError("the owned backend attach client could not be reaped") from error


async def _publish(
    owner: Sandbox,
    session: BackendSession,
    sandbox_id: str,
    execution_id: str | None,
    kind: ArtifactKind,
    archive_format: str,
    key: str,
    produce: Callable[[Path], Awaitable[None]],
) -> ArtifactManifest:
    store = owner._require_store()
    validate_artifact_key(key)
    if await store.exists(key):
        raise SandboxArtifactError(f"artifact {key!r} already exists")
    staging = await _artifacts.stage(owner._staging_directory)
    try:
        archive = staging / "archive"
        await produce(archive)
        manifest = await _artifacts.seal(kind, session.backend_name, archive_format, archive, key)
        await store.publish(key, archive, manifest)
    except BaseException as error:
        cleanup = await settle(asyncio.create_task(_artifacts.unstage(staging)))
        raise_with_cleanup(
            "artifact publication and staging cleanup failed",
            error,
            cleanup.error,
            cleanup.interrupted,
        )
    cleanup = await settle(asyncio.create_task(_artifacts.unstage(staging)))
    raise_combined("artifact staging cleanup failed", cleanup.error, cleanup.interrupted)
    owner._emit(
        EventKind.ARTIFACT_PUBLISHED,
        sandbox_id,
        session.backend_name,
        execution_id,
        key=key,
        artifact_kind=kind.value,
        size=manifest.size,
    )
    return manifest


async def _fetch(
    owner: Sandbox,
    session: BackendSession,
    sandbox_id: str,
    key: str,
    kind: ArtifactKind,
    archive_format: str,
    validate: Callable[[Path], Awaitable[None]] | None,
) -> tuple[Path, Path]:
    store = owner._require_store()
    validate_artifact_key(key)
    staging = await _artifacts.stage(owner._staging_directory)
    try:
        archive = staging / "archive"
        manifest = await store.fetch(key, archive, max_bytes=_MAX_ARTIFACT_BYTES)
        if not isinstance(manifest, ArtifactManifest):
            raise SandboxArtifactError("artifact store returned no ArtifactManifest")
        await _artifacts.verify(
            archive,
            manifest,
            key=key,
            kind=kind,
            backend=session.backend_name,
            archive_format=archive_format,
            max_bytes=_MAX_ARTIFACT_BYTES,
        )
        if validate is not None:
            await validate(archive)
    except BaseException as error:
        cleanup = await settle(asyncio.create_task(_artifacts.unstage(staging)))
        raise_with_cleanup(
            "artifact fetch and staging cleanup failed",
            error,
            cleanup.error,
            cleanup.interrupted,
        )
    owner._emit(
        EventKind.ARTIFACT_RESTORED,
        sandbox_id,
        session.backend_name,
        None,
        key=key,
        artifact_kind=kind.value,
        size=manifest.size,
    )
    return staging, archive


async def _validate_workspace(archive: Path) -> None:
    await _artifacts.validate_workspace_tar(
        archive, max_members=_MAX_ARCHIVE_MEMBERS, max_bytes=_MAX_ARTIFACT_BYTES
    )


async def _validate_rootfs(archive: Path) -> None:
    await _artifacts.validate_rootfs_tar(
        archive, max_members=_MAX_ARCHIVE_MEMBERS, max_bytes=_MAX_ARTIFACT_BYTES
    )


class ExecutionHandle:
    """One execution and its single backpressured output subscription."""

    def __init__(
        self,
        instance: SandboxInstance,
        session: BackendSession,
        request: ExecutionRequest,
        execution_id: str,
        *,
        restore_source: Path | None = None,
        staging: Path | None = None,
    ) -> None:
        self._instance = instance
        self._session = session
        self._request = request
        self._restore_source = restore_source
        self._staging = staging
        self.execution_id = execution_id
        self._boundary: Boundary | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._force_requested = asyncio.Event()
        self._queue: asyncio.Queue[OutputChunk] = asyncio.Queue(instance.policy.output_queue_chunks)
        self._output_done = asyncio.Event()
        self._stream_claimed = False
        self._cancel_requested = False
        self._artifact_lock = asyncio.Lock()
        self._observed = False
        self._stdout = bytearray()
        self._stderr = bytearray()
        self._stdout_truncated = False
        self._stderr_truncated = False
        self._termination: TerminationReport | None = None
        self._work: asyncio.Task[int] | None = None
        self._cleanup_deadline: float | None = None
        self._stop_requested = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"isolattice-execution-{execution_id}")
        self._task.add_done_callback(lambda _: instance._execution_done(self))

    def _register_boundary(self, boundary: Boundary) -> None:
        if self._boundary is not None and self._boundary is not boundary:
            raise SandboxBackendError("backend attempted to replace an owned execution boundary")
        self._boundary = boundary

    def _begin_deadline(self) -> float:
        if self._cleanup_deadline is None:
            self._cleanup_deadline = (
                asyncio.get_running_loop().time()
                + self._instance.policy.termination.cleanup_deadline
            )
        return self._cleanup_deadline

    def _request_stop(self, *, force: bool = False) -> None:
        if force:
            self._force_requested.set()
        if not self._task.done() and not self._cancel_requested:
            self._cancel_requested = True
            self._begin_deadline()
            self._stop_requested.set()
            if self._work is not None:
                self._work.cancel()

    def _deadline_exceeded(self, phase: str) -> TerminationVerificationError:
        boundary = self._boundary
        notes = [
            f"cleanup deadline of {self._instance.policy.termination.cleanup_deadline}s "
            f"was exceeded while {phase}",
            "remaining cleanup continues in the background, owned by the sandbox instance",
        ]
        if boundary is not None:
            notes.append(f"reconcile backend resource {boundary.name!r} if it persists")
        report = TerminationReport(
            boundary.boundary_id if boundary is not None else self.execution_id,
            0,
            0,
            "unknown",
            False,
            tuple(notes),
        )
        return TerminationVerificationError(
            "cleanup exceeded its hard deadline; boundary state is unknown", report
        )

    async def _late_cleanup(self, work: asyncio.Task[int]) -> TerminationReport | None:
        await asyncio.gather(work, return_exceptions=True)
        return await self._cleanup()

    def _mark_observed(self) -> None:
        self._observed = True
        self._instance._unobserved.pop(self, None)

    async def _release_staging(self) -> None:
        # Concurrent cancel and close both remove the tree; removal tolerates that.
        if self._staging is not None:
            await _artifacts.unstage(self._staging)
            self._staging = None

    async def _allocate(self) -> Boundary:
        if self._restore_source is not None:
            try:
                return await self._session.restore_boundary(
                    self._restore_source,
                    self._instance.sandbox_id,
                    self.execution_id,
                    self._register_boundary,
                )
            finally:
                # The checkpoint holds workload memory; drop it once it is restored.
                await self._release_staging()
        return await self._session.create_boundary(
            self._request,
            self._instance.sandbox_id,
            self.execution_id,
            self._register_boundary,
        )

    def _running_boundary(self, action: str) -> Boundary:
        boundary = self._boundary
        if boundary is None or self._process is None or self._task.done() or self._cancel_requested:
            raise SandboxStateError(f"only a running execution can be {action}")
        return boundary

    async def checkpoint(self, key: str) -> ArtifactManifest:
        """Checkpoint the running workload into the artifact store, leaving it running.

        The archive contains workload memory and must be protected like the workload.

        Args:
            key: A new artifact key; see :func:`isolattice.validate_artifact_key`.

        Returns:
            The integrity manifest published with the checkpoint.
        """
        async with self._artifact_lock:
            boundary = self._running_boundary("checkpointed")
            return await _publish(
                self._instance._owner,
                self._session,
                self._instance.sandbox_id,
                self.execution_id,
                ArtifactKind.EXECUTION_CHECKPOINT,
                _artifacts.CHECKPOINT_FORMAT,
                key,
                lambda archive: self._session.checkpoint(boundary, archive),
            )

    async def snapshot_container(self, key: str) -> ArtifactManifest:
        """Publish a pause-consistent root-filesystem snapshot of this running execution.

        The workload is paused while its root is exported and resumed afterwards.
        Restore it with ``Sandbox.create(container_snapshot=key)``. The archive holds
        every file the workload wrote outside mounts and must be protected like it.

        Args:
            key: A new artifact key; see :func:`isolattice.validate_artifact_key`.

        Returns:
            The integrity manifest published with the snapshot.
        """

        async def produce(archive: Path) -> None:
            await self._session.snapshot_boundary(boundary, archive)
            await _validate_rootfs(archive)

        async with self._artifact_lock:
            boundary = self._running_boundary("snapshotted")
            return await _publish(
                self._instance._owner,
                self._session,
                self._instance.sandbox_id,
                self.execution_id,
                ArtifactKind.CONTAINER_SNAPSHOT,
                _artifacts.ROOTFS_FORMAT,
                key,
                produce,
            )

    async def _launch(self, boundary: Boundary) -> asyncio.subprocess.Process:
        process = await self._session.start_boundary(boundary, self._request)
        self._process = process
        return process

    async def _pump(
        self, reader: asyncio.StreamReader, stream: Literal["stdout", "stderr"]
    ) -> None:
        while chunk := await reader.read(_CHUNK_SIZE):
            if stream == "stdout":
                self._stdout_truncated |= _capture(
                    self._stdout, chunk, self._instance.policy.stdout_limit
                )
                item = OutputChunk("stdout", chunk)
            else:
                self._stderr_truncated |= _capture(
                    self._stderr, chunk, self._instance.policy.stderr_limit
                )
                item = OutputChunk("stderr", chunk)
            await self._queue.put(item)

    async def _write_stdin(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        try:
            for offset in range(0, len(data), _CHUNK_SIZE):
                writer.write(data[offset : offset + _CHUNK_SIZE])
                await writer.drain()
            writer.close()
            await writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise SandboxExecutionError(
                "workload closed stdin before input was delivered"
            ) from error

    def _check_stop(self) -> None:
        # Backends may absorb cancellation; never progress the workload after a stop.
        if self._cancel_requested:
            raise asyncio.CancelledError

    async def _execute(self) -> int:
        boundary = await self._allocate()
        self._check_stop()
        process = await self._launch(boundary)
        self._check_stop()
        if process.stdout is None or process.stderr is None:
            raise SandboxBackendError("backend attach process did not provide output pipes")
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._pump(process.stdout, "stdout"))
                group.create_task(self._pump(process.stderr, "stderr"))
                if self._request.stdin is not None:
                    if process.stdin is None:
                        raise SandboxBackendError("backend attach process did not provide stdin")
                    group.create_task(self._write_stdin(process.stdin, self._request.stdin))
                client_exit_code = await _root_exit(process)
                await self._session.workload_exited(boundary)
        except BaseExceptionGroup as grouped:
            raise _sole_error(grouped) from None
        # Both pumps reached end of file, so the stream is complete before cleanup.
        self._output_done.set()
        return await self._session.exit_code(boundary, client_exit_code)

    async def _cleanup(self) -> TerminationReport | None:
        failures: list[BaseException] = []
        report: TerminationReport | None = None
        drains: list[asyncio.Task[bool]] = []
        if self._process is not None:
            drains.extend(
                asyncio.create_task(discard_remaining(reader))
                for reader in (self._process.stdout, self._process.stderr)
                if reader is not None
            )
            if self._process.stdin is not None:
                try:
                    self._process.stdin.close()
                except OSError as error:
                    failures.append(error)
        if self._boundary is not None:
            try:
                report = await self._session.terminate(
                    self._boundary, self._instance.policy.termination, self._force_requested
                )
            except BaseException as error:
                failures.append(error)
        if self._process is not None:
            try:
                await _reap_attach(self._process)
            except BaseException as error:
                failures.append(error)
        try:
            await self._release_staging()
        except BaseException as error:
            failures.append(error)
        try:
            async with asyncio.timeout(_DRAIN_TIMEOUT):
                drain_results = await asyncio.gather(*drains, return_exceptions=True)
        except TimeoutError:
            for drain in drains:
                drain.cancel()
            await asyncio.gather(*drains, return_exceptions=True)
            drain_results = []
            failures.append(
                SandboxBackendError("backend output pipes did not close during cleanup")
            )
        failures.extend(result for result in drain_results if isinstance(result, BaseException))
        if report is not None and any(result is True for result in drain_results):
            report = replace(
                report, notes=(*report.notes, "unread output was discarded during cleanup")
            )
        raise_combined("boundary and attach cleanup failed", *failures)
        return report

    async def _run(self) -> ExecutionResult:
        loop = asyncio.get_running_loop()
        started = loop.time()
        exit_code: int | None = None
        failure: BaseException | None = None
        interrupted: asyncio.CancelledError | None = None
        timed_out = False
        work: asyncio.Task[int] | None = None
        if not self._cancel_requested:
            work = asyncio.create_task(self._execute(), name=f"isolattice-work-{self.execution_id}")
            self._work = work
            timeout = self._request.timeout or self._instance.policy.execution_timeout
            stopper = asyncio.create_task(self._stop_requested.wait())
            try:
                async with asyncio.timeout(timeout):
                    await asyncio.wait((work, stopper), return_when=asyncio.FIRST_COMPLETED)
            except TimeoutError:
                timed_out = not work.done()
            except asyncio.CancelledError as error:
                if not work.done():
                    interrupted = error
                    self._cancel_requested = True
            finally:
                stopper.cancel()
            deadline = self._begin_deadline()
            # A stop request already cancelled the work; a second cancellation would
            # interrupt the backend's own cancellation handling (e.g. reconciliation).
            if not work.done() and not work.cancelling():
                work.cancel()
            late = await wait_until(work, deadline)
            interrupted = interrupted or late
            if not work.done():
                self._abandon_late(
                    asyncio.create_task(self._late_cleanup(work)),
                    "the execution was unwinding",
                    interrupted,
                )
            if work.cancelled():
                failure = (
                    SandboxTimeoutError("execution exceeded its deadline")
                    if timed_out
                    else asyncio.CancelledError()
                )
            elif (work_error := work.exception()) is not None:
                failure = work_error
            else:
                exit_code = work.result()
        else:
            failure = asyncio.CancelledError()
        deadline = self._begin_deadline()
        cleanup_task = asyncio.create_task(self._cleanup())
        late = await wait_until(cleanup_task, deadline)
        interrupted = interrupted or late
        if not cleanup_task.done():
            self._abandon_late(cleanup_task, "verifying termination", interrupted, failure)
        cleanup = await settle(cleanup_task)
        self._termination = cleanup.value
        if cleanup.error is not None:
            self._instance._cleanup_failures.append(cleanup.error)
        if failure is not None and _contains_uncertain_termination(failure):
            self._instance._cleanup_failures.append(failure)
        if isinstance(failure, SandboxTimeoutError):
            failure.report = self._termination
        if failure is None and interrupted is not None:
            failure = interrupted
        self._output_done.set()
        raise_combined("execution and cleanup failed", failure, cleanup.error)
        if exit_code is None or self._termination is None:
            raise SandboxBackendError("execution completed without a verified termination report")
        return ExecutionResult(
            sandbox_id=self._instance.sandbox_id,
            execution_id=self.execution_id,
            exit_code=exit_code,
            stdout=bytes(self._stdout),
            stderr=bytes(self._stderr),
            stdout_truncated=self._stdout_truncated,
            stderr_truncated=self._stderr_truncated,
            duration=loop.time() - started,
            termination=self._termination,
        )

    def _abandon_late(
        self,
        task: asyncio.Task[TerminationReport | None],
        phase: str,
        interrupted: asyncio.CancelledError | None,
        failure: BaseException | None = None,
    ) -> NoReturn:
        error = self._deadline_exceeded(phase)
        self._termination = error.report
        self._instance._emit(EventKind.TERMINATION_UNKNOWN, self.execution_id, phase=phase)
        self._instance._adopt_late_cleanup(task)
        self._instance._cleanup_failures.append(error)
        if isinstance(failure, SandboxTimeoutError):
            failure.report = error.report
        self._output_done.set()
        raise_with_cleanup("execution cleanup exceeded its deadline", error, failure, interrupted)

    async def output(self) -> AsyncGenerator[OutputChunk, None]:
        """Yield stdout/stderr with backpressure; exactly one consumer is allowed."""
        if self._stream_claimed:
            raise SandboxStateError("execution output already has a consumer")
        self._stream_claimed = True
        while True:
            if self._output_done.is_set() and self._queue.empty():
                return
            get = asyncio.create_task(self._queue.get())
            finished = asyncio.create_task(self._output_done.wait())
            try:
                done, _ = await asyncio.wait((get, finished), return_when=asyncio.FIRST_COMPLETED)
                if get in done:
                    yield get.result()
            finally:
                for task in (get, finished):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(get, finished, return_exceptions=True)

    async def wait(self) -> ExecutionResult:
        """Wait for execution and cleanup; consume output concurrently to avoid backpressure."""
        try:
            result = await asyncio.shield(self._task)
        except asyncio.CancelledError as cancellation:
            cleanup_error: BaseException | None = None
            if not self._task.done():
                self._request_stop()
                outcome = await settle(self._task)
                if not isinstance(outcome.error, asyncio.CancelledError):
                    cleanup_error = outcome.error
            self._mark_observed()
            raise_with_cleanup(
                "execution wait cancelled and cleanup failed", cancellation, cleanup_error
            )
        except BaseException:
            self._mark_observed()
            raise
        self._mark_observed()
        return result

    async def cancel(self, *, force: bool = False) -> TerminationReport | None:
        """Cancel this execution's independent boundary and await owned cleanup."""
        self._request_stop(force=force)
        outcome = await settle(self._task)
        self._mark_observed()
        error = outcome.error
        if isinstance(error, asyncio.CancelledError):
            error = None
        elif isinstance(error, BaseExceptionGroup):
            # The requested cancellation is expected; report only the other failures.
            _, rest = error.split(asyncio.CancelledError)
            error = None if rest is None else _sole_error(rest)
        raise_combined("execution cancellation and cleanup failed", outcome.interrupted, error)
        return self._termination


class SandboxInstance:
    """An async owner of independent, per-execution backend boundaries."""

    def __init__(
        self, owner: Sandbox, session: BackendSession, policy: SandboxPolicy, sandbox_id: str
    ) -> None:
        self._owner = owner
        self._session = session
        self.policy = policy
        self.sandbox_id = sandbox_id
        self._lock = asyncio.Lock()
        self._state = "ready"
        self._active: set[ExecutionHandle] = set()
        self._unobserved: dict[ExecutionHandle, BaseException] = {}
        self._cleanup_failures: list[BaseException] = []
        self._stop_task: asyncio.Task[tuple[TerminationReport, ...]] | None = None
        self._force_on_stop = False
        self._late_cleanups: set[asyncio.Task[TerminationReport | None]] = set()

    def _adopt_late_cleanup(self, task: asyncio.Task[TerminationReport | None]) -> None:
        self._late_cleanups.add(task)
        task.add_done_callback(self._late_cleanup_done)

    def _emit(
        self, kind: EventKind, execution_id: str | None = None, **detail: str | int | bool | None
    ) -> None:
        self._owner._emit(kind, self.sandbox_id, self._session.backend_name, execution_id, **detail)

    def _late_cleanup_done(self, task: asyncio.Task[TerminationReport | None]) -> None:
        self._late_cleanups.discard(task)
        if task.cancelled():
            self._emit(EventKind.LATE_CLEANUP_FAILED, error="CancelledError")
            return
        error = task.exception()
        if error is not None:
            self._emit(EventKind.LATE_CLEANUP_FAILED, error=type(error).__name__)
            return
        report = task.result()
        self._emit(
            EventKind.LATE_CLEANUP_FINISHED,
            released=report is not None and report.resources_released,
        )

    @property
    def pending_late_cleanups(self) -> int:
        """Background cleanups still running after their hard deadline was reported."""
        return len(self._late_cleanups)

    async def __aenter__(self) -> Self:
        if self._state != "ready":
            raise SandboxStateError("sandbox instance is not ready")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.close()
        except BaseException as cleanup:
            raise_combined("sandbox body and cleanup failed", exc, cleanup)

    def _execution_done(self, handle: ExecutionHandle) -> None:
        handle._output_done.set()
        self._active.discard(handle)
        if handle._task.cancelled():
            self._emit(EventKind.EXECUTION_FAILED, handle.execution_id, error="CancelledError")
            return
        error = handle._task.exception()
        if error is None:
            self._emit(
                EventKind.EXECUTION_FINISHED,
                handle.execution_id,
                exit_code=handle._task.result().exit_code,
            )
            return
        self._emit(EventKind.EXECUTION_FAILED, handle.execution_id, error=type(error).__name__)
        if not handle._observed:
            self._unobserved[handle] = error

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        """Start a workload inside this instance and return its handle.

        Raises:
            TypeError: If ``request`` is not an ``ExecutionRequest``.
            SandboxStateError: If the instance is not running.
        """
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request must be an ExecutionRequest")
        async with self._lock:
            if self._state != "ready":
                raise SandboxStateError("sandbox instance is not accepting executions")
            if len(self._active) >= self.policy.max_concurrent_executions:
                raise SandboxStateError("sandbox execution concurrency budget is exhausted")
            handle = ExecutionHandle(self, self._session, request, uuid.uuid4().hex)
            self._active.add(handle)
        self._emit(EventKind.EXECUTION_STARTED, handle.execution_id)
        return handle

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute while draining both streams into bounded result captures."""
        handle = await self.start(request)
        try:
            async for _ in handle.output():
                pass
            return await handle.wait()
        except BaseException as error:
            cleanup_error: BaseException | None = None
            if not handle._task.done():
                handle._request_stop()
                outcome = await settle(handle._task)
                handle._mark_observed()
                if not isinstance(outcome.error, asyncio.CancelledError):
                    cleanup_error = outcome.error
            raise_with_cleanup("execution failed and cleanup failed", error, cleanup_error)

    async def _stop(self) -> tuple[TerminationReport, ...]:
        handles = tuple(self._active)
        for handle in handles:
            handle._request_stop(force=self._force_on_stop)
        failures: list[BaseException] = []
        reports: list[TerminationReport] = []
        for handle in handles:
            outcome = await settle(handle._task)
            handle._mark_observed()
            if outcome.error is not None and not isinstance(outcome.error, asyncio.CancelledError):
                failures.append(outcome.error)
            if handle._termination is not None:
                reports.append(handle._termination)
        failures.extend(self._unobserved.values())
        for failure in self._cleanup_failures:
            if failure not in failures:
                failures.append(failure)
        if self._late_cleanups:
            failures.append(
                TerminationVerificationError(
                    "instance-wide backend resources were retained because boundary "
                    "cleanup is still running after its deadline"
                )
            )
        else:
            close = await settle(asyncio.create_task(self._session.close()))
            if close.error is not None:
                failures.append(close.error)
            if close.interrupted is not None:
                failures.append(close.interrupted)
        if failures:
            self._state = "failed"
            self._emit(EventKind.SANDBOX_CLEANUP_FAILED, failures=len(failures))
            raise BaseExceptionGroup("sandbox instance cleanup failed", failures)
        self._state = "closed"
        self._owner._instance_closed(self)
        self._emit(EventKind.SANDBOX_CLOSED)
        return tuple(reports)

    async def _stop_owned(self, *, force: bool) -> tuple[TerminationReport, ...]:
        async with self._lock:
            if force:
                self._force_on_stop = True
                for handle in tuple(self._active):
                    handle._request_stop(force=True)
            if self._stop_task is None:
                self._state = "stopping"
                self._stop_task = asyncio.create_task(self._stop())
            task = self._stop_task
        outcome = await settle(task)
        raise_combined("sandbox stop cancelled or failed", outcome.interrupted, outcome.error)
        assert outcome.value is not None
        return outcome.value

    async def stop(self) -> tuple[TerminationReport, ...]:
        """Freeze admissions, stop owned executions and verify their cleanup."""
        return await self._stop_owned(force=False)

    async def kill(self) -> tuple[TerminationReport, ...]:
        """Escalate active execution boundaries to forced termination."""
        return await self._stop_owned(force=True)

    async def close(self) -> None:
        """Stop the instance and verify cleanup; equivalent to ``stop()``."""
        await self.stop()

    @property
    def workspace_path(self) -> str | None:
        """Persistent workspace path seen by workloads, when the policy requested one."""
        return self._session.workspace_path

    async def snapshot(self, key: str) -> ArtifactManifest:
        """Publish a cold workspace snapshot and its SHA-256 manifest to the artifact store.

        No execution may be active; new executions are refused until it finishes.

        Args:
            key: A new artifact key; see :func:`isolattice.validate_artifact_key`.

        Returns:
            The integrity manifest published with the snapshot.
        """
        if not self.policy.workspace:
            raise SandboxCapabilityError("snapshots require a policy with workspace=True")

        async def produce(archive: Path) -> None:
            await self._session.export_workspace(archive)
            await _validate_workspace(archive)

        async with self._lock:
            if self._state != "ready":
                raise SandboxStateError("sandbox instance is not ready for a snapshot")
            if self._active:
                raise SandboxStateError("cold snapshots require no active executions")
            return await _publish(
                self._owner,
                self._session,
                self.sandbox_id,
                None,
                ArtifactKind.WORKSPACE_SNAPSHOT,
                _artifacts.WORKSPACE_FORMAT,
                key,
                produce,
            )

    async def restore(self, key: str, *, timeout: float | None = None) -> ExecutionHandle:  # noqa: ASYNC109 - the resumed execution's timeout
        """Resume a verified execution checkpoint as a new owned execution.

        Args:
            key: The artifact key of an execution checkpoint from this backend.
            timeout: Execution timeout for the resumed workload.

        Returns:
            The handle of the resumed execution.
        """
        request = ExecutionRequest(argv=(_RESTORED_ARGV,), timeout=timeout)
        staging, archive = await _fetch(
            self._owner,
            self._session,
            self.sandbox_id,
            key,
            ArtifactKind.EXECUTION_CHECKPOINT,
            _artifacts.CHECKPOINT_FORMAT,
            None,
        )
        try:
            async with self._lock:
                if self._state != "ready":
                    raise SandboxStateError("sandbox instance is not accepting executions")
                if len(self._active) >= self.policy.max_concurrent_executions:
                    raise SandboxStateError("sandbox execution concurrency budget is exhausted")
                handle = ExecutionHandle(
                    self,
                    self._session,
                    request,
                    uuid.uuid4().hex,
                    restore_source=archive,
                    staging=staging,
                )
                self._active.add(handle)
        except BaseException as error:
            cleanup = await settle(asyncio.create_task(_artifacts.unstage(staging)))
            raise_with_cleanup(
                "checkpoint restore and staging cleanup failed",
                error,
                cleanup.error,
                cleanup.interrupted,
            )
        self._emit(EventKind.EXECUTION_STARTED, handle.execution_id, restored=True)
        return handle


class Sandbox:
    """Explicit async owner for instances of a selected backend."""

    def __init__(
        self,
        *,
        backend: Backend,
        artifact_store: ArtifactStore | None = None,
        event_sink: EventSink | None = None,
        staging_directory: str | os.PathLike[str] | None = None,
    ) -> None:
        """Own instances of ``backend``.

        Args:
            backend: The backend that prepares every instance.
            artifact_store: Where snapshots and checkpoints are published; artifact
                operations fail closed when it is omitted.
            event_sink: Receives lifecycle events; defaults to :class:`LoggingEventSink`.
            staging_directory: Parent of private per-operation staging directories;
                defaults to the system temporary directory. Staged checkpoints hold
                workload memory, so it must be as protected as the workload.

        Raises:
            TypeError: If an argument has the wrong type.
        """
        if not isinstance(backend, Backend):
            raise TypeError("backend must be a Backend")
        if artifact_store is not None and not isinstance(artifact_store, ArtifactStore):
            raise TypeError("artifact_store must be an ArtifactStore")
        if event_sink is not None and not isinstance(event_sink, EventSink):
            raise TypeError("event_sink must be an EventSink")
        if staging_directory is not None and not isinstance(staging_directory, (str, os.PathLike)):
            raise TypeError("staging_directory must be a path")
        self._backend = backend
        self._artifact_store = artifact_store
        self._event_sink = event_sink if event_sink is not None else LoggingEventSink()
        self._staging_directory = None if staging_directory is None else Path(staging_directory)
        self._instances: set[SandboxInstance] = set()
        self._lock = asyncio.Lock()
        self._state = "open"
        self._close_task: asyncio.Task[None] | None = None

    def _require_store(self) -> ArtifactStore:
        if self._artifact_store is None:
            raise SandboxCapabilityError(
                "snapshots and checkpoints require Sandbox(artifact_store=...)"
            )
        return self._artifact_store

    def _emit(
        self,
        kind: EventKind,
        sandbox_id: str,
        backend: str,
        execution_id: str | None,
        **detail: str | int | bool | None,
    ) -> None:
        try:
            self._event_sink.emit(
                SandboxEvent(
                    kind=kind,
                    sandbox_id=sandbox_id,
                    backend=backend,
                    execution_id=execution_id,
                    detail=detail,
                )
            )
        except Exception:
            # Telemetry must never interrupt containment or cleanup.
            _logger.exception("isolattice event sink failed for %s", kind.value)

    async def __aenter__(self) -> Self:
        if self._state != "open":
            raise SandboxStateError("sandbox owner is closed")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self.close()
        except BaseException as cleanup:
            raise_combined("sandbox body and cleanup failed", exc, cleanup)

    async def discover(self) -> tuple[BackendReport, ...]:
        """Probe the backend and return its capability reports."""
        if self._state != "open":
            raise SandboxStateError("sandbox owner is closed")
        return (await self._backend.discover(),)

    async def create(
        self,
        *,
        policy: SandboxPolicy | None = None,
        workspace_snapshot: str | None = None,
        container_snapshot: str | None = None,
    ) -> SandboxInstance:
        """Prepare an instance, optionally from verified snapshots in the artifact store.

        Args:
            policy: The required security policy; defaults to ``SandboxPolicy()``.
            workspace_snapshot: Key of a workspace snapshot that populates the workspace.
            container_snapshot: Key of a container snapshot every execution starts from.

        Returns:
            The ready instance, owned by this sandbox.
        """
        selected = policy if policy is not None else SandboxPolicy()
        if not isinstance(selected, SandboxPolicy):
            raise TypeError("policy must be a SandboxPolicy")
        for key in (workspace_snapshot, container_snapshot):
            if key is not None:
                validate_artifact_key(key)
                self._require_store()
        if workspace_snapshot is not None and not selected.workspace:
            raise SandboxCapabilityError("restoring a snapshot requires workspace=True")
        if self._state != "open":
            raise SandboxStateError("sandbox owner is closed")
        sandbox_id = uuid.uuid4().hex
        session = await self._backend.prepare(selected, sandbox_id)
        try:
            if container_snapshot is not None:
                await self._import(
                    session,
                    sandbox_id,
                    container_snapshot,
                    ArtifactKind.CONTAINER_SNAPSHOT,
                    _artifacts.ROOTFS_FORMAT,
                    _validate_rootfs,
                    session.import_root,
                )
            if workspace_snapshot is not None:
                await self._import(
                    session,
                    sandbox_id,
                    workspace_snapshot,
                    ArtifactKind.WORKSPACE_SNAPSHOT,
                    _artifacts.WORKSPACE_FORMAT,
                    _validate_workspace,
                    session.import_workspace,
                )
            async with self._lock:
                if self._state != "open":
                    raise SandboxStateError("sandbox owner closed during backend preparation")
                instance = SandboxInstance(self, session, selected, sandbox_id)
                self._instances.add(instance)
        except BaseException as error:
            close = await settle(asyncio.create_task(session.close()))
            raise_with_cleanup(
                "instance preparation and session cleanup failed",
                error,
                close.interrupted,
                close.error,
            )
        self._emit(EventKind.SANDBOX_CREATED, sandbox_id, session.backend_name, None)
        return instance

    async def _import(
        self,
        session: BackendSession,
        sandbox_id: str,
        key: str,
        kind: ArtifactKind,
        archive_format: str,
        validate: Callable[[Path], Awaitable[None]],
        load: Callable[[Path], Awaitable[None]],
    ) -> None:
        staging, archive = await _fetch(
            self, session, sandbox_id, key, kind, archive_format, validate
        )
        failure: BaseException | None = None
        try:
            await load(archive)
        except BaseException as error:
            failure = error
        cleanup = await settle(asyncio.create_task(_artifacts.unstage(staging)))
        raise_combined(
            "artifact import and staging cleanup failed",
            failure,
            cleanup.error,
            cleanup.interrupted,
        )

    def _instance_closed(self, instance: SandboxInstance) -> None:
        self._instances.discard(instance)

    async def _close(self) -> None:
        tasks = [asyncio.create_task(instance.close()) for instance in tuple(self._instances)]
        failures: list[BaseException] = []
        for task in tasks:
            outcome = await settle(task)
            if outcome.error is not None:
                failures.append(outcome.error)
        if failures:
            self._state = "failed"
            raise BaseExceptionGroup("sandbox owner cleanup failed", failures)
        self._state = "closed"

    async def close(self) -> None:
        """Close the owner, stopping every instance it created."""
        async with self._lock:
            if self._close_task is None:
                self._state = "closing"
                self._close_task = asyncio.create_task(self._close())
            task = self._close_task
        outcome = await settle(task)
        raise_combined(
            "sandbox owner close cancelled or failed", outcome.interrupted, outcome.error
        )
