"""S3ArtifactStore contract tests against an in-memory aiobotocore-shaped client.

These tests prove request shapes, conditional writes, bounded reads and cleanup.
They do not prove compatibility with a real S3 server; the live RustFS test does.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings
from hypothesis import strategies as st

from isolattice import ArtifactKind, ArtifactManifest, SandboxArtifactError
from isolattice.s3 import S3ArtifactStore, S3Credentials
from isolattice.testing import check_artifact_store

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

_MIB = 1024 * 1024


def _error(code: str, operation: str = "Operation") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def __aenter__(self) -> FakeBody:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.closed = True

    async def iter_chunks(self, size: int) -> AsyncIterator[bytes]:
        for start in range(0, len(self.data), size):
            yield self.data[start : start + size]


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict[int, bytes]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bodies: list[FakeBody] = []
        self.fail: dict[str, Exception] = {}
        self.client_arguments: list[dict[str, object]] = []
        self.declared_length: int | None = None

    def _call(self, name: str, arguments: dict[str, Any]) -> None:
        self.calls.append((name, arguments))
        if name in self.fail:
            raise self.fail.pop(name)

    async def put_object(self, **arguments: Any) -> dict[str, object]:
        self._call("put_object", arguments)
        key = arguments["Key"]
        if arguments.get("IfNoneMatch") == "*" and key in self.objects:
            raise _error("PreconditionFailed", "PutObject")
        self.objects[key] = bytes(arguments["Body"])
        return {}

    async def create_multipart_upload(self, **arguments: Any) -> dict[str, object]:
        self._call("create_multipart_upload", arguments)
        upload_id = f"upload-{len(self.uploads)}"
        self.uploads[upload_id] = {}
        return {"UploadId": upload_id}

    async def upload_part(self, **arguments: Any) -> dict[str, object]:
        self._call("upload_part", arguments)
        self.uploads[arguments["UploadId"]][arguments["PartNumber"]] = bytes(arguments["Body"])
        return {"ETag": f'"{arguments["PartNumber"]}"'}

    async def complete_multipart_upload(self, **arguments: Any) -> dict[str, object]:
        self._call("complete_multipart_upload", arguments)
        key = arguments["Key"]
        if arguments.get("IfNoneMatch") == "*" and key in self.objects:
            raise _error("PreconditionFailed", "CompleteMultipartUpload")
        parts = self.uploads.pop(arguments["UploadId"])
        numbers = [part["PartNumber"] for part in arguments["MultipartUpload"]["Parts"]]
        assert numbers == sorted(parts)
        self.objects[key] = b"".join(parts[number] for number in numbers)
        return {}

    async def abort_multipart_upload(self, **arguments: Any) -> dict[str, object]:
        self._call("abort_multipart_upload", arguments)
        self.uploads.pop(arguments["UploadId"], None)
        return {}

    async def get_object(self, **arguments: Any) -> dict[str, object]:
        self._call("get_object", arguments)
        key = arguments["Key"]
        if key not in self.objects:
            raise _error("NoSuchKey", "GetObject")
        body = FakeBody(self.objects[key])
        self.bodies.append(body)
        length = len(body.data) if self.declared_length is None else self.declared_length
        return {"ContentLength": length, "Body": body}

    async def head_object(self, **arguments: Any) -> dict[str, object]:
        self._call("head_object", arguments)
        if arguments["Key"] not in self.objects:
            raise _error("404", "HeadObject")
        return {}

    async def delete_object(self, **arguments: Any) -> dict[str, object]:
        self._call("delete_object", arguments)
        self.objects.pop(arguments["Key"], None)
        return {}


class FakeSession:
    def __init__(self, server: FakeS3) -> None:
        self.server = server

    @asynccontextmanager
    async def _client(self, service: str, **arguments: object) -> AsyncGenerator[FakeS3]:
        assert service == "s3"
        self.server.client_arguments.append(arguments)
        yield self.server

    def create_client(self, service: str, **arguments: object) -> Any:
        return self._client(service, **arguments)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    import aiobotocore.session

    fake = FakeS3()
    monkeypatch.setattr(aiobotocore.session, "get_session", lambda: FakeSession(fake))
    return fake


def _manifest(key: str, payload: bytes) -> ArtifactManifest:
    return ArtifactManifest(
        kind=ArtifactKind.WORKSPACE_SNAPSHOT,
        backend="fake",
        format="tar",
        archive=key,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        created_at="2000-01-01T00:00:00+00:00",
    )


def _archive(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "archive"
    path.write_bytes(payload)
    return path


def test_credentials_validate_and_hide_secrets() -> None:
    credentials = S3Credentials("AKID", "secret-value", "token-value")
    assert "secret-value" not in repr(credentials)
    assert "token-value" not in repr(credentials)
    with pytest.raises(ValueError, match="access_key_id"):
        S3Credentials("", "secret")
    with pytest.raises(ValueError, match="secret_access_key"):
        S3Credentials("AKID", "")
    with pytest.raises(ValueError, match="session_token"):
        S3Credentials("AKID", "secret", "")


def test_constructor_validates_arguments() -> None:
    with pytest.raises(ValueError, match="bucket"):
        S3ArtifactStore(bucket="")
    with pytest.raises(ValueError, match="bucket"):
        S3ArtifactStore(bucket="a/b")
    with pytest.raises(TypeError, match="prefix"):
        S3ArtifactStore(bucket="b", prefix=1)  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(ValueError, match="key"):
        S3ArtifactStore(bucket="b", prefix="../x")
    with pytest.raises(TypeError, match="credentials"):
        S3ArtifactStore(bucket="b", credentials=("a", "b"))  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(ValueError, match="part_size"):
        S3ArtifactStore(bucket="b", part_size=_MIB)
    with pytest.raises(ValueError, match="part_size"):
        S3ArtifactStore(bucket="b", part_size=True)
    for option in ("endpoint_url", "region_name", "aws_access_key_id", "service_name"):
        with pytest.raises(ValueError, match="client_options"):
            S3ArtifactStore(bucket="b", client_options={option: "x"})
    store = S3ArtifactStore(bucket="b", prefix="team/a/")
    assert (store.bucket, store.prefix) == ("b", "team/a/")
    assert S3ArtifactStore(bucket="b", prefix="team").prefix == "team/"
    assert S3ArtifactStore(bucket="b").prefix == ""


def test_store_passes_the_conformance_check(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(
        bucket="artifacts",
        prefix="ci",
        endpoint_url="http://127.0.0.1:9000",
        region_name="us-east-1",
        credentials=S3Credentials("AKID", "secret", "token"),
        client_options={"verify": False},
    )
    asyncio.run(check_artifact_store(store, tmp_path))
    assert server.objects == {}
    assert server.client_arguments[0] == {
        "verify": False,
        "endpoint_url": "http://127.0.0.1:9000",
        "region_name": "us-east-1",
        "aws_access_key_id": "AKID",
        "aws_secret_access_key": "secret",
        "aws_session_token": "token",
    }
    puts = [arguments for name, arguments in server.calls if name == "put_object"]
    assert puts
    assert all(arguments["IfNoneMatch"] == "*" for arguments in puts)
    assert all(arguments["Key"].startswith("ci/") for arguments in puts)
    assert server.bodies
    assert all(body.closed for body in server.bodies)


def test_static_credentials_without_a_session_token(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(bucket="artifacts", credentials=S3Credentials("AKID", "secret"))
    payload = b"data"
    asyncio.run(store.publish("k", _archive(tmp_path, payload), _manifest("k", payload)))
    assert server.client_arguments == [
        {"aws_access_key_id": "AKID", "aws_secret_access_key": "secret"}
    ]
    assert set(server.objects) == {"k", "k.manifest.json"}


@settings(max_examples=25, deadline=None)
@given(extra=st.integers(min_value=1, max_value=3 * _MIB))
def test_large_archives_use_bounded_multipart_uploads(extra: int) -> None:
    import tempfile

    payload = bytes(range(256)) * ((5 * _MIB + extra) // 256 + 1)
    server = FakeS3()

    async def check(root: Path) -> None:
        import aiobotocore.session

        original = aiobotocore.session.get_session
        aiobotocore.session.get_session = lambda: FakeSession(server)  # pyrefly: ignore[bad-assignment]
        try:
            store = S3ArtifactStore(bucket="b", part_size=5 * _MIB)
            await store.publish("big", _archive(root, payload), _manifest("big", payload))
            destination = root / "fetched"
            await store.fetch("big", destination, max_bytes=len(payload))
            assert destination.read_bytes() == payload
        finally:
            aiobotocore.session.get_session = original

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(check(Path(directory)))
    sizes = [len(arguments["Body"]) for name, arguments in server.calls if name == "upload_part"]
    assert len(sizes) == -(-len(payload) // (5 * _MIB))
    assert all(size == 5 * _MIB for size in sizes[:-1])
    assert server.objects["big"] == payload
    assert not server.uploads


def test_part_size_grows_to_respect_the_part_limit(
    server: FakeS3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import isolattice.s3

    monkeypatch.setattr(isolattice.s3, "_MAX_PARTS", 2)
    store = S3ArtifactStore(bucket="b", part_size=5 * _MIB)
    payload = b"y" * (11 * _MIB)
    asyncio.run(store.publish("wide", _archive(tmp_path, payload), _manifest("wide", payload)))
    sizes = [len(arguments["Body"]) for name, arguments in server.calls if name == "upload_part"]
    assert sizes == [-(-len(payload) // 2), len(payload) - -(-len(payload) // 2)]
    assert server.objects["wide"] == payload


def test_failed_part_aborts_the_upload(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(bucket="b", part_size=5 * _MIB)
    payload = b"z" * (6 * _MIB)
    server.fail["upload_part"] = _error("InternalError")
    with pytest.raises(SandboxArtifactError, match="could not upload"):
        asyncio.run(store.publish("k", _archive(tmp_path, payload), _manifest("k", payload)))
    assert [name for name, _ in server.calls][-1] == "abort_multipart_upload"
    assert not server.uploads
    assert "k" not in server.objects


def test_publish_maps_conflicts_and_failures(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(bucket="b", part_size=5 * _MIB)
    payload = b"payload"
    archive = _archive(tmp_path, payload)

    async def check() -> None:
        server.objects["taken"] = b"other"
        with pytest.raises(SandboxArtifactError, match="already exists"):
            await store.publish("taken", archive, _manifest("taken", payload))
        assert server.objects["taken"] == b"other"

        server.fail["put_object"] = _error("InternalError")
        with pytest.raises(SandboxArtifactError, match="could not upload artifact 'a'"):
            await store.publish("a", archive, _manifest("a", payload))

        server.objects["b.manifest.json"] = b"{}"
        with pytest.raises(SandboxArtifactError, match="already exists"):
            await store.publish("b", archive, _manifest("b", payload))
        assert "b" not in server.objects

        original = server.put_object
        attempts = 0

        async def manifest_fails(**arguments: Any) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise _error("SlowDown")
            return await original(**arguments)

        server.put_object = manifest_fails
        with pytest.raises(SandboxArtifactError, match="manifest"):
            await store.publish("c", archive, _manifest("c", payload))
        assert "c" not in server.objects

        attempts = 0

        async def manifest_cancelled(**arguments: Any) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise asyncio.CancelledError
            return await original(**arguments)

        server.put_object = manifest_cancelled
        with pytest.raises(asyncio.CancelledError):
            await store.publish("d", archive, _manifest("d", payload))
        assert "d" not in server.objects

    asyncio.run(check())


def test_multipart_conflict_is_reported_and_aborted(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(bucket="b", part_size=5 * _MIB)
    payload = b"x" * (5 * _MIB + 1)
    server.objects["k"] = b"existing"
    with pytest.raises(SandboxArtifactError, match="already exists"):
        asyncio.run(store.publish("k", _archive(tmp_path, payload), _manifest("k", payload)))
    assert server.objects["k"] == b"existing"
    assert [name for name, _ in server.calls][-1] == "abort_multipart_upload"


def test_fetch_bounds_and_maps_errors(server: FakeS3, tmp_path: Path) -> None:
    store = S3ArtifactStore(bucket="b")
    payload = b"payload" * 100
    manifest = _manifest("k", payload)
    server.objects["k"] = payload
    server.objects["k.manifest.json"] = manifest.to_json()

    async def check() -> None:
        assert await store.fetch("k", tmp_path / "ok", max_bytes=len(payload)) == manifest
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("k", tmp_path / "small", max_bytes=len(payload) - 1)
        with pytest.raises(SandboxArtifactError, match="does not exist"):
            await store.fetch("missing", tmp_path / "missing", max_bytes=10)

        server.fail["get_object"] = _error("AccessDenied")
        with pytest.raises(SandboxArtifactError, match="manifest is unreadable"):
            await store.fetch("k", tmp_path / "denied", max_bytes=len(payload))

        server.objects["bad.manifest.json"] = b"not json"
        server.objects["bad"] = payload
        with pytest.raises(SandboxArtifactError, match="malformed"):
            await store.fetch("bad", tmp_path / "bad", max_bytes=len(payload))

        server.objects["lone.manifest.json"] = _manifest("lone", payload).to_json()
        with pytest.raises(SandboxArtifactError, match="archive is unreadable"):
            await store.fetch("lone", tmp_path / "lone", max_bytes=len(payload))

        server.objects["huge.manifest.json"] = b"x" * (64 * 1024 + 1)
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("huge", tmp_path / "huge", max_bytes=len(payload))

        # A server that understates ContentLength is still bounded while streaming.
        server.declared_length = 1
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("k", tmp_path / "lying", max_bytes=len(payload) - 1)
        server.objects["huge2.manifest.json"] = b"x" * (64 * 1024 + 1)
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("huge2", tmp_path / "huge2", max_bytes=len(payload))
        server.objects["liar"] = payload
        server.objects["liar.manifest.json"] = _manifest("liar", b"x").to_json()
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("liar", tmp_path / "liar", max_bytes=10)
        server.declared_length = None

        server.fail["get_object"] = OSError("connection reset")
        with pytest.raises(SandboxArtifactError, match="manifest is unreadable"):
            await store.fetch("k", tmp_path / "reset", max_bytes=len(payload))

        (tmp_path / "exists").write_bytes(b"")
        with pytest.raises(SandboxArtifactError, match="archive is unreadable"):
            await store.fetch("k", tmp_path / "exists", max_bytes=len(payload))

    asyncio.run(check())
    assert all(body.closed for body in server.bodies)


def test_exists_and_delete_map_errors(server: FakeS3) -> None:
    store = S3ArtifactStore(bucket="b")

    async def check() -> None:
        server.fail["head_object"] = _error("AccessDenied")
        with pytest.raises(SandboxArtifactError, match="could not check"):
            await store.exists("k")
        odd = RuntimeError("odd")
        odd.response = {"Error": "not a mapping"}  # pyrefly: ignore[missing-attribute]
        server.fail["head_object"] = odd
        with pytest.raises(SandboxArtifactError, match="could not check"):
            await store.exists("k")
        assert not await store.delete("k")
        server.objects["k.manifest.json"] = b"{}"
        server.fail["delete_object"] = _error("AccessDenied")
        with pytest.raises(SandboxArtifactError, match="could not delete"):
            await store.delete("k")
        with pytest.raises(ValueError, match="key"):
            await store.exists("../k")

    asyncio.run(check())


def test_missing_extra_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "aiobotocore.session", None)
    store = S3ArtifactStore(bucket="b")
    with pytest.raises(SandboxArtifactError, match=r"isolattice\[s3\]"):
        asyncio.run(store.exists("k"))
