"""Pluggable storage for sealed snapshot and checkpoint archives.

Isolattice computes every manifest itself and re-verifies the archive's size and
SHA-256 after each fetch, so a store keeps bytes but never vouches for integrity.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import override

from .errors import SandboxArtifactError
from .models import (
    MANIFEST_SUFFIX,
    MAX_MANIFEST_BYTES,
    ArtifactManifest,
    validate_artifact_key,
)

__all__ = ["ArtifactStore", "LocalArtifactStore"]

_COPY_SIZE = 1024 * 1024
_BINARY = getattr(os, "O_BINARY", 0)


class ArtifactStore(ABC):
    """Durable storage for archives and their manifests, addressed by artifact key.

    Implementations must refuse to overwrite an existing key, make an archive
    visible only together with its manifest, and bound every fetch. Keys are
    validated with :func:`isolattice.validate_artifact_key` before they arrive.
    The runtime treats every fetched byte as untrusted. Use
    :func:`isolattice.testing.check_artifact_store` to verify an implementation.
    """

    @abstractmethod
    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        """Store a complete archive and its manifest under a new key.

        Args:
            key: The validated artifact key; ``manifest.archive`` equals it.
            archive: A local, complete archive file the store may read or link.
            manifest: The integrity record to keep with the archive.

        Raises:
            SandboxArtifactError: If the key exists or the archive cannot be stored.
        """

    @abstractmethod
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        """Copy a stored archive to a new local file and return its stored manifest.

        Args:
            key: The validated artifact key.
            destination: A path that does not exist yet; create it exclusively.
            max_bytes: Refuse archives or manifests that declare more bytes.

        Returns:
            The manifest stored with the archive, which the runtime re-verifies.

        Raises:
            SandboxArtifactError: If the key is absent, oversized or unreadable.
        """

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Report whether a complete artifact is stored under ``key``."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete an artifact and its manifest.

        Returns:
            ``True`` when an artifact was deleted and ``False`` when none existed.
        """


def _write_exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())


def _copy_exclusive(source: Path, destination: Path, limit: int | None) -> None:
    copied = 0
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
        while block := reader.read(_COPY_SIZE):
            copied += len(block)
            if limit is not None and copied > limit:
                raise SandboxArtifactError("stored artifact exceeds its size limit")
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())


class LocalArtifactStore(ArtifactStore):
    """Store artifacts as files below one existing directory.

    ``key`` ``runs/42/workspace.tar`` is stored at ``<root>/runs/42/workspace.tar``
    with its manifest beside it. Publication links complete files into place so
    a concurrent publisher of the same key fails instead of replacing it.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        """Use ``root``, which must be an existing directory when artifacts are stored.

        Raises:
            TypeError: If ``root`` is not a path.
        """
        if not isinstance(root, (str, os.PathLike)):
            raise TypeError("root must be a path")
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        """The resolved directory that contains every stored artifact."""
        return self._root

    def _paths(self, key: str) -> tuple[Path, Path]:
        validate_artifact_key(key)
        archive = self._root.joinpath(*key.split("/"))
        return archive, archive.with_name(archive.name + MANIFEST_SUFFIX)

    def _require_root(self) -> None:
        if not self._root.is_dir():
            raise SandboxArtifactError(f"artifact store root {self._root} is not a directory")

    def _contained(self, path: Path) -> None:
        # A symbolic link inside the store must not redirect an artifact elsewhere.
        if not path.resolve().is_relative_to(self._root):
            raise SandboxArtifactError(f"artifact path {path} leaves the store root")

    def _publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        self._require_root()
        final, record = self._paths(key)
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise SandboxArtifactError(
                f"could not create artifact directory for {key!r}"
            ) from error
        self._contained(final.parent)
        token = uuid.uuid4().hex
        staged = final.with_name(f".{final.name}.{token}.partial")
        staged_record = final.with_name(f".{final.name}.{token}.manifest.partial")
        try:
            _write_exclusive(staged_record, manifest.to_json())
            try:
                os.link(archive, staged)
            except OSError:
                _copy_exclusive(archive, staged, None)
            # Linking refuses to replace a concurrently published key, unlike rename;
            # the manifest is linked last so it only ever names a complete archive.
            try:
                os.link(staged, final)
            except FileExistsError as error:
                raise SandboxArtifactError(f"artifact {key!r} already exists") from error
            try:
                os.link(staged_record, record)
            except BaseException as error:
                final.unlink(missing_ok=True)
                if isinstance(error, FileExistsError):
                    raise SandboxArtifactError(f"artifact {key!r} already exists") from error
                raise
        except OSError as error:
            raise SandboxArtifactError(f"could not store artifact {key!r}") from error
        finally:
            staged.unlink(missing_ok=True)
            staged_record.unlink(missing_ok=True)

    def _fetch(self, key: str, destination: Path, max_bytes: int) -> ArtifactManifest:
        final, record = self._paths(key)
        if not record.is_file():
            raise SandboxArtifactError(f"artifact {key!r} does not exist")
        self._contained(record)
        self._contained(final)
        try:
            with record.open("rb") as file:
                raw = file.read(MAX_MANIFEST_BYTES + 1)
            manifest = ArtifactManifest.from_json(raw)
        except OSError as error:
            raise SandboxArtifactError(f"artifact {key!r} manifest is unreadable") from error
        except ValueError as error:
            raise SandboxArtifactError(f"artifact {key!r} manifest is malformed") from error
        if manifest.size > max_bytes:
            raise SandboxArtifactError("stored artifact exceeds its size limit")
        try:
            _copy_exclusive(final, destination, max_bytes)
        except OSError as error:
            raise SandboxArtifactError(f"artifact {key!r} archive is unreadable") from error
        return manifest

    def _delete(self, key: str) -> bool:
        final, record = self._paths(key)
        existed = record.is_file()
        try:
            record.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
        except OSError as error:
            raise SandboxArtifactError(f"could not delete artifact {key!r}") from error
        return existed

    @override
    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        """Link or copy ``archive`` below the root, then publish its manifest."""
        await asyncio.to_thread(self._publish, key, archive, manifest)

    @override
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        """Copy the stored archive to ``destination`` without exceeding ``max_bytes``."""
        return await asyncio.to_thread(self._fetch, key, destination, max_bytes)

    @override
    async def exists(self, key: str) -> bool:
        """Report whether the manifest, and therefore the archive, is published."""
        _, record = self._paths(key)
        return await asyncio.to_thread(record.is_file)

    @override
    async def delete(self, key: str) -> bool:
        """Remove the manifest first so a concurrent fetch never sees a lone archive."""
        return await asyncio.to_thread(self._delete, key)
