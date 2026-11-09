"""Snapshot a running container's root filesystem and start a new instance from it."""

import argparse
import asyncio
import tempfile

from isolattice import (
    Capability,
    ExecutionRequest,
    LocalArtifactStore,
    PodmanBackend,
    Sandbox,
    SandboxPolicy,
)

# The workload writes into its writable container layer, then waits to be snapshotted.
_WRITER = (
    "mkdir -p /var/tmp/app && echo 'model weights v1' > /var/tmp/app/state.txt && "
    "echo ready && sleep 60"
)


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    writable = SandboxPolicy(
        read_only_root=False,
        required_capabilities=(Capability.SNAPSHOTS,),
    )
    with tempfile.TemporaryDirectory() as directory:
        store = LocalArtifactStore(directory)
        key = "root.tar"
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=writable) as source:
                handle = await source.start(ExecutionRequest(argv=("/bin/sh", "-c", _WRITER)))
                stream = handle.output()
                async for chunk in stream:
                    if chunk.stream == "stdout" and b"ready" in chunk.data:
                        break
                else:
                    raise RuntimeError("the workload exited before it was ready")
                manifest = await handle.snapshot_container(key)
                print(f"Snapshot of the running workload: {manifest.size} bytes")
                print(f"Snapshot sha256: {manifest.sha256[:16]}...")
                await handle.cancel()
                async for _ in stream:
                    pass
            # The restored instance keeps the default read-only root.
            restored_policy = SandboxPolicy()
            async with await sandbox.create(
                policy=restored_policy, container_snapshot=key
            ) as target:
                result = await target.run(
                    ExecutionRequest(
                        argv=(
                            "/bin/sh",
                            "-c",
                            "cat /var/tmp/app/state.txt; "
                            "touch /var/tmp/app/new 2>/dev/null && echo writable || echo read-only",
                        )
                    )
                )
                lines = result.stdout.decode().splitlines()
                if lines != ["model weights v1", "read-only"]:
                    raise RuntimeError(f"the container snapshot was not restored: {lines!r}")
                print(f"New instance reads: {lines[0]}")
                print(f"Restored root is: {lines[1]}")
    print("Owned containers and imported image removed: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
