"""Integrity manifests and safe-archive validation for snapshots and checkpoints."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import re
import shutil
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from .errors import SandboxArtifactError
from .models import ArtifactKind, ArtifactManifest

_READ_SIZE = 1024 * 1024
_DRIVE = re.compile(r"[A-Za-z]:")
_REMOVE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)
WORKSPACE_FORMAT = "tar"
ROOTFS_FORMAT = "rootfs-tar"
CHECKPOINT_FORMAT = "backend-checkpoint"


async def stage(parent: Path | None) -> Path:
    """Create a private staging directory for one artifact operation."""
    return Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="isolattice-", dir=parent))


def _rmtree_if_present(directory: Path) -> None:
    # A cancelled earlier attempt may have finished in its worker thread.
    if directory.exists():
        shutil.rmtree(directory)


def _remove_tree(directory: Path) -> None:
    # On Windows, scanners and indexers briefly hold just-deleted files open, leaving
    # them delete-pending so removing the parent directory fails; retry a bounded number
    # of times, then surface the failure.
    if sys.platform == "win32":
        for delay in _REMOVE_RETRY_DELAYS:
            try:
                _rmtree_if_present(directory)
            except PermissionError:
                time.sleep(delay)
            else:
                return
    _rmtree_if_present(directory)


async def unstage(directory: Path) -> None:
    """Remove a staging directory; archives may contain workload memory."""
    await asyncio.to_thread(_remove_tree, directory)


def _digest(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as file:
        while block := file.read(_READ_SIZE):
            hasher.update(block)
            size += len(block)
    return hasher.hexdigest(), size


async def seal(
    kind: ArtifactKind, backend: str, archive_format: str, archive: Path, key: str
) -> ArtifactManifest:
    """Hash a completed archive and describe it as the artifact ``key``."""
    digest, size = await asyncio.to_thread(_digest, archive)
    return ArtifactManifest(
        kind=kind,
        backend=backend,
        format=archive_format,
        archive=key,
        sha256=digest,
        size=size,
        created_at=datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    )


async def verify(
    archive: Path,
    manifest: ArtifactManifest,
    *,
    key: str,
    kind: ArtifactKind,
    backend: str,
    archive_format: str,
    max_bytes: int,
) -> None:
    """Check a fetched archive against its manifest without trusting the store."""
    if manifest.archive != key:
        raise SandboxArtifactError("artifact manifest names a different key")
    if manifest.kind is not kind:
        raise SandboxArtifactError(f"artifact is a {manifest.kind.value}, not a {kind.value}")
    if manifest.backend != backend:
        raise SandboxArtifactError(
            f"artifact was produced by backend {manifest.backend!r}, not {backend!r}"
        )
    if manifest.format != archive_format:
        raise SandboxArtifactError(f"artifact format {manifest.format!r} is not {archive_format!r}")
    if manifest.size > max_bytes:
        raise SandboxArtifactError("artifact exceeds the configured restore size limit")
    try:
        digest, size = await asyncio.to_thread(_digest, archive)
    except OSError as error:
        raise SandboxArtifactError("fetched artifact archive is unreadable") from error
    if size != manifest.size or digest != manifest.sha256:
        raise SandboxArtifactError("artifact archive does not match its integrity manifest")


def _validate_workspace_tar(archive: Path, max_members: int, max_bytes: int) -> None:
    members = 0
    total = 0
    try:
        with tarfile.open(archive, mode="r:") as bundle:
            for member in bundle:
                members += 1
                if members > max_members:
                    raise SandboxArtifactError("workspace archive has too many members")
                if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
                    raise SandboxArtifactError(
                        f"workspace archive member {member.name!r} is not a regular file, "
                        "directory or link"
                    )
                # data_filter rewrites absolute names; backend extractors may not, so
                # reject them outright instead of relying on the rewrite.
                name = PurePosixPath(member.name)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in member.name
                    or _DRIVE.match(member.name) is not None
                ):
                    raise SandboxArtifactError(
                        f"workspace archive member {member.name!r} is unsafe: "
                        "absolute or traversing name"
                    )
                try:
                    tarfile.data_filter(member, "/isolattice-workspace")
                except tarfile.FilterError as error:
                    raise SandboxArtifactError(
                        f"workspace archive member {member.name!r} is unsafe: {error}"
                    ) from error
                total += member.size
                if total > max_bytes:
                    raise SandboxArtifactError("workspace archive expands beyond its size limit")
    except (tarfile.TarError, OSError) as error:
        raise SandboxArtifactError("workspace archive is not a valid uncompressed tar") from error


async def validate_workspace_tar(archive: Path, *, max_members: int, max_bytes: int) -> None:
    """Reject absolute, traversing, special, link-escaping or oversized members."""
    await asyncio.to_thread(_validate_workspace_tar, archive, max_members, max_bytes)


def _unsafe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or _DRIVE.match(name) is not None
    )


def _validate_rootfs_tar(archive: Path, max_members: int, max_bytes: int) -> None:
    members = 0
    total = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive, mode="r:") as bundle:
            for member in bundle:
                members += 1
                if members > max_members:
                    raise SandboxArtifactError("container snapshot has too many members")
                if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
                    raise SandboxArtifactError(
                        f"container snapshot member {member.name!r} is a device or special file"
                    )
                if _unsafe_name(member.name):
                    raise SandboxArtifactError(
                        f"container snapshot member {member.name!r} has an absolute or "
                        "traversing name"
                    )
                # Symbolic links resolve inside the container root; hard links must
                # name an earlier member so the engine never links outside the archive.
                if member.islnk() and (
                    _unsafe_name(member.linkname)
                    or PurePosixPath(member.linkname).as_posix() not in seen
                ):
                    raise SandboxArtifactError(
                        f"container snapshot hard link {member.name!r} names no earlier member"
                    )
                seen.add(PurePosixPath(member.name).as_posix())
                total += member.size
                if total > max_bytes:
                    raise SandboxArtifactError("container snapshot expands beyond its size limit")
    except (tarfile.TarError, OSError) as error:
        raise SandboxArtifactError("container snapshot is not a valid uncompressed tar") from error


async def validate_rootfs_tar(archive: Path, *, max_members: int, max_bytes: int) -> None:
    """Reject special files, unsafe names, dangling hard links and oversized archives.

    The archive is handed to the container engine and never extracted on the host.
    """
    await asyncio.to_thread(_validate_rootfs_tar, archive, max_members, max_bytes)
