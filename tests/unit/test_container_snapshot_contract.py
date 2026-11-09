"""Container-snapshot runtime and archive contracts with test doubles.

These tests prove manifest wiring, the running-state guard, fail-closed defaults
and rootfs archive validation. They do not prove any engine's snapshot fidelity;
live Podman tests do.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from isolattice import (
    ArtifactKind,
    Capability,
    ExecutionRequest,
    LocalArtifactStore,
    Sandbox,
    SandboxArtifactError,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxStateError,
    _artifacts,
)
from tests.unit.test_async_runtime import FakeBackend, FakeSession

if TYPE_CHECKING:
    from isolattice.backend import Boundary

# A Windows venv interpreter is a redirector whose real child would outlive the kill.
_PYTHON = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
_SLEEPER = ExecutionRequest(argv=(_PYTHON, "-c", "import time; time.sleep(30)"))


def _tar(*members: tuple[tarfile.TarInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        for member, data in members:
            member.size = len(data) if member.isreg() else 0
            bundle.addfile(member, io.BytesIO(data) if member.isreg() else None)
    return buffer.getvalue()


def _member(name: str, kind: bytes = tarfile.REGTYPE, link: str = "") -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = link
    return member


def _root_tar() -> bytes:
    return _tar(
        (_member("etc", tarfile.DIRTYPE), b""),
        (_member("etc/os-release"), b"ID=test\n"),
        (_member("bin/sh", tarfile.SYMTYPE, "/bin/busybox"), b""),
        (_member("etc/alias", tarfile.LNKTYPE, "etc/os-release"), b""),
        (_member("./var/tmp/state", tarfile.DIRTYPE), b""),
    )


def _store(tmp_path: Path) -> LocalArtifactStore:
    root = tmp_path / "store"
    root.mkdir(exist_ok=True)
    return LocalArtifactStore(root)


def _sandbox(session: FakeSession, store: LocalArtifactStore | None, tmp_path: Path) -> Sandbox:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    return Sandbox(backend=FakeBackend(session), artifact_store=store, staging_directory=staging)


def _validate(archive: Path, *, members: int = 10, size: int = 1024) -> None:
    asyncio.run(_artifacts.validate_rootfs_tar(archive, max_members=members, max_bytes=size))


class SnapshotSession(FakeSession):
    def __init__(self, payload: bytes | None = None) -> None:
        super().__init__()
        self.payload = _root_tar() if payload is None else payload
        self.snapshots: list[str] = []
        self.imported: list[bytes] = []
        self.closed = 0

    async def snapshot_boundary(self, boundary: Boundary, destination: Path) -> None:
        self.snapshots.append(boundary.boundary_id)
        await asyncio.to_thread(destination.write_bytes, self.payload)

    async def import_root(self, source: Path) -> None:
        self.imported.append(await asyncio.to_thread(source.read_bytes))

    async def close(self) -> None:
        self.closed += 1


def test_rootfs_validation_accepts_engine_shaped_roots(tmp_path: Path) -> None:
    archive = tmp_path / "root.tar"
    archive.write_bytes(_root_tar())
    _validate(archive)


@pytest.mark.parametrize(
    ("member", "message"),
    [
        (_member("dev/null", tarfile.CHRTYPE), "special"),
        (_member("dev/sda", tarfile.BLKTYPE), "special"),
        (_member("run/pipe", tarfile.FIFOTYPE), "special"),
        (_member("/etc/passwd"), "absolute or traversing"),
        (_member("../escape"), "absolute or traversing"),
        (_member("a/../../escape"), "absolute or traversing"),
        (_member("a\\b"), "absolute or traversing"),
        (_member("C:/windows"), "absolute or traversing"),
        (_member("link", tarfile.LNKTYPE, "missing"), "names no earlier member"),
        (_member("link", tarfile.LNKTYPE, "/etc/passwd"), "names no earlier member"),
        (_member("link", tarfile.LNKTYPE, "../outside"), "names no earlier member"),
    ],
)
def test_rootfs_validation_rejects_unsafe_members(
    member: tarfile.TarInfo, message: str, tmp_path: Path
) -> None:
    archive = tmp_path / "unsafe.tar"
    archive.write_bytes(_tar((member, b"x" if member.isreg() else b"")))
    with pytest.raises(SandboxArtifactError, match=message):
        _validate(archive)


def test_rootfs_validation_rejects_forward_hard_links(tmp_path: Path) -> None:
    archive = tmp_path / "forward.tar"
    archive.write_bytes(
        _tar(
            (_member("early", tarfile.LNKTYPE, "late"), b""),
            (_member("late"), b"data"),
        )
    )
    with pytest.raises(SandboxArtifactError, match="names no earlier member"):
        _validate(archive)


def test_rootfs_validation_bounds_members_size_and_format(tmp_path: Path) -> None:
    archive = tmp_path / "root.tar"
    archive.write_bytes(_root_tar())
    with pytest.raises(SandboxArtifactError, match="too many members"):
        _validate(archive, members=4)
    with pytest.raises(SandboxArtifactError, match="size limit"):
        _validate(archive, size=7)
    archive.write_bytes(b"not a tar archive" * 64)
    with pytest.raises(SandboxArtifactError, match="valid uncompressed tar"):
        _validate(archive)


_SEGMENT = st.sampled_from(["a", "b", "..", ".", "", "c d", "é", "x\\y", "D:"])


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(parts=st.lists(_SEGMENT, min_size=1, max_size=5), absolute=st.booleans())
def test_rootfs_validation_only_accepts_names_inside_the_root(
    parts: list[str], absolute: bool, tmp_path: Path
) -> None:
    name = ("/" if absolute else "") + "/".join(parts)
    if not name.strip("/."):
        return
    archive = tmp_path / "generated.tar"
    archive.write_bytes(_tar((_member(name), b"x")))
    try:
        _validate(archive)
    except SandboxArtifactError:
        return
    assert not absolute
    assert ".." not in parts
    assert "\\" not in name
    assert not name.startswith("D:")


def test_policy_accepts_snapshots_for_a_writable_root() -> None:
    with pytest.raises(ValueError, match="snapshots"):
        SandboxPolicy(required_capabilities=(Capability.SNAPSHOTS,))
    SandboxPolicy(read_only_root=False, required_capabilities=(Capability.SNAPSHOTS,))


def test_snapshot_seals_and_restore_imports_a_verified_root(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def check() -> None:
        source = SnapshotSession()
        async with (
            _sandbox(source, store, tmp_path) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(_SLEEPER)
            await source.started.wait()
            manifest = await handle.snapshot_container("roots/base")
            await handle.cancel()
        assert manifest.kind is ArtifactKind.CONTAINER_SNAPSHOT
        assert manifest.format == "rootfs-tar"
        assert manifest.size == (store.root / "roots" / "base").stat().st_size
        assert len(source.snapshots) == 1
        target = SnapshotSession()
        async with (
            _sandbox(target, store, tmp_path) as sandbox,
            await sandbox.create(container_snapshot="roots/base"),
        ):
            assert target.imported == [_root_tar()]

    asyncio.run(check())
    assert list((tmp_path / "staging").iterdir()) == []


def test_snapshot_rejects_idle_executions_and_unsafe_engine_output(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def check() -> None:
        session = SnapshotSession(_tar((_member("dev/null", tarfile.CHRTYPE), b"")))
        async with (
            _sandbox(session, store, tmp_path) as sandbox,
            await sandbox.create() as instance,
        ):
            finished = await instance.start(ExecutionRequest(argv=(_PYTHON, "-c", "pass")))
            async for _ in finished.output():
                pass
            await finished.wait()
            with pytest.raises(SandboxStateError, match="running"):
                await finished.snapshot_container("late")
            session.started.clear()
            handle = await instance.start(_SLEEPER)
            await session.started.wait()
            with pytest.raises(SandboxArtifactError, match="special"):
                await handle.snapshot_container("device")
            await handle.cancel()
        assert session.snapshots
        assert len(session.snapshots) == 1

    asyncio.run(check())
    assert list(store.root.iterdir()) == []
    assert list((tmp_path / "staging").iterdir()) == []


def test_default_sessions_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def check() -> None:
        source = SnapshotSession()
        async with (
            _sandbox(source, store, tmp_path) as sandbox,
            await sandbox.create() as instance,
        ):
            handle = await instance.start(_SLEEPER)
            await source.started.wait()
            await handle.snapshot_container("root")
            await handle.cancel()
        plain = FakeSession()
        async with _sandbox(plain, store, tmp_path) as sandbox:
            with pytest.raises(SandboxCapabilityError, match="container snapshots"):
                await sandbox.create(container_snapshot="root")
            async with await sandbox.create() as instance:
                handle = await instance.start(_SLEEPER)
                await plain.started.wait()
                with pytest.raises(SandboxCapabilityError, match="container snapshots"):
                    await handle.snapshot_container("unsupported")
                await handle.cancel()
        async with _sandbox(FakeSession(), None, tmp_path) as sandbox:
            with pytest.raises(SandboxCapabilityError, match="artifact_store"):
                await sandbox.create(container_snapshot="root")

    asyncio.run(check())
    assert not (store.root / "unsupported").exists()


def test_restore_rejects_tampered_or_mismatched_roots(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def check() -> None:
        source = SnapshotSession()
        async with _sandbox(source, store, tmp_path) as sandbox:
            async with await sandbox.create() as instance:
                handle = await instance.start(_SLEEPER)
                await source.started.wait()
                await handle.snapshot_container("root")
                await handle.cancel()
            with pytest.raises(SandboxArtifactError, match="workspace-snapshot"):
                await sandbox.create(
                    policy=SandboxPolicy(workspace=True), workspace_snapshot="root"
                )
        with (store.root / "root").open("r+b") as file:
            file.seek(600)
            file.write(b"X")
        target = SnapshotSession()
        async with _sandbox(target, store, tmp_path) as sandbox:
            with pytest.raises(SandboxArtifactError, match="integrity"):
                await sandbox.create(container_snapshot="root")
        assert target.imported == []
        assert target.closed == 1

    asyncio.run(check())
