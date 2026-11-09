"""Run a benign command using an explicitly configured rootless Podman engine."""

import argparse
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main(connection: str, image: str) -> None:
    backend = PodmanBackend(connection=connection, image=image)
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        if not report.available:
            raise RuntimeError(f"Podman backend is unavailable: {report.error}")
        print("Backend:", report.name)
        print("Qualification:", report.limitations[0])
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(argv=("/bin/sh", "-c", "printf 'Hello from Isolattice\\n'"))
            )
            if (
                result.exit_code != 0
                or result.stdout != b"Hello from Isolattice\n"
                or result.termination.state != "terminated"
                or not result.termination.resources_released
            ):
                raise RuntimeError("hello-world execution or owned cleanup failed")
            print(result.stdout.decode(), end="")
            print("Exit code:", result.exit_code)
            print("Owned container released:", result.termination.resources_released)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Explicit rootless Podman connection")
    parser.add_argument("--image", required=True, help="Already-local image containing /bin/sh")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image))
