"""Contract tests for SQLiteArtifactStore. Run with pytest."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING

import pytest
from store_template import SQLiteArtifactStore

from isolattice import ArtifactKind, ArtifactManifest, SandboxArtifactError
from isolattice.testing import check_artifact_store

if TYPE_CHECKING:
    from pathlib import Path

PAYLOAD = b"payload"


def manifest_for(key: str) -> ArtifactManifest:
    return ArtifactManifest(
        kind=ArtifactKind.WORKSPACE_SNAPSHOT,
        backend="test",
        format="tar",
        archive=key,
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        size=len(PAYLOAD),
        created_at="2000-01-01T00:00:00+00:00",
    )


@pytest.fixture
def store(tmp_path: Path) -> SQLiteArtifactStore:
    return SQLiteArtifactStore(tmp_path / "artifacts.sqlite3")


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "archive"
    path.write_bytes(PAYLOAD)
    return path


def test_the_store_passes_the_isolattice_conformance_check(
    store: SQLiteArtifactStore, tmp_path: Path
) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()

    asyncio.run(check_artifact_store(store, workdir))


def test_concurrent_publishers_of_one_key_cannot_both_succeed(
    store: SQLiteArtifactStore, archive: Path
) -> None:
    manifest = manifest_for("runs/1/workspace.tar")

    async def race() -> list[BaseException | None]:
        return await asyncio.gather(
            *(store.publish(manifest.archive, archive, manifest) for _ in range(4)),
            return_exceptions=True,
        )

    outcomes = asyncio.run(race())

    assert outcomes.count(None) == 1
    assert all(isinstance(outcome, SandboxArtifactError) for outcome in outcomes if outcome)


def test_fetch_never_replaces_an_existing_destination(
    store: SQLiteArtifactStore, archive: Path, tmp_path: Path
) -> None:
    manifest = manifest_for("runs/2/workspace.tar")
    asyncio.run(store.publish(manifest.archive, archive, manifest))
    existing = tmp_path / "existing"
    existing.write_bytes(b"keep")

    with pytest.raises(SandboxArtifactError):
        asyncio.run(store.fetch(manifest.archive, existing, max_bytes=1024))
    assert existing.read_bytes() == b"keep"


def test_a_corrupt_manifest_is_refused(
    store: SQLiteArtifactStore, archive: Path, tmp_path: Path
) -> None:
    manifest = manifest_for("runs/3/workspace.tar")
    asyncio.run(store.publish(manifest.archive, archive, manifest))
    connection = store.connect()
    with connection:
        connection.execute("UPDATE artifacts SET manifest = ?", (b"{}",))
    connection.close()

    with pytest.raises(SandboxArtifactError, match="bad manifest"):
        asyncio.run(store.fetch(manifest.archive, tmp_path / "out", max_bytes=1024))
