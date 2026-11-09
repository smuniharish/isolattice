"""S3-compatible artifact storage using aiobotocore.

Install with ``pip install "isolattice[s3]"``. Objects are written with
conditional ``If-None-Match: *`` requests so a key is never overwritten; the
endpoint must support conditional writes (AWS S3 and current S3-compatible
servers do). Archives are streamed in bounded parts and never held in memory.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from .errors import SandboxArtifactError
from .models import (
    MANIFEST_SUFFIX,
    MAX_MANIFEST_BYTES,
    ArtifactManifest,
    validate_artifact_key,
)
from .storage import ArtifactStore

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["S3ArtifactStore", "S3Credentials"]

_MIN_PART_SIZE = 5 * 1024 * 1024
_MAX_PARTS = 10_000
_EXISTS = frozenset({"PreconditionFailed", "ConditionalRequestConflict", "412", "409"})
_MISSING = frozenset({"NoSuchKey", "404", "NotFound"})


@dataclass(frozen=True, slots=True)
class S3Credentials:
    """Static S3 credentials; the secret and token never appear in ``repr``."""

    access_key_id: str
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        for name in ("access_key_id", "secret_access_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        if self.session_token is not None and (
            not isinstance(self.session_token, str) or not self.session_token
        ):
            raise ValueError("session_token must be a nonempty string when given")


def _code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        details = response.get("Error")
        if isinstance(details, Mapping):
            return str(details.get("Code", ""))
    return ""


class S3ArtifactStore(ArtifactStore):
    """Store artifacts as objects in one S3 bucket below an optional key prefix.

    The client is created per operation from an aiobotocore session, so the store
    holds no open connections between operations and needs no explicit closing.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        endpoint_url: str | None = None,
        region_name: str | None = None,
        credentials: S3Credentials | None = None,
        part_size: int = 8 * 1024 * 1024,
        client_options: Mapping[str, object] | None = None,
    ) -> None:
        """Configure the bucket and client.

        Args:
            bucket: The existing bucket that holds every artifact.
            prefix: A key prefix such as ``"team-a/"``; it follows artifact key rules.
            endpoint_url: The S3-compatible endpoint; ``None`` uses AWS.
            region_name: The signing region; ``None`` uses the botocore default chain.
            credentials: Static credentials; ``None`` uses the botocore default chain.
            part_size: The multipart upload part size, at least 5 MiB.
            client_options: Extra keyword arguments for ``create_client``, such as
                ``config`` or ``verify``.

        Raises:
            TypeError: If an argument has the wrong type.
            ValueError: If an argument is out of range.
        """
        if not isinstance(bucket, str) or not bucket or "/" in bucket:
            raise ValueError("bucket must be a nonempty bucket name")
        if not isinstance(prefix, str):
            raise TypeError("prefix must be a string")
        if prefix:
            validate_artifact_key(prefix.removesuffix("/"))
            prefix = prefix.removesuffix("/") + "/"
        if credentials is not None and not isinstance(credentials, S3Credentials):
            raise TypeError("credentials must be S3Credentials")
        if type(part_size) is not int or part_size < _MIN_PART_SIZE:
            raise ValueError("part_size must be an integer of at least 5 MiB")
        options = dict(client_options or {})
        reserved = {"service_name", "endpoint_url", "region_name"} | {
            name for name in options if name.startswith("aws_")
        }
        if reserved & set(options):
            raise ValueError("client_options must not set the endpoint, region or credentials")
        self._bucket = bucket
        self._prefix = prefix
        self._endpoint_url = endpoint_url
        self._region_name = region_name
        self._credentials = credentials
        self._part_size = part_size
        self._options = options

    @property
    def bucket(self) -> str:
        """The bucket that holds every artifact."""
        return self._bucket

    @property
    def prefix(self) -> str:
        """The normalized key prefix, empty or ending with ``/``."""
        return self._prefix

    def _names(self, key: str) -> tuple[str, str]:
        validate_artifact_key(key)
        name = self._prefix + key
        return name, name + MANIFEST_SUFFIX

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[Any]:
        try:
            from aiobotocore.session import get_session
        except ImportError as error:
            raise SandboxArtifactError(
                'S3ArtifactStore requires the "isolattice[s3]" extra'
            ) from error
        arguments: dict[str, object] = dict(self._options)
        if self._endpoint_url is not None:
            arguments["endpoint_url"] = self._endpoint_url
        if self._region_name is not None:
            arguments["region_name"] = self._region_name
        if self._credentials is not None:
            arguments["aws_access_key_id"] = self._credentials.access_key_id
            arguments["aws_secret_access_key"] = self._credentials.secret_access_key
            if self._credentials.session_token is not None:
                arguments["aws_session_token"] = self._credentials.session_token
        async with get_session().create_client("s3", **arguments) as client:
            yield client

    async def _put_new(self, client: Any, name: str, body: bytes) -> None:
        await client.put_object(Bucket=self._bucket, Key=name, Body=body, IfNoneMatch="*")

    async def _upload(self, client: Any, name: str, archive: Path) -> None:
        size = (await asyncio.to_thread(archive.stat)).st_size
        if size <= self._part_size:
            await self._put_new(client, name, await asyncio.to_thread(archive.read_bytes))
            return
        part_size = max(self._part_size, -(-size // _MAX_PARTS))
        upload = await client.create_multipart_upload(Bucket=self._bucket, Key=name)
        upload_id = upload["UploadId"]
        try:
            parts: list[dict[str, object]] = []
            with archive.open("rb") as file:
                while block := await asyncio.to_thread(file.read, part_size):
                    number = len(parts) + 1
                    response = await client.upload_part(
                        Bucket=self._bucket,
                        Key=name,
                        UploadId=upload_id,
                        PartNumber=number,
                        Body=block,
                    )
                    parts.append({"PartNumber": number, "ETag": response["ETag"]})
            await client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=name,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
        except BaseException:
            await client.abort_multipart_upload(Bucket=self._bucket, Key=name, UploadId=upload_id)
            raise

    @override
    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        """Upload the archive, then its manifest, both refusing existing objects."""
        name, record = self._names(key)
        async with self._client() as client:
            try:
                await self._upload(client, name, archive)
            except Exception as error:
                if _code(error) in _EXISTS:
                    raise SandboxArtifactError(f"artifact {key!r} already exists") from error
                raise SandboxArtifactError(f"could not upload artifact {key!r}") from error
            try:
                await self._put_new(client, record, manifest.to_json())
            except BaseException as error:
                # The manifest marks completeness; never leave a lone archive behind.
                await client.delete_object(Bucket=self._bucket, Key=name)
                if isinstance(error, Exception):
                    if _code(error) in _EXISTS:
                        raise SandboxArtifactError(f"artifact {key!r} already exists") from error
                    raise SandboxArtifactError(
                        f"could not upload artifact {key!r} manifest"
                    ) from error
                raise

    async def _read(self, client: Any, name: str, destination: Path | None, limit: int) -> bytes:
        response = await client.get_object(Bucket=self._bucket, Key=name)
        received = 0
        data = bytearray()
        async with response["Body"] as body:
            length = response.get("ContentLength")
            if isinstance(length, int) and length > limit:
                raise SandboxArtifactError("stored artifact exceeds its size limit")
            if destination is None:
                async for chunk in body.iter_chunks(65536):
                    received += len(chunk)
                    if received > limit:
                        raise SandboxArtifactError("stored artifact exceeds its size limit")
                    data.extend(chunk)
                return bytes(data)
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as file:
                async for chunk in body.iter_chunks(1024 * 1024):
                    received += len(chunk)
                    if received > limit:
                        raise SandboxArtifactError("stored artifact exceeds its size limit")
                    await asyncio.to_thread(file.write, chunk)
                await asyncio.to_thread(file.flush)
        return b""

    @override
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        """Download the manifest, then stream at most ``max_bytes`` of the archive."""
        name, record = self._names(key)
        async with self._client() as client:
            try:
                raw = await self._read(client, record, None, MAX_MANIFEST_BYTES)
            except SandboxArtifactError:
                raise
            except Exception as error:
                if _code(error) in _MISSING:
                    raise SandboxArtifactError(f"artifact {key!r} does not exist") from error
                raise SandboxArtifactError(f"artifact {key!r} manifest is unreadable") from error
            try:
                manifest = ArtifactManifest.from_json(raw)
            except ValueError as error:
                raise SandboxArtifactError(f"artifact {key!r} manifest is malformed") from error
            if manifest.size > max_bytes:
                raise SandboxArtifactError("stored artifact exceeds its size limit")
            try:
                await self._read(client, name, destination, max_bytes)
            except SandboxArtifactError:
                raise
            except Exception as error:
                raise SandboxArtifactError(f"artifact {key!r} archive is unreadable") from error
        return manifest

    @override
    async def exists(self, key: str) -> bool:
        """Report whether the manifest object exists."""
        _, record = self._names(key)
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=record)
            except Exception as error:
                if _code(error) in _MISSING:
                    return False
                raise SandboxArtifactError(f"could not check artifact {key!r}") from error
        return True

    @override
    async def delete(self, key: str) -> bool:
        """Delete the manifest first so a concurrent fetch never sees a lone archive."""
        existed = await self.exists(key)
        name, record = self._names(key)
        async with self._client() as client:
            try:
                await client.delete_object(Bucket=self._bucket, Key=record)
                await client.delete_object(Bucket=self._bucket, Key=name)
            except Exception as error:
                raise SandboxArtifactError(f"could not delete artifact {key!r}") from error
        return existed
