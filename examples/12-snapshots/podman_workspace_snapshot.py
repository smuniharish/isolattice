"""Persist a workspace across executions, snapshot it and restore it into a new instance."""

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


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    policy = SandboxPolicy(
        workspace=True,
        required_capabilities=(Capability.WORKSPACE, Capability.SNAPSHOTS),
    )
    with tempfile.TemporaryDirectory() as directory:
        store = LocalArtifactStore(directory)
        key = "workspace.tar"
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as first:
                await first.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", "echo 'state from run 1' > notes.txt"),
                        cwd="/workspace",
                    )
                )
                again = await first.run(
                    ExecutionRequest(argv=("/bin/cat", "notes.txt"), cwd="/workspace")
                )
                print(f"Second execution reads: {again.stdout.decode().strip()}")
                manifest = await first.snapshot(key)
                print(f"Snapshot sha256: {manifest.sha256[:16]}...")
            async with await sandbox.create(policy=policy, workspace_snapshot=key) as second:
                restored = await second.run(
                    ExecutionRequest(argv=("/bin/cat", "/workspace/notes.txt"))
                )
                if restored.stdout != b"state from run 1\n":
                    raise RuntimeError("the workspace snapshot was not restored")
                print(f"New instance reads: {restored.stdout.decode().strip()}")
    print("Owned volumes removed: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
