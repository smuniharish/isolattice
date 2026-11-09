"""Runtime artifact, workspace and session-ownership contracts with test doubles.

These tests prove manifest integrity, archive validation and lifecycle wiring;
they do not prove any backend's isolation or snapshot fidelity.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isolattice import (
    ArtifactKind,
    Capability,
    EventKind,
    EventSink,
    ExecutionRequest,
    LocalArtifactStore,
    ResourceBudget,
    Sandbox,
    SandboxArtifactError,
    SandboxCapabilityError,
    SandboxEvent,
    SandboxPolicy,
    SandboxStateError,
    _artifacts,
)
from isolattice.backend import Backend, Boundary
from tests.unit.test_async_runtime import FakeBackend, FakeSession
from tests.unit.test_async_runtime import request as make_request

if TYPE_CHECKING:
    from collections.abc import Callable


class WorkspaceSession(FakeSession):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.closed = 0
        self.fail_close = False
        self.fail_import = False
        self.checkpoints: list[Path] = []
        self.restored: list[bytes] = []
        self.restored_event = asyncio.Event()

    @property
    def workspace_path(self) -> str | None:
        return "/workspace"

    async def close(self) -> None:
        self.closed += 1
        if self.fail_close:
            raise RuntimeError("injected session close failure")

    async def export_workspace(self, destination: Path) -> None:
        def write() -> None:
            with tarfile.open(destination, "w") as bundle:
                for path in sorted(self.root.rglob("*")):
                    bundle.add(path, path.relative_to(self.root).as_posix(), recursive=False)

        await asyncio.to_thread(write)

    async def import_workspace(self, source: Path) -> None:
        if self.fail_import:
            raise RuntimeError("injected import failure")

        def extract() -> None:
            with tarfile.open(source, "r:") as bundle:
                bundle.extractall(self.root, filter="data")

        await asyncio.to_thread(extract)

    async def checkpoint(self, boundary: Boundary, destination: Path) -> None:
        await asyncio.to_thread(
            destination.write_bytes, b"checkpoint:" + boundary.boundary_id.encode()
        )
        self.checkpoints.append(destination)

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        if boundary.restore_source is not None:
            request = make_request("import time; time.sleep(30)")
        return await super().start_boundary(boundary, request)

    async def restore_boundary(
        self,
        source: Path,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        self.restored.append(await asyncio.to_thread(source.read_bytes))
        self.restored_event.set()
        boundary = Boundary(
            execution_id, "restored", sandbox_id, execution_id, restore_source=source
        )
        register(boundary)
        return boundary


def workspace_policy() -> SandboxPolicy:
    return SandboxPolicy(workspace=True)


class RecordingSink(EventSink):
    def __init__(self) -> None:
        self.events: list[SandboxEvent] = []

    def emit(self, event: SandboxEvent) -> None:
        self.events.append(event)


def owner(
    backend: Backend,
    store: LocalArtifactStore | None,
    tmp_path: Path,
    sink: EventSink | None = None,
) -> Sandbox:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    return Sandbox(
        backend=backend, artifact_store=store, event_sink=sink, staging_directory=staging
    )


def store(tmp_path: Path) -> LocalArtifactStore:
    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    return LocalArtifactStore(root)


def test_policy_validates_new_budgets_and_required_settings() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ResourceBudget()
    with pytest.raises(ValueError, match="memory_bytes must be positive"):
        ResourceBudget(memory_bytes=0)
    with pytest.raises(TypeError):
        SandboxPolicy(instance_budget=object())  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(ValueError, match="scratch_bytes must be positive"):
        SandboxPolicy(scratch_bytes=0)
    for capability in (
        Capability.INSTANCE_LIMITS,
        Capability.DISK_LIMIT,
        Capability.WORKSPACE,
        Capability.SNAPSHOTS,
    ):
        with pytest.raises(ValueError, match=capability.value):
            SandboxPolicy(required_capabilities=(capability,))
    SandboxPolicy(
        instance_budget=ResourceBudget(process_limit=4),
        scratch_bytes=1024,
        workspace=True,
        required_capabilities=(
            Capability.INSTANCE_LIMITS,
            Capability.DISK_LIMIT,
            Capability.WORKSPACE,
            Capability.SNAPSHOTS,
        ),
    )


def test_snapshot_round_trip_verifies_and_restores(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    (source_root / "nested").mkdir(parents=True)
    (source_root / "nested" / "data.txt").write_text("state")
    target_root = tmp_path / "target"
    target_root.mkdir()
    artifacts = store(tmp_path)
    sink = RecordingSink()

    async def check() -> None:
        source = WorkspaceSession(source_root)
        async with (
            owner(FakeBackend(source), artifacts, tmp_path, sink) as sandbox,
            await sandbox.create(policy=workspace_policy()) as instance,
        ):
            assert instance.workspace_path == "/workspace"
            manifest = await instance.snapshot("runs/base")
            with pytest.raises(SandboxArtifactError, match="already exists"):
                await instance.snapshot("runs/base")
        assert source.closed == 1
        assert manifest.kind is ArtifactKind.WORKSPACE_SNAPSHOT
        assert manifest.backend == "fake"
        assert manifest.archive == "runs/base"
        assert manifest.size == (artifacts.root / "runs" / "base").stat().st_size
        assert sorted(path.name for path in (artifacts.root / "runs").iterdir()) == [
            "base",
            "base.manifest.json",
        ]
        assert list((tmp_path / "staging").iterdir()) == []
        target = WorkspaceSession(target_root)
        async with (
            owner(FakeBackend(target), artifacts, tmp_path, sink) as sandbox,
            await sandbox.create(policy=workspace_policy(), workspace_snapshot="runs/base"),
        ):
            assert (target_root / "nested" / "data.txt").read_text() == "state"
        assert list((tmp_path / "staging").iterdir()) == []
        kinds = [event.kind for event in sink.events]
        assert EventKind.ARTIFACT_PUBLISHED in kinds
        assert EventKind.ARTIFACT_RESTORED in kinds
        published = next(e for e in sink.events if e.kind is EventKind.ARTIFACT_PUBLISHED)
        assert published.detail["key"] == "runs/base"
        assert published.detail["artifact_kind"] == "workspace-snapshot"

    asyncio.run(check())


def test_snapshot_refuses_active_executions_and_missing_workspace_policy(tmp_path: Path) -> None:
    artifacts = store(tmp_path)

    async def check() -> None:
        session = WorkspaceSession(tmp_path)
        async with owner(FakeBackend(session), artifacts, tmp_path) as sandbox:
            async with await sandbox.create(policy=workspace_policy()) as instance:
                handle = await instance.start(make_request("import time; time.sleep(30)"))
                await session.started.wait()
                with pytest.raises(SandboxStateError, match="no active"):
                    await instance.snapshot("busy")
                await handle.cancel(force=True)
            with pytest.raises(SandboxCapabilityError, match="workspace=True"):
                await sandbox.create(workspace_snapshot="missing")
        assert not await artifacts.exists("busy")
        assert list((tmp_path / "staging").iterdir()) == []

    asyncio.run(check())


def test_artifact_operations_fail_closed_without_a_store(tmp_path: Path) -> None:
    async def check() -> None:
        session = WorkspaceSession(tmp_path)
        async with Sandbox(backend=FakeBackend(session)) as sandbox:
            with pytest.raises(SandboxCapabilityError, match="artifact_store"):
                await sandbox.create(policy=workspace_policy(), workspace_snapshot="base")
            async with await sandbox.create(policy=workspace_policy()) as instance:
                with pytest.raises(SandboxCapabilityError, match="artifact_store"):
                    await instance.snapshot("base")
        assert session.closed == 1

    asyncio.run(check())


@pytest.mark.parametrize("key", ["", "../escape", "/absolute", "a//b", "x.manifest.json"])
def test_invalid_artifact_keys_are_rejected_before_preparing(key: str, tmp_path: Path) -> None:
    async def check() -> None:
        backend = FakeBackend(WorkspaceSession(tmp_path))
        async with owner(backend, store(tmp_path), tmp_path) as sandbox:
            with pytest.raises(ValueError, match="key"):
                await sandbox.create(policy=workspace_policy(), workspace_snapshot=key)
        assert not backend.preparing.is_set()

    asyncio.run(check())


def test_restore_rejects_tampered_or_mismatched_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_text("value")
    artifacts = store(tmp_path)
    archive = artifacts.root / "snapshot"
    manifest_file = artifacts.root / "snapshot.manifest.json"

    async def check() -> None:
        async with (
            owner(FakeBackend(WorkspaceSession(root)), artifacts, tmp_path) as sandbox,
            await sandbox.create(policy=workspace_policy()) as instance,
        ):
            await instance.snapshot("snapshot")

        async def restore(session: WorkspaceSession) -> None:
            async with owner(FakeBackend(session), artifacts, tmp_path) as sandbox:
                await sandbox.create(policy=workspace_policy(), workspace_snapshot="snapshot")

        original = manifest_file.read_text()
        session = WorkspaceSession(tmp_path / "unused")
        record = json.loads(original)
        record["backend"] = "other"
        manifest_file.write_text(json.dumps(record))
        with pytest.raises(SandboxArtifactError, match="backend"):
            await restore(session)
        assert session.closed == 1
        record = json.loads(original)
        record["kind"] = ArtifactKind.EXECUTION_CHECKPOINT.value
        manifest_file.write_text(json.dumps(record))
        with pytest.raises(SandboxArtifactError, match="execution-checkpoint"):
            await restore(WorkspaceSession(tmp_path / "unused"))
        record = json.loads(original)
        record["archive"] = "other"
        manifest_file.write_text(json.dumps(record))
        with pytest.raises(SandboxArtifactError, match="different key"):
            await restore(WorkspaceSession(tmp_path / "unused"))
        manifest_file.write_text('{"kind": "workspace-snapshot"}')
        with pytest.raises(SandboxArtifactError, match="malformed"):
            await restore(WorkspaceSession(tmp_path / "unused"))
        manifest_file.write_text(original)
        with archive.open("r+b") as file:
            file.seek(600)
            file.write(b"X")
        with pytest.raises(SandboxArtifactError, match="integrity"):
            await restore(WorkspaceSession(tmp_path / "unused"))
        manifest_file.unlink()
        with pytest.raises(SandboxArtifactError, match="does not exist"):
            await restore(WorkspaceSession(tmp_path / "unused"))
        assert list((tmp_path / "staging").iterdir()) == []

    asyncio.run(check())


def _tar_with(member: tarfile.TarInfo, data: bytes = b"") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        member.size = len(data) if member.isreg() else 0
        bundle.addfile(member, io.BytesIO(data) if member.isreg() else None)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "member",
    [
        tarfile.TarInfo("/absolute"),
        tarfile.TarInfo("../escape"),
        tarfile.TarInfo("a/../../escape"),
    ],
)
def test_workspace_validation_rejects_escaping_names(
    member: tarfile.TarInfo, tmp_path: Path
) -> None:
    archive = tmp_path / "unsafe.tar"
    archive.write_bytes(_tar_with(member, b"x"))
    with pytest.raises(SandboxArtifactError, match="unsafe"):
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=10, max_bytes=10))


@pytest.mark.parametrize(
    ("kind", "target", "message"),
    [
        (tarfile.SYMTYPE, "/etc/passwd", "unsafe"),
        (tarfile.SYMTYPE, "../../outside", "unsafe"),
        (tarfile.LNKTYPE, "../outside", "unsafe"),
        (tarfile.CHRTYPE, "", "not a regular"),
        (tarfile.FIFOTYPE, "", "not a regular"),
    ],
)
def test_workspace_validation_rejects_links_and_special_files(
    kind: bytes, target: str, message: str, tmp_path: Path
) -> None:
    member = tarfile.TarInfo("entry")
    member.type = kind
    member.linkname = target
    archive = tmp_path / "unsafe.tar"
    archive.write_bytes(_tar_with(member))
    with pytest.raises(SandboxArtifactError, match=message):
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=10, max_bytes=10))


def test_workspace_validation_bounds_members_size_and_format(tmp_path: Path) -> None:
    archive = tmp_path / "big.tar"
    archive.write_bytes(_tar_with(tarfile.TarInfo("file"), b"0123456789"))
    with pytest.raises(SandboxArtifactError, match="size limit"):
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=10, max_bytes=9))
    with pytest.raises(SandboxArtifactError, match="too many"):
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=0, max_bytes=100))
    archive.write_bytes(b"not a tar archive" * 64)
    with pytest.raises(SandboxArtifactError, match="valid uncompressed tar"):
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=10, max_bytes=100))


_SEGMENT = st.sampled_from(["a", "b", "..", ".", "", "c d", "é"])


@settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(parts=st.lists(_SEGMENT, min_size=1, max_size=5), absolute=st.booleans())
def test_workspace_validation_never_accepts_a_name_outside_the_root(
    parts: list[str], absolute: bool, tmp_path: Path
) -> None:
    name = ("/" if absolute else "") + "/".join(parts)
    if not name.strip("/"):
        return
    archive = tmp_path / "generated.tar"
    archive.write_bytes(_tar_with(tarfile.TarInfo(name), b"x"))
    try:
        asyncio.run(_artifacts.validate_workspace_tar(archive, max_members=10, max_bytes=10))
    except SandboxArtifactError:
        return
    root = Path("/isolattice-workspace")
    resolved = (root / name.lstrip("/")).resolve() if not absolute else None
    assert not absolute
    assert resolved is not None
    assert resolved.is_relative_to(root.resolve())


def test_session_close_runs_once_and_failures_surface(tmp_path: Path) -> None:
    async def check() -> None:
        session = WorkspaceSession(tmp_path)
        sandbox = Sandbox(backend=FakeBackend(session))
        instance = await sandbox.create()
        await instance.stop()
        await instance.stop()
        await sandbox.close()
        assert session.closed == 1

        failing = WorkspaceSession(tmp_path)
        failing.fail_close = True
        other = Sandbox(backend=FakeBackend(failing))
        instance = await other.create()
        with pytest.raises(BaseExceptionGroup) as raised:
            await instance.stop()
        assert any("injected session close" in str(item) for item in raised.value.exceptions)
        with pytest.raises(BaseExceptionGroup):
            await other.close()
        assert failing.closed == 1

    asyncio.run(check())


def test_failed_workspace_import_closes_the_prepared_session(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    artifacts = store(tmp_path)

    async def check() -> None:
        async with (
            owner(FakeBackend(WorkspaceSession(root)), artifacts, tmp_path) as sandbox,
            await sandbox.create(policy=workspace_policy()) as instance,
        ):
            await instance.snapshot("snapshot")
        session = WorkspaceSession(tmp_path / "unused")
        session.fail_import = True
        async with owner(FakeBackend(session), artifacts, tmp_path) as sandbox:
            with pytest.raises(RuntimeError, match="injected import"):
                await sandbox.create(policy=workspace_policy(), workspace_snapshot="snapshot")
        assert session.closed == 1
        assert list((tmp_path / "staging").iterdir()) == []

    asyncio.run(check())


def test_checkpoint_and_restore_wiring(tmp_path: Path) -> None:
    artifacts = store(tmp_path)

    async def check() -> None:
        session = WorkspaceSession(tmp_path)
        async with (
            owner(FakeBackend(session), artifacts, tmp_path) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(make_request("import time; time.sleep(30)"))
            with pytest.raises(SandboxStateError, match="running"):
                await handle.checkpoint("early")
            await session.started.wait()
            manifest = await handle.checkpoint("run")
            assert manifest.kind is ArtifactKind.EXECUTION_CHECKPOINT
            assert manifest.format == "backend-checkpoint"
            await handle.cancel(force=True)
            with pytest.raises(SandboxStateError, match="running"):
                await handle.checkpoint("late")
            with pytest.raises(SandboxArtifactError, match="does not exist"):
                await instance.restore("missing")
            restored = await instance.restore("run")
            await session.restored_event.wait()
            assert session.restored == [b"checkpoint:" + handle.execution_id.encode()]
            await restored.cancel(force=True)
        assert list((tmp_path / "staging").iterdir()) == []

    asyncio.run(check())


def test_unsupported_checkpoint_leaves_no_partial_files(tmp_path: Path) -> None:
    artifacts = store(tmp_path)

    async def check() -> None:
        session = FakeSession()
        async with (
            owner(FakeBackend(session), artifacts, tmp_path) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(make_request("import time; time.sleep(30)"))
            await session.started.wait()
            with pytest.raises(SandboxCapabilityError, match="checkpoints"):
                await handle.checkpoint("run")
            await handle.cancel(force=True)
        assert list(artifacts.root.iterdir()) == []
        assert list((tmp_path / "staging").iterdir()) == []

    asyncio.run(check())


def test_restore_argv_is_internal_and_valid() -> None:
    assert (
        ExecutionRequest(argv=("isolattice-restored-checkpoint",)).argv[0].startswith("isolattice")
    )
