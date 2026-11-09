"""Live Podman instance features: scratch caps, workspaces, snapshots, pod budgets, CRIU.

Every assertion observes real kernel or engine behavior. Rootful tests touch only
resources labeled and named for the sandbox they create.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionHandle,
    ExecutionRequest,
    LocalArtifactStore,
    PodmanBackend,
    ResourceBudget,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    TerminationPolicy,
)

pytestmark = pytest.mark.podman

_TERMINATION = TerminationPolicy(verification_interval=0.1)


async def _drain_and_wait(handle: ExecutionHandle) -> None:
    async for _ in handle.output():
        pass
    await handle.wait()


def _owned_leftovers(connection: str, sandbox_id: str) -> list[str]:
    executable = shutil.which("podman")
    assert executable is not None
    found: list[str] = []
    queries: list[tuple[str, ...]] = [
        ("ps", "-a", "--filter", f"label=io.isolattice.sandbox={sandbox_id}"),
        ("ps", "-a", "--filter", f"name={sandbox_id}"),
        ("pod", "ps", "--filter", f"label=io.isolattice.sandbox={sandbox_id}"),
        ("pod", "ps", "--filter", f"name={sandbox_id}"),
        ("volume", "ls", "--filter", f"label=io.isolattice.sandbox={sandbox_id}"),
    ]
    for query in queries:
        output = subprocess.run(
            [executable, f"--connection={connection}", *query, "-q"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
        found.extend(f"{query[0]}:{line}" for line in output.decode().split())
    return found


def test_scratch_tmpfs_is_size_capped(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image)
        report = await configured.discover()
        assert report.capabilities[Capability.DISK_LIMIT].state is CapabilityState.SUPPORTED
        policy = SandboxPolicy(
            scratch_bytes=1048576,
            required_capabilities=(Capability.DISK_LIMIT,),
            termination=_TERMINATION,
        )
        async with (
            Sandbox(backend=configured) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            sandbox_id = instance.sandbox_id
            result = await instance.run(
                ExecutionRequest(
                    argv=(
                        "/bin/sh",
                        "-c",
                        "dd if=/dev/zero of=/tmp/fill bs=1024 count=2048 2>/dev/null; "
                        "echo rc=$?; wc -c < /tmp/fill; "
                        "touch /root-write 2>/dev/null; echo root=$?",
                    )
                )
            )
            assert result.exit_code == 0, result.stderr
            lines = result.stdout.decode().split()
            assert lines[0] != "rc=0"
            assert int(lines[1]) <= 1048576
            assert lines[2] != "root=0"
        assert _owned_leftovers(connection, sandbox_id) == []

    asyncio.run(check())


def test_workspace_persists_and_snapshot_restores(
    podman_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_config
    policy = SandboxPolicy(
        workspace=True,
        required_capabilities=(Capability.WORKSPACE, Capability.SNAPSHOTS),
        termination=_TERMINATION,
    )

    async def check() -> None:
        key = "workspace.tar"
        store = LocalArtifactStore(tmp_path)
        configured = PodmanBackend(connection=connection, image=image)
        async with Sandbox(backend=configured, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as first:
                first_id = first.sandbox_id
                assert first.workspace_path == "/workspace"
                written = await first.run(
                    ExecutionRequest(
                        argv=(
                            "/bin/sh",
                            "-c",
                            "mkdir -p nested && printf persisted > nested/data.txt && "
                            "ln -s nested/data.txt link",
                        ),
                        cwd="/workspace",
                    )
                )
                assert written.exit_code == 0, written.stderr
                read = await first.run(
                    ExecutionRequest(argv=("/bin/cat", "/workspace/link"), cwd="/workspace")
                )
                assert read.stdout == b"persisted"
                manifest = await first.snapshot(key)
                assert manifest.backend == "podman"
                assert await store.exists(key)
            assert _owned_leftovers(connection, first_id) == []
            async with await sandbox.create(policy=policy, workspace_snapshot=key) as second:
                second_id = second.sandbox_id
                restored = await second.run(
                    ExecutionRequest(
                        argv=(
                            "/bin/sh",
                            "-c",
                            "cat /workspace/nested/data.txt; printf more >> /workspace/new.txt",
                        )
                    )
                )
                assert restored.exit_code == 0, restored.stderr
                assert restored.stdout == b"persisted"
            assert _owned_leftovers(connection, second_id) == []

    asyncio.run(check())


def test_rootless_instance_budget_is_rejected(
    podman_resources_config: tuple[str, str, bool],
) -> None:
    connection, image, rootful = podman_resources_config
    if rootful:
        pytest.skip("the resource connection is rootful")

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image)
        report = await configured.discover()
        evidence = report.capabilities[Capability.INSTANCE_LIMITS]
        assert evidence.state is CapabilityState.UNSUPPORTED
        assert "rootless" in evidence.detail
        async with Sandbox(backend=configured) as sandbox:
            with pytest.raises(SandboxCapabilityError, match="rootful"):
                await sandbox.create(
                    policy=SandboxPolicy(
                        instance_budget=ResourceBudget(memory_bytes=67108864),
                    )
                )

    asyncio.run(check())


def test_rootless_checkpoints_are_rejected(podman_config: tuple[str, str]) -> None:
    connection, image = podman_config

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image)
        report = await configured.discover()
        assert report.capabilities[Capability.CHECKPOINTS].state is CapabilityState.UNSUPPORTED
        async with Sandbox(backend=configured) as sandbox:
            with pytest.raises(SandboxCapabilityError, match="rootful"):
                await sandbox.create(
                    policy=SandboxPolicy(
                        required_capabilities=(Capability.CHECKPOINTS,),
                    )
                )

    asyncio.run(check())


def test_rootful_pod_budget_is_shared_by_executions(
    podman_rootful_config: tuple[str, str],
) -> None:
    connection, image = podman_rootful_config

    async def check() -> None:
        configured = PodmanBackend(connection=connection, image=image, allow_rootful=True)
        report = await configured.discover()
        assert report.capabilities[Capability.INSTANCE_LIMITS].state is CapabilityState.SUPPORTED
        policy = SandboxPolicy(
            instance_budget=ResourceBudget(memory_bytes=67108864, cpu_cores=0.5),
            scratch_bytes=50331648,
            required_capabilities=(Capability.INSTANCE_LIMITS,),
            execution_timeout=60,
            termination=_TERMINATION,
        )
        # Each execution alone fits the 64 MiB pod budget; together they exceed it.
        fill = ExecutionRequest(
            argv=(
                "/bin/sh",
                "-c",
                "dd if=/dev/zero of=/tmp/fill bs=1048576 count=40 2>/dev/null || exit 3; sleep 4",
            )
        )
        async with (
            Sandbox(backend=configured) as sandbox,
            await sandbox.create(policy=policy) as instance,
        ):
            sandbox_id = instance.sandbox_id
            alone = await instance.run(fill)
            assert alone.exit_code == 0, alone.stderr
            first = await instance.start(fill)
            second = await instance.start(fill)
            results = await asyncio.gather(first.wait(), second.wait())
            codes = sorted(result.exit_code for result in results)
            # The shared cgroup refuses the combined fill (tmpfs ENOMEM) or OOM-kills.
            assert codes != [0, 0], codes
            assert set(codes) <= {0, 3, 137}, codes
            assert all(result.termination.resources_released for result in results)
        assert _owned_leftovers(connection, sandbox_id) == []

    asyncio.run(check())


def test_rootful_checkpoint_restores_into_new_instance(
    podman_rootful_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_rootful_config
    policy = SandboxPolicy(
        required_capabilities=(Capability.CHECKPOINTS,),
        execution_timeout=120,
        termination=_TERMINATION,
    )
    counter = ExecutionRequest(
        argv=(
            "/bin/sh",
            "-c",
            "i=0; while [ $i -lt 40 ]; do echo n$i; echo e$i >&2; i=$((i+1)); sleep 0.2; done; "
            "exit 7",
        )
    )

    async def check() -> None:
        key = "execution.ckpt"
        configured = PodmanBackend(connection=connection, image=image, allow_rootful=True)
        report = await configured.discover()
        assert report.capabilities[Capability.CHECKPOINTS].state is CapabilityState.LIMITED
        store = LocalArtifactStore(tmp_path)
        async with Sandbox(backend=configured, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as source:
                source_id = source.sandbox_id
                handle = await source.start(counter)
                stream = handle.output()
                seen = b""
                async for chunk in stream:
                    if chunk.stream == "stdout":
                        seen += chunk.data
                    if b"n5\n" in seen:
                        break
                manifest = await handle.checkpoint(key)
                assert manifest.backend == "podman"
                async for _ in stream:
                    pass
                original = await handle.wait()
                assert original.exit_code == 7
                assert original.stdout.decode().split() == [f"n{index}" for index in range(40)]
            assert _owned_leftovers(connection, source_id) == []
            async with await sandbox.create(policy=policy) as target:
                target_id = target.sandbox_id
                resumed = await target.restore(key)
                async for _ in resumed.output():
                    pass
                restored = await resumed.wait()
            assert _owned_leftovers(connection, target_id) == []
        assert restored.exit_code == 7, restored.stderr
        lines = restored.stdout.decode().split()
        errors = restored.stderr.decode().split()
        first = int(lines[0][1:])
        assert first >= 6
        assert lines == [f"n{index}" for index in range(first, 40)]
        assert errors[-1] == "e39"
        assert len(errors) <= len(lines) + 1

    asyncio.run(check())


def test_restore_rejects_foreign_archive(
    podman_rootful_config: tuple[str, str], tmp_path: Path
) -> None:
    connection, image = podman_rootful_config
    archive = tmp_path / "foreign.tar.gz"
    executable = shutil.which("podman")
    assert executable is not None
    name = f"isolattice-foreign-{os.getpid()}"
    command = [executable, f"--connection={connection}"]
    # A container without the hardened shape (default capabilities, root user).
    subprocess.run(
        [
            *command,
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "io.isolattice.sandbox=foreign-test",
            "--log-driver",
            "k8s-file",
            image,
            "sleep",
            "60",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    try:
        checkpointed = subprocess.run(
            [*command, "container", "checkpoint", "--export", str(archive), name],
            check=False,
            capture_output=True,
            timeout=120,
        )
        # The disposable foreign container holds no workload data, so show the engine error.
        assert checkpointed.returncode == 0, checkpointed.stderr.decode(errors="replace")
    finally:
        subprocess.run(
            [*command, "rm", "-f", "-t", "0", name], check=True, capture_output=True, timeout=60
        )

    async def check() -> None:
        from isolattice import SandboxArtifactError
        from isolattice._artifacts import seal
        from isolattice.models import ArtifactKind

        key = "foreign.ckpt"
        manifest = await seal(
            ArtifactKind.EXECUTION_CHECKPOINT, "podman", "backend-checkpoint", archive, key
        )
        store_root = tmp_path / "store"
        store_root.mkdir()
        store = LocalArtifactStore(store_root)
        await store.publish(key, archive, manifest)
        configured = PodmanBackend(connection=connection, image=image, allow_rootful=True)
        async with Sandbox(backend=configured, artifact_store=store) as sandbox:
            async with await sandbox.create(
                policy=SandboxPolicy(
                    required_capabilities=(Capability.CHECKPOINTS,),
                    termination=_TERMINATION,
                )
            ) as target:
                target_id = target.sandbox_id
                handle = await target.restore(key)
                with pytest.raises(SandboxArtifactError, match="unprivileged user"):
                    await _drain_and_wait(handle)
            assert _owned_leftovers(connection, target_id) == []

    asyncio.run(check())
