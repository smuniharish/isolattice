"""Fail-closed defaults of BackendSession and archive verification against manifests.

These tests prove that optional session features refuse rather than silently succeed
and that a fetched archive is checked against every manifest field; they do not prove
any backend's isolation.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Any

import pytest

from isolattice import ArtifactKind, SandboxArtifactError, SandboxCapabilityError, _artifacts
from isolattice.backend import Boundary
from tests.unit.test_async_runtime import FakeSession

if TYPE_CHECKING:
    from pathlib import Path


def boundary() -> Boundary:
    return Boundary(boundary_id="b-1", name="b-1", sandbox_id="s-1", execution_id="e-1")


def test_optional_session_features_fail_closed(tmp_path: Path) -> None:
    session = FakeSession()
    target = tmp_path / "artifact.tar"

    async def check() -> None:
        assert session.workspace_path is None
        await session.close()
        refusals = (
            ("workspace to snapshot", session.export_workspace(target)),
            ("workspace to restore", session.import_workspace(target)),
            ("checkpoints", session.checkpoint(boundary(), target)),
            ("checkpoints", session.restore_boundary(target, "s-1", "e-1", lambda _: None)),
            ("container snapshots", session.snapshot_boundary(boundary(), target)),
            ("container snapshots", session.import_root(target)),
        )
        for message, operation in refusals:
            with pytest.raises(SandboxCapabilityError, match=message):
                await operation
        assert not target.exists()

    asyncio.run(check())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"archive": "other"}, "names a different key"),
        ({"kind": ArtifactKind.EXECUTION_CHECKPOINT}, "is a execution-checkpoint, not a"),
        ({"backend": "other"}, "produced by backend 'other'"),
        ({"format": "zip"}, "format 'zip'"),
        ({"size": 1_000}, "exceeds the configured restore size limit"),
        ({"sha256": "0" * 64}, "does not match its integrity manifest"),
    ],
)
def test_verify_rejects_every_manifest_mismatch(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    archive = tmp_path / "workspace.tar"
    archive.write_bytes(b"workspace bytes")

    async def check() -> None:
        manifest = await _artifacts.seal(
            ArtifactKind.WORKSPACE_SNAPSHOT, "fake", "tar", archive, "key"
        )
        await _artifacts.verify(
            archive,
            manifest,
            key="key",
            kind=ArtifactKind.WORKSPACE_SNAPSHOT,
            backend="fake",
            archive_format="tar",
            max_bytes=100,
        )
        tampered = dataclasses.replace(manifest, **change)
        with pytest.raises(SandboxArtifactError, match=message):
            await _artifacts.verify(
                archive,
                tampered,
                key="key",
                kind=ArtifactKind.WORKSPACE_SNAPSHOT,
                backend="fake",
                archive_format="tar",
                max_bytes=100,
            )

    asyncio.run(check())


def test_verify_reports_an_unreadable_archive(tmp_path: Path) -> None:
    archive = tmp_path / "workspace.tar"
    archive.write_bytes(b"workspace bytes")

    async def check() -> None:
        manifest = await _artifacts.seal(
            ArtifactKind.WORKSPACE_SNAPSHOT, "fake", "tar", archive, "key"
        )
        archive.unlink()
        with pytest.raises(SandboxArtifactError, match="unreadable") as raised:
            await _artifacts.verify(
                archive,
                manifest,
                key="key",
                kind=ArtifactKind.WORKSPACE_SNAPSHOT,
                backend="fake",
                archive_format="tar",
                max_bytes=100,
            )
        assert isinstance(raised.value.__cause__, OSError)

    asyncio.run(check())


def test_staging_directory_is_removed_and_removal_is_idempotent(tmp_path: Path) -> None:
    async def check() -> None:
        directory = await _artifacts.stage(tmp_path)
        (directory / "memory.img").write_bytes(b"workload memory")
        await _artifacts.unstage(directory)
        assert not directory.exists()
        await _artifacts.unstage(directory)

    asyncio.run(check())


def _flaky_rmtree(failures: int) -> tuple[list[int], Any]:
    calls = [0]
    real = _artifacts.shutil.rmtree

    def rmtree(path: Path) -> None:
        calls[0] += 1
        if calls[0] <= failures:
            raise PermissionError("delete pending")
        real(path)

    return calls, rmtree


def test_windows_staging_removal_retries_delete_pending_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "staging"
    directory.mkdir()
    calls, rmtree = _flaky_rmtree(2)
    monkeypatch.setattr(_artifacts.sys, "platform", "win32")
    monkeypatch.setattr(_artifacts.shutil, "rmtree", rmtree)
    monkeypatch.setattr(_artifacts.time, "sleep", lambda _: None)
    asyncio.run(_artifacts.unstage(directory))
    assert calls == [3]
    assert not directory.exists()


def test_windows_staging_removal_surfaces_persistent_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "staging"
    directory.mkdir()
    calls, rmtree = _flaky_rmtree(100)
    monkeypatch.setattr(_artifacts.sys, "platform", "win32")
    monkeypatch.setattr(_artifacts.shutil, "rmtree", rmtree)
    monkeypatch.setattr(_artifacts.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError, match="delete pending"):
        asyncio.run(_artifacts.unstage(directory))
    assert calls == [len(_artifacts._REMOVE_RETRY_DELAYS) + 1]
    assert directory.exists()


def test_staging_removal_does_not_retry_outside_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "staging"
    directory.mkdir()
    calls, rmtree = _flaky_rmtree(1)
    monkeypatch.setattr(_artifacts.sys, "platform", "linux")
    monkeypatch.setattr(_artifacts.shutil, "rmtree", rmtree)
    with pytest.raises(PermissionError):
        asyncio.run(_artifacts.unstage(directory))
    assert calls == [1]
