"""Live S3-compatible artifact storage: conformance, multipart, tampering and credentials.

Every test writes below a unique key prefix in an existing bucket and removes
every object under that prefix afterwards. Nothing else in the bucket is touched.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    ArtifactKind,
    Capability,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxArtifactError,
    SandboxPolicy,
    TerminationPolicy,
)
from isolattice._artifacts import WORKSPACE_FORMAT, seal
from isolattice.s3 import S3ArtifactStore, S3Credentials
from isolattice.testing import check_artifact_store

pytestmark = pytest.mark.s3

_PART = 5 * 1024 * 1024
_MAX_BYTES = 64 * 1024 * 1024


async def _client_call(config: tuple[str, str, str, str], operation: str, **arguments: Any) -> Any:
    from aiobotocore.session import get_session

    endpoint, _, access_key_id, secret_access_key = config
    async with get_session().create_client(
        "s3",
        endpoint_url=endpoint,
        region_name="us-east-1",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
    ) as client:
        response = await getattr(client, operation)(**arguments)
        if operation == "get_object":
            async with response["Body"] as body:
                return await body.read()
        return response


async def _objects(config: tuple[str, str, str, str], prefix: str) -> list[str]:
    response = await _client_call(config, "list_objects_v2", Bucket=config[1], Prefix=prefix)
    return [item["Key"] for item in response.get("Contents", [])]


async def _remove_prefix(config: tuple[str, str, str, str], prefix: str) -> None:
    for name in await _objects(config, prefix):
        await _client_call(config, "delete_object", Bucket=config[1], Key=name)


@pytest.fixture
def s3_prefix(s3_config: tuple[str, str, str, str]) -> Iterator[str]:
    prefix = f"isolattice-test/{uuid.uuid4().hex}"
    yield prefix
    asyncio.run(_remove_prefix(s3_config, prefix + "/"))
    assert asyncio.run(_objects(s3_config, prefix + "/")) == []


def _store(
    config: tuple[str, str, str, str], prefix: str, *, secret: str | None = None
) -> S3ArtifactStore:
    endpoint, bucket, access_key_id, secret_access_key = config
    return S3ArtifactStore(
        bucket=bucket,
        prefix=prefix,
        endpoint_url=endpoint,
        region_name="us-east-1",
        credentials=S3Credentials(access_key_id, secret or secret_access_key),
        part_size=_PART,
    )


def test_store_meets_the_artifact_store_contract(
    s3_config: tuple[str, str, str, str], s3_prefix: str, tmp_path: Path
) -> None:
    asyncio.run(check_artifact_store(_store(s3_config, s3_prefix), tmp_path))
    assert asyncio.run(_objects(s3_config, s3_prefix + "/")) == []


def test_multipart_archive_round_trips_byte_for_byte(
    s3_config: tuple[str, str, str, str], s3_prefix: str, tmp_path: Path
) -> None:
    async def check() -> None:
        store = _store(s3_config, s3_prefix)
        archive = tmp_path / "archive"
        payload = os.urandom(2 * _PART + 12345)
        archive.write_bytes(payload)
        manifest = await seal(
            ArtifactKind.WORKSPACE_SNAPSHOT, "test", WORKSPACE_FORMAT, archive, "big"
        )
        await store.publish("big", archive, manifest)
        assert sorted(await _objects(s3_config, s3_prefix + "/")) == [
            f"{s3_prefix}/big",
            f"{s3_prefix}/big.manifest.json",
        ]
        with pytest.raises(SandboxArtifactError, match="already exists"):
            await store.publish("big", archive, manifest)
        fetched = tmp_path / "fetched"
        assert await store.fetch("big", fetched, max_bytes=_MAX_BYTES) == manifest
        assert fetched.read_bytes() == payload
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("big", tmp_path / "bounded", max_bytes=_PART)
        assert await store.delete("big")
        assert not await store.exists("big")
        with pytest.raises(SandboxArtifactError, match="does not exist"):
            await store.fetch("big", tmp_path / "missing", max_bytes=_MAX_BYTES)

    asyncio.run(check())


def test_wrong_credentials_fail_closed_without_leaking_the_secret(
    s3_config: tuple[str, str, str, str], s3_prefix: str, tmp_path: Path
) -> None:
    secret = "wrong-" + uuid.uuid4().hex
    store = _store(s3_config, s3_prefix, secret=secret)

    async def check() -> None:
        archive = tmp_path / "archive"
        archive.write_bytes(b"data")
        manifest = await seal(
            ArtifactKind.WORKSPACE_SNAPSHOT, "test", WORKSPACE_FORMAT, archive, "key"
        )
        failures: list[SandboxArtifactError] = []
        for operation in (
            store.exists("key"),
            store.publish("key", archive, manifest),
            store.fetch("key", tmp_path / "fetched", max_bytes=_MAX_BYTES),
        ):
            with pytest.raises(SandboxArtifactError) as caught:
                await operation
            failures.append(caught.value)
        for failure in failures:
            assert secret not in str(failure)
            assert secret not in repr(failure.__cause__)
        assert secret not in repr(store)

    asyncio.run(check())
    assert asyncio.run(_objects(s3_config, s3_prefix + "/")) == []


@pytest.mark.podman
def test_podman_workspace_snapshot_round_trips_through_s3_and_detects_tampering(
    s3_config: tuple[str, str, str, str],
    s3_prefix: str,
    podman_config: tuple[str, str],
) -> None:
    connection, image = podman_config
    policy = SandboxPolicy(
        workspace=True,
        required_capabilities=(Capability.WORKSPACE, Capability.SNAPSHOTS),
        termination=TerminationPolicy(verification_interval=0.1),
    )

    async def check() -> None:
        store = _store(s3_config, s3_prefix)
        backend = PodmanBackend(connection=connection, image=image)
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as first:
                written = await first.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", "printf persisted > /workspace/data.txt")
                    )
                )
                assert written.exit_code == 0, written.stderr
                manifest = await first.snapshot("workspace.tar")
                assert manifest.kind is ArtifactKind.WORKSPACE_SNAPSHOT
            async with await sandbox.create(
                policy=policy, workspace_snapshot="workspace.tar"
            ) as second:
                restored = await second.run(
                    ExecutionRequest(argv=("/bin/cat", "/workspace/data.txt"))
                )
                assert restored.stdout == b"persisted"

            # Same size, different bytes: only the SHA-256 manifest can catch it.
            name = f"{s3_prefix}/workspace.tar"
            original = await _client_call(s3_config, "get_object", Bucket=s3_config[1], Key=name)
            tampered = bytearray(original)
            tampered[-1] ^= 0xFF
            await _client_call(
                s3_config, "put_object", Bucket=s3_config[1], Key=name, Body=bytes(tampered)
            )
            with pytest.raises(SandboxArtifactError, match="integrity manifest"):
                await sandbox.create(policy=policy, workspace_snapshot="workspace.tar")

    asyncio.run(check())
