# ArtifactStore contract

`isolattice.ArtifactStore` keeps the archives of workspace snapshots,
container snapshots and CRIU checkpoints, each with its manifest. Isolattice
computes the manifest (kind, backend, format, key, SHA-256, size, creation
time) and verifies the archive against it after every fetch. The store only
keeps bytes.

## Abstract methods

```python
async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None
async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest
async def exists(self, key: str) -> bool
async def delete(self, key: str) -> bool
```

## Promises

1. `publish` refuses an existing key with `SandboxArtifactError`. It never
   overwrites, even when two publishers race.
2. The archive and the manifest become visible together. `exists` never
   reports a key whose archive or manifest is missing.
3. `fetch` creates `destination` exclusively. It refuses a missing key and an
   archive or manifest larger than `max_bytes` with `SandboxArtifactError`, and
   stops reading at the bound instead of trusting a declared size.
4. `fetch` returns the stored manifest unchanged; decode the stored bytes with
   `ArtifactManifest.from_json`, and store them with `manifest.to_json()`.
5. `delete` returns `True` when it removed an artifact and `False` when none
   existed, and removes the archive and the manifest.
6. Every error is a `SandboxArtifactError` with a message that names no
   credential. Wrap client exceptions with `raise ... from error`.
7. Blocking I/O runs off the event loop (`asyncio.to_thread`) or uses an async
   client.

Keys arrive already validated by `isolattice.validate_artifact_key`: relative,
`/`-separated, no empty, `.` or `..` segments, and never ending in the
manifest suffix.

## Verifying

```python
from pathlib import Path

from isolattice.testing import check_artifact_store


async def test_store(tmp_path: Path) -> None:
    await check_artifact_store(MyStore(...), tmp_path, key_prefix="ci")
```

`check_artifact_store` publishes, re-publishes, fetches, bounds and deletes
one disposable artifact, then raises `isolattice.testing.ConformanceError`
naming the first broken promise. `scripts/check_store.py` runs the same check
from the command line.

## Built-in stores

- `LocalArtifactStore(root)`: files below one existing directory. Publication
  links complete files into place, so concurrent publishers of a key cannot
  replace each other.
- `S3ArtifactStore(bucket=..., prefix=..., endpoint_url=..., credentials=...)`
  from `isolattice[s3]`: S3-compatible object storage through aiobotocore.
  Credentials are passed explicitly and never appear in `repr` or errors.
