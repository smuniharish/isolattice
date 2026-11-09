"""Conformance checks for user-supplied extension implementations.

The checks are plain coroutines with no test-framework dependency, so they run
under pytest, unittest or a deployment health check.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

from .errors import SandboxArtifactError
from .models import ArtifactKind, ArtifactManifest
from .storage import ArtifactStore

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from pathlib import Path

__all__ = ["ConformanceError", "check_artifact_store"]


class ConformanceError(AssertionError):
    """An extension implementation violated its documented contract."""


async def _expect_refusal(operation: Awaitable[object], message: str) -> None:
    try:
        await operation
    except SandboxArtifactError:
        return
    raise ConformanceError(message)


async def check_artifact_store(
    store: ArtifactStore, workdir: Path, *, key_prefix: str = "isolattice-conformance"
) -> None:
    """Exercise ``store`` with one disposable artifact and verify its contract.

    The check publishes, re-publishes, fetches, bounds and deletes one small
    artifact under ``<key_prefix>/<random>``. It removes the artifact afterwards.

    Args:
        store: The implementation under test.
        workdir: An existing, empty, writable local directory for archives.
        key_prefix: The key prefix for the disposable artifact.

    Raises:
        ConformanceError: If the store violates its contract.
    """
    if not isinstance(store, ArtifactStore):
        raise TypeError("store must be an ArtifactStore")
    key = f"{key_prefix}/{uuid.uuid4().hex}/archive.tar"
    payload = b"isolattice conformance payload\n" * 64
    archive = workdir / "source"
    archive.write_bytes(payload)
    manifest = ArtifactManifest(
        kind=ArtifactKind.WORKSPACE_SNAPSHOT,
        backend="conformance",
        format="tar",
        archive=key,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        created_at="2000-01-01T00:00:00+00:00",
    )
    if await store.exists(key):
        raise ConformanceError("a fresh key was reported as existing")
    await store.publish(key, archive, manifest)
    try:
        if not await store.exists(key):
            raise ConformanceError("a published key was reported as missing")
        await _expect_refusal(
            store.publish(key, archive, manifest), "publishing an existing key did not fail"
        )
        fetched = workdir / "fetched"
        stored = await store.fetch(key, fetched, max_bytes=len(payload))
        if stored != manifest:
            raise ConformanceError("the fetched manifest differs from the published one")
        if fetched.read_bytes() != payload:
            raise ConformanceError("the fetched archive differs from the published one")
        await _expect_refusal(
            store.fetch(key, workdir / "bounded", max_bytes=len(payload) - 1),
            "fetching beyond max_bytes did not fail",
        )
        await _expect_refusal(
            store.fetch(f"{key}.missing", workdir / "missing", max_bytes=len(payload)),
            "fetching a missing key did not fail",
        )
    finally:
        deleted = await store.delete(key)
    if not deleted:
        raise ConformanceError("deleting a published key reported no artifact")
    if await store.exists(key):
        raise ConformanceError("a deleted key was reported as existing")
    if await store.delete(key):
        raise ConformanceError("deleting a missing key reported an artifact")
