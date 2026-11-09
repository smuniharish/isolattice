# Custom integrations

Downstream packages can customize how Isolattice stores artifacts, reports events and
launches container engine clients without writing a backend. Each extension point is
an abstract base class exported from `isolattice`.

| Need | Extension point | Passed to |
| --- | --- | --- |
| Store snapshots and checkpoints elsewhere | `ArtifactStore` | `Sandbox(artifact_store=...)` |
| Send lifecycle events to your telemetry | `EventSink` | `Sandbox(event_sink=...)` |
| Audit or wrap engine client processes | `CommandRunner` | `PodmanBackend`, `DockerBackend`, `KubernetesBackend` (`runner=...`) |
| Control where archives are staged | a directory path | `Sandbox(staging_directory=...)` |
| Tune the S3 client | `client_options` | `S3ArtifactStore(...)` |
| A new isolation mechanism | `Backend` | See [custom backends](custom-backend.md) |

!!! warning "Integrations are trusted code"
    Your implementations run inside the runtime with access to artifacts and engine
    commands. They do not change the built-in backends' threat model: shared-kernel
    boundaries still leave kernel, engine and cluster exploits out of scope.

## Custom artifact store

An `ArtifactStore` keeps an archive and its manifest under a key. Implement four
coroutines:

| Method | Contract |
| --- | --- |
| `publish(key, archive, manifest)` | Store a new key; raise `SandboxArtifactError` if it exists. Make the archive visible only together with its manifest. |
| `fetch(key, destination, *, max_bytes)` | Create `destination` exclusively, copy at most `max_bytes`, return the stored manifest. Raise `SandboxArtifactError` if missing or too large. |
| `exists(key)` | Report whether a complete artifact is stored. |
| `delete(key)` | Delete it; return `True` if something was deleted. |

Keys are validated before they reach the store. The runtime re-verifies size and
SHA-256 after every fetch, so a store never has to vouch for integrity, but it must
never overwrite.

This in-memory store suits tests:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from isolattice import ArtifactManifest, ArtifactStore, SandboxArtifactError


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)


class MemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._items: dict[str, tuple[bytes, bytes]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        data = await asyncio.to_thread(archive.read_bytes)
        async with self._lock:
            if key in self._items:
                raise SandboxArtifactError(f"artifact {key!r} already exists")
            self._items[key] = (data, manifest.to_json())

    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        async with self._lock:
            item = self._items.get(key)
        if item is None:
            raise SandboxArtifactError(f"artifact {key!r} does not exist")
        data, raw_manifest = item
        manifest = ArtifactManifest.from_json(raw_manifest)
        if len(data) > max_bytes or manifest.size > max_bytes:
            raise SandboxArtifactError(f"artifact {key!r} exceeds {max_bytes} bytes")
        await asyncio.to_thread(_write_new, destination, data)
        return manifest

    async def exists(self, key: str) -> bool:
        async with self._lock:
            return key in self._items

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return self._items.pop(key, None) is not None
```

### Check conformance

`isolattice.testing.check_artifact_store()` publishes, re-publishes, fetches, bounds
and deletes one disposable artifact, and raises `ConformanceError` on any violation.
It has no test-framework dependency, so it also works as a deployment health check:

```python
import tempfile
from pathlib import Path

from isolattice.testing import check_artifact_store


async def verify_store() -> None:
    with tempfile.TemporaryDirectory() as workdir:
        await check_artifact_store(MemoryArtifactStore(), Path(workdir))
```

`workdir` must be an existing, empty, writable directory. Pass `key_prefix=` to keep
the probe artifact under a prefix your bucket policy allows.

### Built-in stores

- `LocalArtifactStore(root)` stores files below a directory. It hard-links the staged
  archive when the store root and the staging directory share a filesystem.
- `S3ArtifactStore` stores objects in one bucket using conditional writes, so a key
  is never overwritten. See [S3 client options](#s3-client-options).

## Custom event sink

An `EventSink` receives every `SandboxEvent` synchronously on the event loop.
`emit()` must not block: buffer the event and export it from a separate task. If
`emit()` raises, the runtime logs and suppresses the error so telemetry never
interrupts cleanup.

```python
import asyncio

from isolattice import EventSink, SandboxEvent


class QueueEventSink(EventSink):
    def __init__(self, maxsize: int = 10_000) -> None:
        self.queue: asyncio.Queue[SandboxEvent] = asyncio.Queue(maxsize)
        self.dropped = 0

    def emit(self, event: SandboxEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1


async def export_events(sink: QueueEventSink) -> None:
    while True:
        event = await sink.queue.get()
        record = {
            "kind": event.kind.value,
            "sandbox": event.sandbox_id,
            "execution": event.execution_id,
            "backend": event.backend,
            "warning": event.is_warning,
            **event.detail,
        }
        print(record)
```

Events never contain argv, environment, stdin or output, so they are safe for shared
telemetry. Artifact keys do appear in events. The default `LoggingEventSink` writes to
the `isolattice` logger and logs warning events (`event.is_warning`) at `WARNING`. See
the [event reference](../reference/events.md).

## Custom command runner

Container backends start engine clients (`podman`, `docker`, `kubectl`) through a
`CommandRunner`. Its single method, `spawn(argv, *, stdin)`, returns an
`asyncio.subprocess.Process` with piped stdout and stderr, and a piped stdin when
`stdin` is true. The backend owns the returned process: it bounds output, enforces
deadlines, and kills and reaps it.

This runner records every engine invocation without logging arguments, which can
contain container names, labels and file paths:

```python
import asyncio
import logging
from collections.abc import Sequence
from pathlib import PurePath

from isolattice import CommandRunner, PodmanBackend, SubprocessRunner

audit = logging.getLogger("myapp.engine-audit")


class AuditingRunner(CommandRunner):
    def __init__(self, inner: CommandRunner | None = None) -> None:
        self._inner = inner if inner is not None else SubprocessRunner()

    async def spawn(self, argv: Sequence[str], *, stdin: bool) -> asyncio.subprocess.Process:
        audit.info("engine client %s with %d arguments", PurePath(argv[0]).name, len(argv) - 1)
        return await self._inner.spawn(argv, stdin=stdin)


backend = PodmanBackend(
    connection="podman-machine-default",
    image="localhost/tools:latest",
    runner=AuditingRunner(),
)
```

Use a runner to add auditing, run the client under a dedicated service account, or
wrap it in a sandboxing launcher on the client host. Do not use it to run the client on
another machine (for example through `ssh`): backends pass local file paths for
environment files and archives, which would not exist there. Point the engine's own
remote connection (a Podman connection, `DOCKER_HOST`-style host, or kubeconfig) at the
remote engine instead.

## Staging directory

Snapshot and checkpoint archives are written to a private, per-operation directory
created under `Sandbox(staging_directory=...)` (the system temporary directory by
default) and removed afterwards.

```python
from isolattice import LocalArtifactStore, Sandbox

sandbox = Sandbox(
    backend=backend,
    artifact_store=LocalArtifactStore("/srv/isolattice/artifacts"),
    staging_directory="/srv/isolattice/staging",
)
```

Choose a directory that is:

- **Private to the service.** Staged checkpoints hold workload memory.
- **Large enough** for the biggest archive (up to 16 GiB).
- **On the same filesystem as a `LocalArtifactStore` root**, so publishing is a link
  instead of a copy.

## S3 client options

`S3ArtifactStore` needs the `isolattice[s3]` extra. It creates an aiobotocore client
per operation; `client_options` adds keyword arguments to `create_client`:

```python
from aiobotocore.config import AioConfig

from isolattice import S3ArtifactStore

store = S3ArtifactStore(
    bucket="isolattice-artifacts",
    prefix="team-a/",
    endpoint_url="https://minio.internal.example:9000",
    region_name="us-east-1",
    part_size=16 * 1024 * 1024,
    client_options={
        "config": AioConfig(
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3},
        ),
        "verify": "/etc/ssl/certs/internal-ca.pem",
    },
)
```

- `client_options` must not set `service_name`, `endpoint_url`, `region_name` or any
  `aws_*` credential; use the constructor arguments, which raise `ValueError`
  otherwise.
- `credentials=S3Credentials(...)` passes static credentials that never appear in
  `repr`. Without it, the botocore default chain (environment, profile, instance role)
  applies.
- `part_size` is the multipart upload size, at least 5 MiB.
- The bucket must support conditional writes (`If-None-Match: *`) so that a key is
  never overwritten.

Run `check_artifact_store()` against the configured bucket before relying on it.

## Related pages

- [Storage API](../api/storage.md), [events API](../api/events.md),
  [extension API](../api/extension.md) and [testing API](../api/testing.md)
- [Snapshots](snapshots.md) and [checkpoints](checkpoints.md)
- [Observability](../operations/observability.md)
