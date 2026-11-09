"""Verify cgroup v2 budgets on an explicitly configured, capable Podman engine."""

import argparse
import asyncio

from isolattice import Capability, ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main(connection: str, image: str, allow_rootful: bool = False) -> None:
    backend = PodmanBackend(connection=connection, image=image, allow_rootful=allow_rootful)
    policy = SandboxPolicy(
        memory_bytes=64 * 1024 * 1024,
        cpu_cores=0.25,
        process_limit=8,
        execution_timeout=60,
        required_capabilities=(
            Capability.MEMORY_LIMIT,
            Capability.CPU_LIMIT,
            Capability.PROCESS_LIMIT,
        ),
    )
    async with (
        Sandbox(backend=backend) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        result = await instance.run(
            ExecutionRequest(
                argv=(
                    "/bin/sh",
                    "-c",
                    "/bin/cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max "
                    "/sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max",
                )
            )
        )
        if (
            result.exit_code != 0
            or result.stdout != b"67108864\n0\n25000 100000\n8\n"
            or not result.termination.resources_released
        ):
            raise RuntimeError("kernel budgets or owned cleanup were not verified")
        print("Kernel memory.max: 67108864")
        print("Kernel memory.swap.max: 0")
        print("Kernel cpu.max: 25000 100000")
        print("Kernel pids.max: 8")
        print("Owned container released: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument(
        "--image", required=True, help="Local image containing /bin/sh and /bin/cat"
    )
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Explicit privileged-engine opt-in"
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image, arguments.allow_rootful))
