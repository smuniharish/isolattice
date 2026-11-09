"""Verify benign stdin, environment, cwd, concurrency and bounded output."""

import argparse
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main(connection: str, image: str) -> None:
    policy = SandboxPolicy(execution_timeout=60, stdout_limit=8, stderr_limit=4)
    async with (
        Sandbox(backend=PodmanBackend(connection=connection, image=image)) as sandbox,
        await sandbox.create(policy=policy) as instance,
    ):
        first, second = await asyncio.gather(
            instance.run(
                ExecutionRequest(
                    argv=(
                        "/bin/sh",
                        "-c",
                        'IFS= read -r line; [ "$line" = payload ] && '
                        '[ "$GREETING" = hello ] && [ "$PWD" = /tmp ] || exit 96; '
                        "printf received; printf warn >&2",
                    ),
                    stdin=b"payload\n",
                    environment={"GREETING": "hello"},
                    cwd="/tmp",  # noqa: S108 - a directory inside the container
                )
            ),
            instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "printf abcdefghijklmnop"))),
        )
        if (
            first.exit_code != 0
            or first.stdout != b"received"
            or first.stderr != b"warn"
            or second.exit_code != 0
            or second.stdout != b"abcdefgh"
            or not second.stdout_truncated
            or first.termination.boundary_id == second.termination.boundary_id
            or not first.termination.resources_released
            or not second.termination.resources_released
        ):
            raise RuntimeError("execution contracts or owned cleanup failed")
        print("stdin/environment/cwd: verified")
        print("Bounded capture:", second.stdout.decode())
        print("Concurrent boundaries: distinct")
        print("Owned container released: True")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True, help="Local image containing /bin/sh")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image))
