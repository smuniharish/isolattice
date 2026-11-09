"""Live Podman whole-container snapshots: pause, export, verified import and cleanup.

Rootful tests touch only containers, pods, volumes and images labeled for the
sandbox they create.
"""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from isolattice import (
    ArtifactKind,
    Capability,
    CapabilityState,
    ExecutionRequest,
    LocalArtifactStore,
    PodmanBackend,
    Sandbox,
    SandboxArtifactError,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxStateError,
    TerminationPolicy,
)
from isolattice._artifacts import seal

pytestmark = pytest.mark.podman

_TERMINATION = TerminationPolicy(verification_interval=0.1)
_POLICY = SandboxPolicy(
    read_only_root=False,
    required_capabilities=(Capability.SNAPSHOTS,),
    execution_timeout=120,
    termination=_TERMINATION,
)
_WRITER = ExecutionRequest(
    argv=(
        "/bin/sh",
        "-c",
        "mkdir -p /var/tmp/state && printf captured > /var/tmp/state/data.txt && "
        "ln -s state/data.txt /var/tmp/link && echo ready && sleep 60",
    )
)


def _owned(connection: str, sandbox_id: str) -> list[str]:
    executable = shutil.which("podman")
    assert executable is not None
    label = f"label=io.isolattice.sandbox={sandbox_id}"
    found: list[str] = []
    for query in (
        ("ps", "-a", "--filter", label),
        ("pod", "ps", "--filter", label),
        ("volume", "ls", "--filter", label),
        ("image", "ls", "--all", "--filter", label),
    ):
        output = subprocess.run(
            [executable, f"--connection={connection}", *query, "-q"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        found.extend(f"{query[0]}:{line}" for line in output.decode().split())
    return found


def _states(connection: str, sandbox_id: str) -> list[str]:
    executable = shutil.which("podman")
    assert executable is not None
    output = subprocess.run(
        [
            executable,
            f"--connection={connection}",
            "ps",
            "--filter",
            f"label=io.isolattice.sandbox={sandbox_id}",
            "--format",
            "{{.State}}",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    return output.decode().split()


async def _snapshot_running(sandbox: Sandbox, key: str, connection: str) -> str:
    async with await sandbox.create(policy=_POLICY) as source:
        handle = await source.start(_WRITER)
        stream = handle.output()
        errors = b""
        async for chunk in stream:
            if chunk.stream == "stderr":
                errors += chunk.data
            if chunk.stream == "stdout" and b"ready" in chunk.data:
                break
        else:
            pytest.fail(f"the writer exited before it was ready: {errors!r}")
        manifest = await handle.snapshot_container(key)
        assert manifest.backend == "podman"
        assert manifest.format == "rootfs-tar"
        assert _states(connection, source.sandbox_id) == ["running"]
        await handle.cancel()
        async for _ in stream:
            pass
        source_id = source.sandbox_id
    assert _owned(connection, source_id) == []
    return source_id


async def _check_round_trip(
    configured: PodmanBackend, connection: str, store: LocalArtifactStore
) -> None:
    report = await configured.discover()
    assert report.capabilities[Capability.SNAPSHOTS].state is CapabilityState.SUPPORTED
    assert "root-filesystem" in report.capabilities[Capability.SNAPSHOTS].detail
    async with Sandbox(backend=configured, artifact_store=store) as sandbox:
        await _snapshot_running(sandbox, "root.tar", connection)
        restored_policy = SandboxPolicy(termination=_TERMINATION)
        async with await sandbox.create(
            policy=restored_policy, container_snapshot="root.tar"
        ) as target:
            target_id = target.sandbox_id
            assert any(item.startswith("image:") for item in _owned(connection, target_id))
            for _ in range(2):
                result = await target.run(
                    ExecutionRequest(
                        argv=(
                            "/bin/sh",
                            "-c",
                            "cat /var/tmp/link; touch /var/tmp/new 2>/dev/null; echo root=$?",
                        )
                    )
                )
                assert result.exit_code == 0, result.stderr
                # The restored root is read-only and still holds the snapshotted files.
                assert result.stdout == b"capturedroot=1\n"
        assert _owned(connection, target_id) == []


def test_rootless_container_snapshot_restores(
    podman_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_config
    asyncio.run(
        _check_round_trip(
            PodmanBackend(connection=connection, image=image),
            connection,
            LocalArtifactStore(tmp_path),
        )
    )


def test_rootful_container_snapshot_restores(
    podman_rootful_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_rootful_config
    asyncio.run(
        _check_round_trip(
            PodmanBackend(connection=connection, image=image, allow_rootful=True),
            connection,
            LocalArtifactStore(tmp_path),
        )
    )


def test_tampered_container_snapshot_is_rejected_before_import(
    podman_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_config
    archive = tmp_path / "root.tar"
    store = LocalArtifactStore(tmp_path)

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image)
        async with Sandbox(backend=configured, artifact_store=store) as sandbox:
            await _snapshot_running(sandbox, "root.tar", connection)
            # Seal an intact copy as the wrong kind before tampering with the original.
            intact = tmp_path / "intact.tar"
            shutil.copyfile(archive, intact)
            wrong_kind = await seal(
                ArtifactKind.WORKSPACE_SNAPSHOT, "podman", "tar", intact, "workspace-kind.tar"
            )
            await store.publish("workspace-kind.tar", intact, wrong_kind)
            data = bytearray(archive.read_bytes())
            data[len(data) // 2] ^= 0xFF
            archive.write_bytes(bytes(data))
            with pytest.raises(SandboxArtifactError, match="integrity manifest"):
                await sandbox.create(policy=_POLICY, container_snapshot="root.tar")
            with pytest.raises(SandboxArtifactError, match="not a container-snapshot"):
                await sandbox.create(policy=_POLICY, container_snapshot="workspace-kind.tar")

    asyncio.run(check())


def test_container_snapshot_requires_running_execution(
    podman_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_config

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image)
        store = LocalArtifactStore(tmp_path)
        async with Sandbox(backend=configured, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=_POLICY) as instance:
                handle = await instance.start(ExecutionRequest(argv=("/bin/true",)))
                async for _ in handle.output():
                    pass
                await handle.wait()
                with pytest.raises(SandboxStateError, match="running"):
                    await handle.snapshot_container("late.tar")
                sandbox_id = instance.sandbox_id
            assert _owned(connection, sandbox_id) == []
            assert not await store.exists("late.tar")
            with pytest.raises(SandboxCapabilityError, match="quota"):
                await sandbox.create(
                    policy=SandboxPolicy(
                        read_only_root=False,
                        scratch_bytes=1048576,
                        required_capabilities=(Capability.DISK_LIMIT,),
                    )
                )

    asyncio.run(check())
