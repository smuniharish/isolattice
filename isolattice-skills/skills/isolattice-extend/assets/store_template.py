"""An ArtifactStore on the standard-library sqlite3 module.

Copy this file and adapt the storage calls. Keep every promise: exclusive
publication, archive and manifest committed together, bounded fetches, and
SandboxArtifactError for every failure. ``from_environment`` is the
zero-argument factory that ``scripts/check_store.py`` loads.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import override

from isolattice import ArtifactManifest, ArtifactStore, SandboxArtifactError

CHUNK = 1024 * 1024
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS artifacts ("
    "key TEXT PRIMARY KEY, manifest BLOB NOT NULL, archive BLOB NOT NULL)"
)


class SQLiteArtifactStore(ArtifactStore):
    """Store each archive and its manifest in one row of a SQLite database."""

    def __init__(self, database: str | os.PathLike[str]) -> None:
        """Use the SQLite database file at ``database``, creating its table."""
        self._database = Path(database)
        connection = self.connect()
        try:
            with connection:
                connection.execute(SCHEMA)
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        """Open a connection that takes the write lock at the start of each transaction."""
        return sqlite3.connect(self._database, timeout=30, isolation_level="IMMEDIATE")

    def _publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        connection = self.connect()
        try:
            with connection, archive.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                cursor = connection.execute(
                    "INSERT INTO artifacts (key, manifest, archive) VALUES (?, ?, zeroblob(?))",
                    (key, manifest.to_json(), size),
                )
                rowid = cursor.lastrowid
                if rowid is None:
                    raise sqlite3.DatabaseError("the insert returned no row")
                with connection.blobopen("artifacts", "archive", rowid) as blob:
                    while chunk := source.read(CHUNK):
                        blob.write(chunk)
        except sqlite3.IntegrityError as error:
            raise SandboxArtifactError(f"artifact {key!r} already exists") from error
        except (OSError, ValueError, sqlite3.Error) as error:
            raise SandboxArtifactError(f"cannot store artifact {key!r}: {error}") from error
        finally:
            connection.close()

    def _fetch(self, key: str, destination: Path, max_bytes: int) -> ArtifactManifest:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT rowid, length(manifest), length(archive) FROM artifacts WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise SandboxArtifactError(f"artifact {key!r} does not exist")
            rowid, manifest_size, archive_size = row
            if manifest_size > max_bytes or archive_size > max_bytes:
                raise SandboxArtifactError(f"artifact {key!r} exceeds {max_bytes} bytes")
            (data,) = connection.execute(
                "SELECT manifest FROM artifacts WHERE rowid = ?", (rowid,)
            ).fetchone()
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
        connection = self.connect()
        try:
            row = connection.execute("SELECT 1 FROM artifacts WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error as error:
            raise SandboxArtifactError(f"cannot query artifact {key!r}: {error}") from error
        finally:
            connection.close()
        return row is not None

    def _delete(self, key: str) -> bool:
        connection = self.connect()
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


def from_environment() -> SQLiteArtifactStore:
    """Build the store from the ``ISOLATTICE_SQLITE_DATABASE`` file path."""
    return SQLiteArtifactStore(os.environ["ISOLATTICE_SQLITE_DATABASE"])
