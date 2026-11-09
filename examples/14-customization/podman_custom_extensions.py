"""Plug a custom ArtifactStore and CommandRunner into a rootless Podman sandbox.

The store keeps sealed workspace snapshots in a SQLite database and is checked
with ``isolattice.testing.check_artifact_store`` before use. The runner counts
the Podman client processes the backend starts, without recording their
arguments, which can hold environment values.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, override

from isolattice import (
    ArtifactManifest,
    ArtifactStore,
    Capability,
    CommandRunner,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxArtifactError,
    SandboxPolicy,
    SubprocessRunner,
)
from isolattice.testing import check_artifact_store

if TYPE_CHECKING:
    from collections.abc import Sequence

CHUNK = 1024 * 1024


class SQLiteArtifactStore(ArtifactStore):
    """Store each archive and its manifest together in one SQLite row."""

    def __init__(self, database: Path) -> None:
        self._database = database
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS artifacts ("
                "key TEXT PRIMARY KEY, manifest BLOB NOT NULL, archive BLOB NOT NULL)"
            )
        connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database, timeout=30, isolation_level="IMMEDIATE")

    def _publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        connection = self._connect()
        try:
            with connection, archive.open("rb") as source:
                cursor = connection.execute(
                    "INSERT INTO artifacts (key, manifest, archive) VALUES (?, ?, zeroblob(?))",
                    (key, manifest.to_json(), os.fstat(source.fileno()).st_size),
                )
                if cursor.lastrowid is None:
                    raise sqlite3.DatabaseError("the insert returned no row")
                with connection.blobopen("artifacts", "archive", cursor.lastrowid) as blob:
                    while chunk := source.read(CHUNK):
                        blob.write(chunk)
        except sqlite3.IntegrityError as error:
            raise SandboxArtifactError(f"artifact {key!r} already exists") from error
        except (OSError, ValueError, sqlite3.Error) as error:
            raise SandboxArtifactError(f"cannot store artifact {key!r}: {error}") from error
        finally:
            connection.close()

    def _fetch(self, key: str, destination: Path, max_bytes: int) -> ArtifactManifest:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT rowid, manifest, length(archive) FROM artifacts WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                raise SandboxArtifactError(f"artifact {key!r} does not exist")
            rowid, data, size = row
            if len(data) > max_bytes or size > max_bytes:
                raise SandboxArtifactError(f"artifact {key!r} exceeds {max_bytes} bytes")
            manifest = ArtifactManifest.from_json(data)
            with destination.open("xb") as target:
                try:
                    with connection.blobopen("artifacts", "archive", rowid, readonly=True) as blob:
                        while chunk := blob.read(CHUNK):
                            target.write(chunk)
                except BaseException:
                    target.close()
                    destination.unlink()
                    raise
        except ValueError as error:
            raise SandboxArtifactError(f"artifact {key!r} has a bad manifest: {error}") from error
        except (OSError, sqlite3.Error) as error:
            raise SandboxArtifactError(f"cannot fetch artifact {key!r}: {error}") from error
        finally:
            connection.close()
        return manifest

    def _exists(self, key: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute("SELECT 1 FROM artifacts WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as error:
            raise SandboxArtifactError(f"cannot query artifact {key!r}: {error}") from error
        finally:
            connection.close()
        return row is not None

    def _delete(self, key: str) -> bool:
        connection = self._connect()
        try:
            with connection:
                deleted = connection.execute("DELETE FROM artifacts WHERE key = ?", (key,))
        except sqlite3.Error as error:
            raise SandboxArtifactError(f"cannot delete artifact {key!r}: {error}") from error
        finally:
            connection.close()
        return deleted.rowcount == 1

    @override
    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        await asyncio.to_thread(self._publish, key, archive, manifest)

    @override
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        return await asyncio.to_thread(self._fetch, key, destination, max_bytes)

    @override
    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._exists, key)

    @override
    async def delete(self, key: str) -> bool:
        return await asyncio.to_thread(self._delete, key)


class CountingRunner(CommandRunner):
    """Delegate to SubprocessRunner and count client starts per executable name."""

    def __init__(self) -> None:
        self._inner = SubprocessRunner()
        self.starts: collections.Counter[str] = collections.Counter()

    @override
    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        self.starts[Path(argv[0]).stem] += 1
        return await self._inner.spawn(argv, stdin=stdin)


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    runner = CountingRunner()
    backend = PodmanBackend(
        connection=connection, image=image, allow_rootful=allow_rootful, runner=runner
    )
    policy = SandboxPolicy(
        workspace=True,
        required_capabilities=(Capability.WORKSPACE, Capability.SNAPSHOTS),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = SQLiteArtifactStore(root / "artifacts.sqlite3")
        conformance = root / "conformance"
        conformance.mkdir()
        await check_artifact_store(store, conformance)
        print("Store conformance: passed")
        key = "custom/workspace.tar"
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as first:
                await first.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", "echo 'kept in SQLite' > notes.txt"),
                        cwd="/workspace",
                    )
                )
                await first.snapshot(key)
            print("Snapshot stored in SQLite:", await store.exists(key))
            async with await sandbox.create(policy=policy, workspace_snapshot=key) as second:
                restored = await second.run(
                    ExecutionRequest(argv=("/bin/cat", "/workspace/notes.txt"))
                )
        if restored.stdout != b"kept in SQLite\n":
            raise RuntimeError("the workspace snapshot was not restored from SQLite")
        print("Restored workspace reads:", restored.stdout.decode().strip())
        print("Snapshot deleted:", await store.delete(key))
    if not runner.starts:
        raise RuntimeError("the custom runner started no engine clients")
    print("Engine clients started through the custom runner:", sum(runner.starts.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
