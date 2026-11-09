import asyncio
import contextlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    BackendReport,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    ExecutionRequest,
    Sandbox,
    SandboxCapabilityError,
    SandboxInstance,
    SandboxPolicy,
    SandboxStateError,
    SandboxTimeoutError,
    TerminationPolicy,
    TerminationReport,
    TerminationVerificationError,
)
from isolattice.backend import Backend, BackendSession, Boundary


class FakeSession(BackendSession):
    """Lifecycle test double; it does not provide or prove isolation."""

    backend_name = "fake"

    def __init__(self) -> None:
        self.registered = asyncio.Event()
        self.launching = asyncio.Event()
        self.started = asyncio.Event()
        self.terminating = asyncio.Event()
        self.create_gate: asyncio.Event | None = None
        self.start_gate: asyncio.Event | None = None
        self.terminate_gate: asyncio.Event | None = None
        self.force_event: asyncio.Event | None = None
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.terminations = 0
        self.fail_termination = False
        self.fail_creation_unknown = False

    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        boundary = Boundary(execution_id, "fake", sandbox_id, execution_id)
        if self.fail_creation_unknown:
            self.registered.set()
            if self.create_gate is not None:
                try:
                    await self.create_gate.wait()
                except asyncio.CancelledError as cancellation:
                    raise BaseExceptionGroup(
                        "cancelled creation outcome is unknown",
                        [
                            cancellation,
                            TerminationVerificationError("injected uncertain creation outcome"),
                        ],
                    ) from None
            raise TerminationVerificationError("injected uncertain creation outcome")
        register(boundary)
        self.registered.set()
        if self.create_gate is not None:
            await self.create_gate.wait()
        return boundary

    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        boundary.start_requested = True
        self.launching.set()
        if self.start_gate is not None:
            await self.start_gate.wait()
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            stdin=asyncio.subprocess.PIPE
            if request.stdin is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.processes[boundary.boundary_id] = process
        self.started.set()
        return process

    async def workload_exited(self, boundary: Boundary) -> None:
        return None

    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        return client_exit_code

    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        self.terminations += 1
        self.force_event = force_requested
        self.terminating.set()
        if self.terminate_gate is not None:
            await self.terminate_gate.wait()
        process = self.processes.get(boundary.boundary_id)
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if self.fail_termination:
            raise TerminationVerificationError("injected cleanup uncertainty")
        return TerminationReport(boundary.boundary_id, 1, 2, "terminated", True)


class FakeBackend(Backend):
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.preparing = asyncio.Event()
        self.prepare_gate: asyncio.Event | None = None

    async def discover(self) -> BackendReport:
        return BackendReport(
            "fake",
            True,
            {
                Capability.PROCESS_ISOLATION: CapabilityEvidence(
                    CapabilityState.UNSUPPORTED, "test double"
                )
            },
        )

    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> FakeSession:
        self.preparing.set()
        if self.prepare_gate is not None:
            await self.prepare_gate.wait()
        return self.session


def request(
    code: str, *, stdin: bytes | None = None, timeout: float | None = None
) -> ExecutionRequest:
    executable = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
    return ExecutionRequest(argv=(executable, "-c", code), stdin=stdin, timeout=timeout)


def exceptions(error: BaseException) -> list[BaseException]:
    if isinstance(error, BaseExceptionGroup):
        return [item for child in error.exceptions for item in exceptions(child)]
    return [error]


def test_bounded_capture_stdin_streams_and_nonzero_exit() -> None:
    async def check() -> None:
        session = FakeSession()
        policy = SandboxPolicy(stdout_limit=10, stderr_limit=3, output_queue_chunks=1)
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            result = await instance.run(
                request(
                    "import sys; data=sys.stdin.buffer.read(); "
                    "sys.stdout.buffer.write(data + b'x'*200000); "
                    "sys.stderr.buffer.write(b'warning'); sys.exit(7)",
                    stdin=b"hello",
                )
            )
            assert result.exit_code == 7
            assert result.stdout == b"helloxxxxx"
            assert result.stderr == b"war"
            assert result.stdout_truncated
            assert result.stderr_truncated
            assert result.termination.resources_released
        assert session.terminations == 1

    asyncio.run(check())


def test_streaming_is_complete_when_consumed_and_captures_are_independently_bounded() -> None:
    async def check() -> None:
        session = FakeSession()
        policy = SandboxPolicy(stdout_limit=8, output_queue_chunks=1)
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            handle = await instance.start(
                request("import sys; sys.stdout.buffer.write(b'a'*150000)")
            )

            async def consume() -> bytes:
                return b"".join([chunk.data async for chunk in handle.output()])

            consumer = asyncio.create_task(consume())
            result = await handle.wait()
            assert len(await consumer) == 150000
            assert result.stdout == b"a" * 8
            assert result.stdout_truncated
            with pytest.raises(SandboxStateError):
                async for _ in handle.output():
                    pass

    asyncio.run(check())


def test_slow_stream_consumer_has_bounded_queue_and_can_cancel() -> None:
    async def check() -> None:
        session = FakeSession()
        policy = SandboxPolicy(output_queue_chunks=1, execution_timeout=10)
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            handle = await instance.start(
                request("import sys,time; sys.stdout.buffer.write(b'x'*1000000); time.sleep(20)")
            )
            await session.started.wait()
            await asyncio.sleep(0.1)
            assert handle._queue.qsize() <= 1
            report = await handle.cancel(force=True)
            assert report is not None
            assert report.resources_released
            assert session.force_event is not None
            assert session.force_event.is_set()

    asyncio.run(check())


def test_timeout_terminates_before_propagating() -> None:
    async def check() -> None:
        session = FakeSession()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxTimeoutError) as raised:
                await instance.run(request("import time; time.sleep(30)", timeout=0.1))
            assert raised.value.report is not None
            assert raised.value.report.resources_released
            assert session.terminations == 1
            assert all(process.returncode is not None for process in session.processes.values())

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["create", "start"])
def test_timeout_interrupts_inflight_backend_operation_before_cleanup(stage: str) -> None:
    async def check() -> None:
        session = FakeSession()
        gate = asyncio.Event()
        if stage == "create":
            session.create_gate = gate
        else:
            session.start_gate = gate
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create(policy=SandboxPolicy(execution_timeout=0.05)) as instance,
        ):
            handle = await instance.start(request("import time; time.sleep(30)"))
            await (session.registered if stage == "create" else session.launching).wait()
            try:
                done, _ = await asyncio.wait((handle._task,), timeout=1)
                assert done, "execution timeout did not interrupt pending startup"
            finally:
                gate.set()
            with pytest.raises(SandboxTimeoutError) as raised:
                await handle.wait()
            assert raised.value.report is not None
            assert raised.value.report.resources_released
            assert session.terminations == 1
            await asyncio.sleep(0)
            assert session.processes == {}

    asyncio.run(check())


def test_repeated_cancellation_interrupts_creation_but_waits_for_cleanup() -> None:
    async def check() -> None:
        session = FakeSession()
        session.create_gate = asyncio.Event()
        session.terminate_gate = asyncio.Event()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(request("print('never started')"))
            await session.registered.wait()
            cancel = asyncio.create_task(handle.cancel())
            try:
                async with asyncio.timeout(1):
                    await session.terminating.wait()
            except TimeoutError:
                session.create_gate.set()
                session.terminate_gate.set()
                raise
            cancel.cancel()
            await asyncio.sleep(0)
            cancel.cancel()
            assert not cancel.done()
            session.terminate_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await cancel
            assert session.terminations == 1
            assert session.processes == {}

    asyncio.run(check())


def test_immediate_cancel_closes_output_even_before_runner_starts() -> None:
    async def check() -> None:
        session = FakeSession()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(request("print('not started')"))
            await handle.cancel()
            async with asyncio.timeout(1):
                assert [chunk async for chunk in handle.output()] == []
            assert not session.registered.is_set()

    asyncio.run(check())


def test_concurrency_budget_and_stop_freeze_admissions() -> None:
    async def check() -> None:
        session = FakeSession()
        session.create_gate = asyncio.Event()
        async with Sandbox(backend=FakeBackend(session)) as sandbox:
            instance = await sandbox.create(policy=SandboxPolicy(max_concurrent_executions=1))
            await instance.start(request("print('one')"))
            await session.registered.wait()
            with pytest.raises(SandboxStateError):
                await instance.start(request("print('two')"))
            stop = asyncio.create_task(instance.stop())
            await asyncio.sleep(0)
            with pytest.raises(SandboxStateError):
                await instance.start(request("print('three')"))
            session.create_gate.set()
            assert len(await stop) == 1
            assert await instance.stop() == await instance.stop()

    asyncio.run(check())


def test_kill_escalates_an_in_progress_stop() -> None:
    async def check() -> None:
        session = FakeSession()
        session.terminate_gate = asyncio.Event()
        async with Sandbox(backend=FakeBackend(session)) as sandbox:
            instance = await sandbox.create()
            await instance.start(request("import time; time.sleep(30)"))
            await session.started.wait()
            stopping = asyncio.create_task(instance.stop())
            await session.terminating.wait()
            killing = asyncio.create_task(instance.kill())
            await asyncio.sleep(0)
            assert session.force_event is not None
            assert session.force_event.is_set()
            session.terminate_gate.set()
            assert await stopping == await killing
            assert session.terminations == 1

    asyncio.run(check())


def test_cancel_and_cleanup_errors_are_both_preserved_and_remain_failed() -> None:
    async def check() -> None:
        session = FakeSession()
        session.fail_termination = True
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        running = asyncio.create_task(instance.run(request("import time; time.sleep(30)")))
        await session.started.wait()
        running.cancel()
        with pytest.raises(BaseExceptionGroup) as raised:
            await running
        leaves = exceptions(raised.value)
        assert any(isinstance(error, asyncio.CancelledError) for error in leaves)
        assert any(isinstance(error, TerminationVerificationError) for error in leaves)
        assert all(process.returncode is not None for process in session.processes.values())
        with pytest.raises(BaseExceptionGroup):
            await instance.close()
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


def test_create_cannot_complete_after_owner_closes() -> None:
    async def check() -> None:
        session = FakeSession()
        backend = FakeBackend(session)
        backend.prepare_gate = asyncio.Event()
        sandbox = Sandbox(backend=backend)
        creating = asyncio.create_task(sandbox.create())
        await backend.preparing.wait()
        await sandbox.close()
        backend.prepare_gate.set()
        with pytest.raises(SandboxStateError):
            await creating

    asyncio.run(check())


def test_cancelled_unknown_creation_remains_a_failed_owner() -> None:
    async def check() -> None:
        session = FakeSession()
        session.fail_creation_unknown = True
        session.create_gate = asyncio.Event()
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        handle = await instance.start(request("print('not started')"))
        await session.registered.wait()
        cancelling = asyncio.create_task(handle.cancel())
        await asyncio.sleep(0)
        session.create_gate.set()
        # The requested cancellation is expected, so only the uncertain outcome surfaces.
        with pytest.raises(TerminationVerificationError, match="uncertain creation"):
            await cancelling
        with pytest.raises(BaseExceptionGroup):
            await instance.close()
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()

    asyncio.run(check())


def test_unimplemented_lifecycle_operations_fail_explicitly() -> None:
    async def check() -> None:
        async with (
            Sandbox(backend=FakeBackend(FakeSession())) as sandbox,
            await sandbox.create() as instance,
        ):
            with pytest.raises(SandboxCapabilityError, match="workspace=True"):
                await instance.snapshot("unused")

    asyncio.run(check())


async def _finish_late_cleanups(instance: SandboxInstance) -> None:
    async with asyncio.timeout(10):
        if pending := tuple(instance._late_cleanups):
            await asyncio.wait(pending)
    assert instance.pending_late_cleanups == 0


async def _ignore_cancellation(gate: asyncio.Event) -> None:
    while not gate.is_set():
        with contextlib.suppress(asyncio.CancelledError):
            await gate.wait()


class StallingSession(FakeSession):
    """Backend double whose chosen operation ignores cancellation until released."""

    def __init__(self, stage: str) -> None:
        super().__init__()
        self.stage = stage
        self.release = asyncio.Event()

    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        boundary = await super().create_boundary(request, sandbox_id, execution_id, register)
        if self.stage == "create":
            await _ignore_cancellation(self.release)
        return boundary

    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        if self.stage == "start":
            self.launching.set()
            await _ignore_cancellation(self.release)
        return await super().start_boundary(boundary, request)

    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        if self.stage == "terminate":
            self.terminating.set()
            await _ignore_cancellation(self.release)
        return await super().terminate(boundary, policy, force_requested)


def _deadline_policy(**overrides: Any) -> SandboxPolicy:
    termination = TerminationPolicy(total_timeout=0.2, cleanup_deadline=0.3)
    return SandboxPolicy(termination=termination, **overrides)


def _assert_deadline_error(error: BaseException) -> TerminationVerificationError:
    matches = [item for item in exceptions(error) if isinstance(item, TerminationVerificationError)]
    assert matches, error
    report = matches[0].report
    assert report is not None
    assert report.state == "unknown"
    assert not report.resources_released
    assert any("deadline" in note for note in report.notes)
    return matches[0]


@pytest.mark.parametrize("stage", ["create", "start", "terminate"])
def test_cancel_returns_within_hard_cleanup_deadline_and_keeps_ownership(stage: str) -> None:
    async def check() -> None:
        session = StallingSession(stage)
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create(policy=_deadline_policy())
        handle = await instance.start(request("import time; time.sleep(30)"))
        ready = {"create": session.registered, "start": session.launching}.get(
            stage, session.started
        )
        await ready.wait()
        loop = asyncio.get_running_loop()
        began = loop.time()
        with pytest.raises((TerminationVerificationError, BaseExceptionGroup)) as raised:
            await handle.cancel(force=True)
        assert loop.time() - began < 2
        _assert_deadline_error(raised.value)
        assert instance.pending_late_cleanups == 1
        began = loop.time()
        with pytest.raises(BaseExceptionGroup):
            await instance.close()
        with pytest.raises(BaseExceptionGroup):
            await sandbox.close()
        assert loop.time() - began < 2
        session.release.set()
        await _finish_late_cleanups(instance)
        assert session.terminations == 1
        assert all(process.returncode is not None for process in session.processes.values())

    asyncio.run(check())


def test_execution_timeout_with_stalled_termination_is_bounded() -> None:
    async def check() -> None:
        session = StallingSession("terminate")
        async with asyncio.timeout(5):
            sandbox = Sandbox(backend=FakeBackend(session))
            instance = await sandbox.create(policy=_deadline_policy(execution_timeout=0.1))
            with pytest.raises(BaseExceptionGroup) as raised:
                await instance.run(request("import time; time.sleep(30)"))
            leaves = exceptions(raised.value)
            timeout = next(item for item in leaves if isinstance(item, SandboxTimeoutError))
            error = _assert_deadline_error(raised.value)
            assert timeout.report is error.report
            with pytest.raises(BaseExceptionGroup):
                await sandbox.close()
        session.release.set()
        await _finish_late_cleanups(instance)

    asyncio.run(check())


def test_cleanup_deadline_is_validated() -> None:
    with pytest.raises(ValueError, match="cleanup_deadline must be finite and positive"):
        TerminationPolicy(cleanup_deadline=0)


class ReconcilingSession(FakeSession):
    """Backend double that reconciles an accepted allocation after cancellation."""

    def __init__(self) -> None:
        super().__init__()
        self.allocated = asyncio.Event()
        self.reconciled = False

    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        boundary = Boundary(execution_id, "fake", sandbox_id, execution_id)
        self.allocated.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            for _ in range(5):
                await asyncio.sleep(0.02)
            register(boundary)
            self.reconciled = True
            raise
        raise AssertionError("unreachable")


def test_single_stop_request_does_not_interrupt_backend_reconciliation() -> None:
    async def check() -> None:
        session = ReconcilingSession()
        async with (
            Sandbox(backend=FakeBackend(session)) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(request("print(1)"))
            await session.allocated.wait()
            report = await handle.cancel(force=True)
        assert session.reconciled
        assert report is not None
        assert report.resources_released
        assert session.terminations == 1

    asyncio.run(check())
