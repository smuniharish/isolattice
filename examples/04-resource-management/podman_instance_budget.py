"""Share one memory and CPU budget across all executions of an instance (rootful Podman)."""

import argparse
import asyncio

from isolattice import (
    Capability,
    ExecutionRequest,
    PodmanBackend,
    ResourceBudget,
    Sandbox,
    SandboxPolicy,
)

FILL = ExecutionRequest(
    argv=(
        "/bin/sh",
        "-c",
        "dd if=/dev/zero of=/tmp/fill bs=1048576 count=40 2>/dev/null || exit 3; sleep 4",
    )
)


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    policy = SandboxPolicy(
        instance_budget=ResourceBudget(memory_bytes=64 * 1024 * 1024, cpu_cores=0.5),
        scratch_bytes=48 * 1024 * 1024,
        execution_timeout=60,
        required_capabilities=(Capability.INSTANCE_LIMITS,),
    )
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        alone = await instance.run(FILL)
        if alone.exit_code != 0:
            raise RuntimeError("a single 40 MiB fill should fit the 64 MiB instance budget")
        print("Single 40 MiB fill: fits the 64 MiB instance budget")
        handles = [await instance.start(FILL), await instance.start(FILL)]
        results = await asyncio.gather(*(handle.wait() for handle in handles))
        codes = sorted(result.exit_code for result in results)
        if codes == [0, 0] or not set(codes) <= {0, 3, 137}:
            raise RuntimeError(f"the shared instance budget was not enforced: {codes}")
        print("Two concurrent fills: refused by the shared budget")
        released = all(result.termination.resources_released for result in results)
        print(f"Owned containers released: {released}")
    print("Owned pod removed: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True, help="Explicit rootful connection")
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh and dd")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
