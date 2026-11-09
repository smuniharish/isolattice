"""Run a limited workload through an explicitly configured Docker API endpoint."""

import argparse
import asyncio

from isolattice import (
    Capability,
    DockerBackend,
    ExecutionRequest,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
)

PROBE = (
    'printf "uid=%s\\n" "$(id -u)"; '
    """awk '/^CapEff:/ { print "caps=" $2 }' /proc/self/status; """
    'IFS= read -r memory < /sys/fs/cgroup/memory.max; printf "memory.max=%s\\n" "$memory"; '
    'IFS= read -r pids < /sys/fs/cgroup/pids.max; printf "pids.max=%s\\n" "$pids"; '
    "if touch /root-write 2>/dev/null; then echo root=writable; else echo root=read-only; fi; "
    'IFS= read -r line; printf "stdin=%s\\n" "$line"; '
    'printf "greeting=%s\\n" "$GREETING"'
)


async def main(host: str, image: str, executable: str | None, allow_rootful: bool) -> None:
    backend = DockerBackend(
        host=host, image=image, executable=executable, allow_rootful=allow_rootful
    )
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        if not report.available:
            raise RuntimeError(f"Docker backend is unavailable: {report.error}")
        print("Backend:", report.name)
        print("Engine:", report.limitations[-1])
        print("Qualification:", report.limitations[0])
        try:
            await sandbox.create(
                policy=SandboxPolicy(
                    workspace=True,
                    required_capabilities=(Capability.WORKSPACE,),
                )
            )
        except SandboxCapabilityError:
            print("Unsupported workspace: rejected")
        else:
            raise RuntimeError("an unimplemented Docker workspace was accepted")
        policy = SandboxPolicy(
            memory_bytes=64 * 1024 * 1024,
            process_limit=16,
            required_capabilities=(Capability.MEMORY_LIMIT, Capability.PROCESS_LIMIT),
            execution_timeout=120,
        )
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(
                    argv=("/bin/sh", "-c", PROBE),
                    stdin=b"from the host\n",
                    environment={"GREETING": "hello"},
                )
            )
            expected = (
                b"uid=65534\n",
                b"caps=0000000000000000\n",
                b"memory.max=67108864\n",
                b"pids.max=16\n",
                b"root=read-only\n",
                b"stdin=from the host\n",
                b"greeting=hello\n",
            )
            if (
                result.exit_code != 0
                or any(line not in result.stdout for line in expected)
                or result.termination.state != "terminated"
                or not result.termination.resources_released
            ):
                raise RuntimeError("Docker execution, isolation or owned cleanup failed")
            print(result.stdout.decode(), end="")
            print("Owned container released:", result.termination.resources_released)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Explicit Docker API endpoint")
    parser.add_argument("--image", required=True, help="Already-local image containing /bin/sh")
    parser.add_argument("--executable", help="Docker CLI path when not on PATH")
    parser.add_argument(
        "--allow-rootful", action="store_true", help="Accept an explicitly rootful engine"
    )
    arguments = parser.parse_args()
    asyncio.run(
        main(arguments.host, arguments.image, arguments.executable, arguments.allow_rootful)
    )
