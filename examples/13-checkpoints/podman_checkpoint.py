"""Checkpoint a running counter with CRIU and resume it in a new instance (rootful Podman)."""

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

# A CRIU dump of a running container takes seconds, so the counter runs long
# enough (about 10 s) for the checkpoint to capture it mid-count.
LAST = 99
COUNTER = ExecutionRequest(
    argv=(
        "/bin/sh",
        "-c",
        f"i=0; while [ $i -le {LAST} ]; do echo $i; i=$((i+1)); sleep 0.1; done",
    )
)


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    policy = SandboxPolicy(
        execution_timeout=120,
        required_capabilities=(Capability.CHECKPOINTS,),
    )
    with tempfile.TemporaryDirectory() as directory:
        store = LocalArtifactStore(directory)
        key = "counter.ckpt"
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            async with await sandbox.create(policy=policy) as source:
                handle = await source.start(COUNTER)
                stream = handle.output()
                seen = b""
                async for chunk in stream:
                    seen += chunk.data
                    if b"\n5\n" in seen:
                        break
                await handle.checkpoint(key)
                print("Checkpointed after the counter reached 5; the source keeps running")
                await handle.cancel()
                async for _ in stream:
                    pass
            async with await sandbox.create(policy=policy) as target:
                resumed = await target.restore(key)
                async for _ in resumed.output():
                    pass
                result = await resumed.wait()
        numbers = [int(value) for value in result.stdout.split()]
        if not numbers or numbers != list(range(numbers[0], LAST + 1)) or numbers[0] < 6:
            raise RuntimeError("the restored counter did not continue from its checkpoint")
        print(f"Restored execution continued from {numbers[0]} to {numbers[-1]}")
        print(f"Restored exit code: {result.exit_code}")
    print("Owned containers released: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Explicit rootful connection")
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
