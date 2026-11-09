"""Fault injection for runtime ownership, cleanup and backend-contract violations.

The fakes prove lifecycle bookkeeping only; they provide no isolation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast, override

import pytest

from isolattice import (
    ArtifactManifest,
    EventKind,
    EventSink,
    ExecutionHandle,
    ExecutionRequest,
    LocalArtifactStore,
    Sandbox,
    SandboxArtifactError,
    SandboxBackendError,
    SandboxEvent,
    SandboxExecutionError,
    SandboxPolicy,
    SandboxStateError,
    TerminationPolicy,
    TerminationReport,
    TerminationVerificationError,
)
from isolattice import runtime as runtime_module
from isolattice.backend import Boundary
from tests.unit.test_async_runtime import (
    FakeBackend,
    FakeSession,
    StallingSession,
    _finish_late_cleanups,
    exceptions,
    request,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_SLEEP = "import time; time.sleep(30)"


class RecordingSink(EventSink):
    def __init__(self) -> None:
        self.events: list[SandboxEvent] = []

    @override
    def emit(self, event: SandboxEvent) -> None:
        self.events.append(event)

    def kinds(self) -> list[EventKind]:
        return [event.kind for event in self.events]


class FailingSink(EventSink):
    @override
    def emit(self, event: SandboxEvent) -> None:
        raise RuntimeError("telemetry is down")


class HeldProcess:
    """An attach client whose ``wait`` stays pending, as when descendants hold pipes."""

    def __init__(self, *, killable: bool = True, lookup_error: bool = False) -> None:
        self.returncode: int | None = None
        self.killable = killable
        self.lookup_error = lookup_error
        self.killed = asyncio.Event()

    async def wait(self) -> int:
        if self.killable:
            await self.killed.wait()
            self.returncode = -9
            return -9
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    def kill(self) -> None:
        if self.lookup_error:
            raise ProcessLookupError
        self.killed.set()


def _process(held: HeldProcess) -> asyncio.subprocess.Process:
    # The runtime touches only ``returncode``, ``wait`` and ``kill`` on attach clients.
    return cast("asyncio.subprocess.Process", held)


def test_root_exit_observes_returncode_while_wait_is_held_open() -> None:
    async def check() -> None:
        process = HeldProcess(killable=False)
        asyncio.get_running_loop().call_later(0.05, setattr, process, "returncode", 7)
        assert await runtime_module._root_exit(_process(process)) == 7

    asyncio.run(check())


def test_attach_reaping_kills_and_bounds_stuck_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_ATTACH_EXIT_GRACE", 0.01)
    monkeypatch.setattr(runtime_module, "_ATTACH_REAP_TIMEOUT", 0.05)

    async def check() -> None:
        killable = HeldProcess()
        await runtime_module._reap_attach(_process(killable))
        assert killable.killed.is_set()
        with pytest.raises(SandboxBackendError, match="could not be reaped"):
            await runtime_module._reap_attach(
                _process(HeldProcess(killable=False, lookup_error=True))
            )

    asyncio.run(check())


class _NoManifestStore(LocalArtifactStore):
    @override
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        await asyncio.to_thread(destination.write_bytes, b"")
        return None  # pyrefly: ignore[bad-return]


def test_store_returning_no_manifest_is_rejected(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()

    async def check() -> None:
        store = _NoManifestStore(tmp_path)
        async with (
            Sandbox(
                backend=FakeBackend(FakeSession()), artifact_store=store, staging_directory=staging
            ) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxArtifactError, match="no ArtifactManifest"):
                await instance.restore("k")

    asyncio.run(check())
    assert list(staging.iterdir()) == []


class _ReplacingSession(FakeSession):
    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        boundary = await super().create_boundary(request, sandbox_id, execution_id, register)
        register(boundary)
        register(Boundary("other", "fake", sandbox_id, execution_id))
        return boundary


def test_backend_cannot_replace_an_owned_boundary() -> None:
    async def check() -> None:
        session = _ReplacingSession()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxBackendError, match="replace an owned"):
                await instance.run(request("pass"))
        assert session.terminations == 1

    asyncio.run(check())


class _UnregisteredStall(FakeSession):
    """Stalls before registering a boundary and ignores cancellation until released."""

    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        self.registered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        raise RuntimeError("released without a boundary")


def test_late_cleanup_without_a_boundary_reports_the_execution() -> None:
    async def check() -> None:
        sink = RecordingSink()
        session = _UnregisteredStall()
        policy = SandboxPolicy(termination=TerminationPolicy(cleanup_deadline=0.2))
        sandbox = Sandbox(backend=FakeBackend(session), event_sink=sink)
        instance = await sandbox.create(policy=policy)
        handle = await instance.start(request(_SLEEP))
        await session.registered.wait()
        with pytest.raises(TerminationVerificationError) as raised:
            await handle.cancel()
        report = raised.value.report
        assert report is not None
        assert report.boundary_id == handle.execution_id
        assert not any("reconcile" in note for note in report.notes)
        session.release.set()
        await _finish_late_cleanups(instance)
        finished = [e for e in sink.events if e.kind is EventKind.LATE_CLEANUP_FINISHED]
        assert finished
        assert finished[0].detail["released"] is False
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


class _FailingLateTermination(StallingSession):
    @override
    async def terminate(
        self, boundary: Boundary, policy: TerminationPolicy, force_requested: asyncio.Event
    ) -> TerminationReport:
        await super().terminate(boundary, policy, force_requested)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            # Honour the cancellation that arrived while the stall ignored it.
            raise asyncio.CancelledError
        raise TerminationVerificationError("late termination could not be verified")


@pytest.mark.parametrize("outcome", ["error", "cancelled"])
def test_failed_or_cancelled_late_cleanup_is_reported(outcome: str) -> None:
    async def check() -> None:
        sink = RecordingSink()
        session = _FailingLateTermination("terminate")
        policy = SandboxPolicy(termination=TerminationPolicy(cleanup_deadline=0.2))
        sandbox = Sandbox(backend=FakeBackend(session), event_sink=sink)
        instance = await sandbox.create(policy=policy)
        handle = await instance.start(request(_SLEEP))
        await session.started.wait()
        with pytest.raises((TerminationVerificationError, BaseExceptionGroup)):
            await handle.cancel(force=True)
        (late,) = tuple(instance._late_cleanups)
        if outcome == "cancelled":
            late.cancel()
        session.release.set()
        await _finish_late_cleanups(instance)
        failed = [e for e in sink.events if e.kind is EventKind.LATE_CLEANUP_FAILED]
        expected = "CancelledError" if outcome == "cancelled" else "TerminationVerificationError"
        assert [event.detail["error"] for event in failed] == [expected]
        session.release.set()
        for process in session.processes.values():
            if process.returncode is None:
                process.kill()
                await process.wait()
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


def test_stdin_rejected_by_the_workload_is_reported() -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=FakeBackend(FakeSession())) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxExecutionError, match="closed stdin"):
                await instance.run(
                    request("import os; os.close(0)", stdin=b"x" * (16 * 1024 * 1024))
                )

    asyncio.run(check())


class _PipelessSession(FakeSession):
    def __init__(self, missing: str) -> None:
        super().__init__()
        self.missing = missing

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        process = await super().start_boundary(boundary, request)
        if self.missing == "stdout":
            process.stdout = None
        else:
            stdin = process.stdin
            assert stdin is not None
            stdin.close()
            process.stdin = None
        return process


@pytest.mark.parametrize(
    ("missing", "message"), [("stdout", "output pipes"), ("stdin", "provide stdin")]
)
def test_attach_processes_must_provide_their_pipes(missing: str, message: str) -> None:
    async def check() -> None:
        session = _PipelessSession(missing)
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxBackendError, match=message):
                await instance.run(request("import sys; sys.stdin.read()", stdin=b"in"))
        assert session.terminations == 1

    asyncio.run(check())


class _StdinCloseFails(FakeSession):
    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        process = await super().start_boundary(boundary, request)
        stdin = process.stdin
        assert stdin is not None
        real_close = stdin.close

        def close() -> None:
            real_close()
            raise OSError("injected stdin close failure")

        stdin.close = close
        return process


def test_cleanup_failures_are_all_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_reap(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise SandboxBackendError("injected reap failure")

    async def failing_release(self: runtime_module.ExecutionHandle) -> None:
        raise OSError("injected staging failure")

    monkeypatch.setattr(runtime_module, "_reap_attach", failing_reap)
    monkeypatch.setattr(runtime_module.ExecutionHandle, "_release_staging", failing_release)

    async def check() -> None:
        session = _StdinCloseFails()
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        handle = await instance.start(request(_SLEEP, stdin=b""))
        await session.started.wait()
        with pytest.raises(BaseExceptionGroup) as raised:
            await handle.cancel()
        messages = {str(error) for error in exceptions(raised.value)}
        assert {
            "injected stdin close failure",
            "injected reap failure",
            "injected staging failure",
        } <= messages
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


class _OpenPipeSession(FakeSession):
    """Returns an attach client whose output pipe never reaches end of file."""

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        process = await super().start_boundary(boundary, request)
        process.stdout = asyncio.StreamReader()
        return process


def test_output_pipes_that_never_close_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "_DRAIN_TIMEOUT", 0.05)

    async def check() -> None:
        session = _OpenPipeSession()
        async with Sandbox(backend=FakeBackend(session)) as sandbox:
            instance = await sandbox.create()
            handle = await instance.start(request(_SLEEP))
            await session.started.wait()
            with pytest.raises(SandboxBackendError, match="did not close"):
                await handle.cancel()
            with pytest.raises(BaseExceptionGroup):
                await instance.close()
            sandbox._instances.clear()

    asyncio.run(check())


def test_owner_task_cancellation_stops_and_cleans_up() -> None:
    async def check() -> None:
        session = FakeSession()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(request(_SLEEP))
            await session.started.wait()
            handle._task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handle.wait()
            assert handle._task.cancelled() or handle._task.exception() is not None
        assert session.terminations == 1

    asyncio.run(check())


def test_cancellation_during_cleanup_of_a_finished_workload_is_reported() -> None:
    async def check() -> None:
        session = FakeSession()
        session.terminate_gate = asyncio.Event()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(request("pass"))
            async for _ in handle.output():
                pass
            await session.terminating.wait()
            handle._task.cancel()
            await asyncio.sleep(0.01)
            session.terminate_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await handle.wait()

    asyncio.run(check())


class _ReportlessSession(FakeSession):
    @override
    async def terminate(
        self, boundary: Boundary, policy: TerminationPolicy, force_requested: asyncio.Event
    ) -> TerminationReport:
        await super().terminate(boundary, policy, force_requested)
        return None  # pyrefly: ignore[bad-return]


def test_completion_without_a_termination_report_is_a_backend_error() -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=FakeBackend(_ReportlessSession())) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxBackendError, match="verified termination report"):
                await instance.run(request("pass"))

    asyncio.run(check())


def test_cancelled_wait_stops_the_execution_and_keeps_cleanup_errors() -> None:
    async def check() -> None:
        session = FakeSession()
        session.fail_termination = True
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        handle = await instance.start(request(_SLEEP))
        await session.started.wait()
        waiter = asyncio.create_task(handle.wait())
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(BaseExceptionGroup) as raised:
            await waiter
        kinds = {type(error) for error in exceptions(raised.value)}
        assert {asyncio.CancelledError, TerminationVerificationError} <= kinds
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


def test_cancelled_run_keeps_cleanup_errors() -> None:
    async def check() -> None:
        session = FakeSession()
        session.fail_termination = True
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        runner = asyncio.create_task(instance.run(request(_SLEEP)))
        await session.started.wait()
        runner.cancel()
        with pytest.raises(BaseExceptionGroup) as raised:
            await runner
        kinds = {type(error) for error in exceptions(raised.value)}
        assert {asyncio.CancelledError, TerminationVerificationError} <= kinds
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


def test_instance_and_owner_state_guards() -> None:
    async def check() -> None:
        session = FakeSession()
        sandbox = Sandbox(backend=FakeBackend(session))
        with pytest.raises(TypeError, match="SandboxPolicy"):
            await sandbox.create(policy={})  # pyrefly: ignore[bad-argument-type]
        instance = await sandbox.create(
            policy=SandboxPolicy(workspace=False, max_concurrent_executions=1)
        )
        with pytest.raises(TypeError, match="ExecutionRequest"):
            await instance.start(("python",))  # pyrefly: ignore[bad-argument-type]
        await instance.close()
        with pytest.raises(SandboxStateError, match="not ready"):
            async with instance:
                pass
        with pytest.raises(SandboxStateError, match="not accepting"):
            await instance.start(request("pass"))
        await sandbox.close()
        await sandbox.close()
        with pytest.raises(SandboxStateError, match="closed"):
            async with sandbox:
                pass
        with pytest.raises(SandboxStateError, match="closed"):
            await sandbox.discover()
        with pytest.raises(SandboxStateError, match="closed"):
            await sandbox.create()

    asyncio.run(check())


def test_restore_respects_instance_state_and_concurrency(tmp_path: Path) -> None:
    store_root = tmp_path / "store"
    store_root.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()
    from tests.unit.test_runtime_artifacts import WorkspaceSession

    async def check() -> None:
        session = WorkspaceSession(tmp_path)
        store = LocalArtifactStore(store_root)
        async with Sandbox(
            backend=FakeBackend(session), artifact_store=store, staging_directory=staging
        ) as sandbox:
            instance = await sandbox.create(policy=SandboxPolicy(max_concurrent_executions=1))
            handle = await instance.start(request(_SLEEP))
            await session.started.wait()
            await handle.checkpoint("run")
            with pytest.raises(SandboxStateError, match="concurrency"):
                await instance.restore("run")
            with pytest.raises(SandboxStateError, match="concurrency"):
                await instance.start(request("pass"))
            await handle.cancel(force=True)
            await instance.close()
            with pytest.raises(SandboxStateError, match="not accepting"):
                await instance.restore("run")
            instance.policy = SandboxPolicy(workspace=True)
            with pytest.raises(SandboxStateError, match="not ready for a snapshot"):
                await instance.snapshot("late")

    asyncio.run(check())
    assert list(staging.iterdir()) == []


def test_sandbox_validates_extension_arguments() -> None:
    backend = FakeBackend(FakeSession())
    with pytest.raises(TypeError, match="Backend"):
        Sandbox(backend=object())  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="ArtifactStore"):
        Sandbox(backend=backend, artifact_store=object())  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="EventSink"):
        Sandbox(backend=backend, event_sink=object())  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="path"):
        Sandbox(backend=backend, staging_directory=1)  # pyrefly: ignore[bad-argument-type]


def test_event_sink_failures_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=FakeBackend(FakeSession()), event_sink=FailingSink()) as sandbox,
            await sandbox.create() as instance,
        ):
            result = await instance.run(request("pass"))
            assert result.exit_code == 0

    with caplog.at_level(logging.ERROR, logger="isolattice"):
        asyncio.run(check())
    assert any("event sink failed" in record.getMessage() for record in caplog.records)


def test_body_and_cleanup_failures_are_combined() -> None:
    async def check() -> None:
        session = FakeSession()
        session.fail_termination = True
        handles: list[ExecutionHandle] = []

        async def failing_body() -> None:
            async with (
                Sandbox(backend=FakeBackend(session)) as sandbox,
                await sandbox.create() as instance,
            ):
                handles.append(await instance.start(request(_SLEEP)))
                await session.started.wait()
                raise KeyError("body")

        with pytest.raises(BaseExceptionGroup) as raised:
            await failing_body()
        leaves = exceptions(raised.value)
        assert any(isinstance(error, KeyError) for error in leaves)
        assert any(isinstance(error, TerminationVerificationError) for error in leaves)
        assert handles[0]._task.done()

    asyncio.run(check())
