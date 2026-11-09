"""Stream a benign command, then force termination of its owned container."""

import argparse
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main(connection: str, image: str) -> None:
    backend = PodmanBackend(connection=connection, image=image)
    policy = SandboxPolicy()
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        handle = await instance.start(
            ExecutionRequest(argv=("/bin/sh", "-c", "echo READY; sleep 60"))
        )
        stream = handle.output()
        ready = False
        tail = b""
        try:
            async for chunk in stream:
                print(f"{chunk.stream}: {chunk.data.decode().rstrip()}")
                if chunk.stream == "stdout":
                    combined = tail + chunk.data
                    if b"READY\n" in combined:
                        ready = True
                        break
                    tail = combined[-5:]
        finally:
            await stream.aclose()
        if not ready:
            raise RuntimeError("execution ended before its readiness marker")
        report = await handle.cancel(force=True)
        if report is None or report.state != "terminated" or not report.resources_released:
            raise RuntimeError("execution did not produce verified owned cleanup")
        print("Termination:", report.state)
        print("Owned container released:", report.resources_released)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Explicit rootless Podman connection")
    parser.add_argument("--image", required=True, help="Already-local image containing /bin/sh")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image))
