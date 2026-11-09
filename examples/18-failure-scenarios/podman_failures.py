"""Check fail-closed policies and verified cleanup of a timed-out benign workload."""

import argparse
import asyncio

from isolattice import (
    ExecutionRequest,
    NetworkAccess,
    PodmanBackend,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
)


async def main(connection: str, image: str) -> None:
    backend = PodmanBackend(connection=connection, image=image)
    async with Sandbox(backend=backend) as sandbox:
        try:
            await sandbox.create(policy=SandboxPolicy(network=NetworkAccess.ALLOWLIST))
        except SandboxCapabilityError:
            print("Unsupported network policy: rejected")
        else:
            raise RuntimeError("unsupported network policy was accepted")
        async with await sandbox.create(policy=SandboxPolicy(execution_timeout=60)) as instance:
            try:
                await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "sleep 60"), timeout=12))
            except SandboxTimeoutError as error:
                if error.report is None or not error.report.resources_released:
                    raise RuntimeError("timeout did not verify owned cleanup") from error
                print("Timeout cleanup:", error.report.state)
                print("Owned container released:", error.report.resources_released)
            else:
                raise RuntimeError("workload did not time out")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image))
