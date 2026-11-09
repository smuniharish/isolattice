"""Run a limited workload as an owned Pod in an explicitly configured namespace."""

import argparse
import asyncio

from isolattice import (
    Capability,
    ExecutionRequest,
    KubernetesBackend,
    NetworkAccess,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
)

PROBE = (
    'printf "uid=%s\\n" "$(id -u)"; '
    """awk '/^CapEff:/ { print "caps=" $2 }' /proc/self/status; """
    'IFS= read -r memory < /sys/fs/cgroup/memory.max; printf "memory.max=%s\\n" "$memory"; '
    'IFS= read -r cpu < /sys/fs/cgroup/cpu.max; printf "cpu.max=%s\\n" "$cpu"; '
    "if touch /root-write 2>/dev/null; then echo root=writable; else echo root=read-only; fi; "
    "if dd if=/dev/zero of=/tmp/fill bs=4096 count=4096 2>/dev/null; "
    "then echo scratch=unbounded; else echo scratch=full; fi; "
    "if [ -e /var/run/secrets/kubernetes.io ]; then echo token=mounted; else echo token=none; fi; "
    'IFS= read -r line; printf "stdin=%s\\n" "$line"; '
    'printf "greeting=%s\\n" "$GREETING"'
)


async def main(
    kubeconfig: str, context: str, namespace: str, image: str, executable: str | None
) -> None:
    backend = KubernetesBackend(
        kubeconfig=kubeconfig,
        context=context,
        namespace=namespace,
        image=image,
        executable=executable,
    )
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        if not report.available:
            raise RuntimeError(f"Kubernetes backend is unavailable: {report.error}")
        print("Backend:", report.name)
        print("Cluster:", report.limitations[-1])
        print("Qualification:", report.limitations[0])
        try:
            await sandbox.create(policy=SandboxPolicy(network=NetworkAccess.NONE))
        except SandboxCapabilityError:
            print("Unverifiable network isolation: rejected")
        else:
            raise RuntimeError("network isolation without verified enforcement was accepted")
        policy = SandboxPolicy(
            # Pods always join the cluster network; accept that explicitly.
            network=NetworkAccess.ENABLED,
            memory_bytes=64 * 1024 * 1024,
            cpu_cores=0.25,
            scratch_bytes=8 * 1024 * 1024,
            required_capabilities=(Capability.MEMORY_LIMIT, Capability.CPU_LIMIT),
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
                b"cpu.max=25000 100000\n",
                b"root=read-only\n",
                b"scratch=full\n",
                b"token=none\n",
                b"stdin=from the host\n",
                b"greeting=hello\n",
            )
            if (
                result.exit_code != 0
                or any(line not in result.stdout for line in expected)
                or result.termination.state != "terminated"
                or not result.termination.resources_released
            ):
                raise RuntimeError("Kubernetes execution, isolation or owned cleanup failed")
            print(result.stdout.decode(), end="")
            print("Owned Pod released:", result.termination.resources_released)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True, help="Explicit kubeconfig file")
    parser.add_argument("--context", required=True, help="Explicit kubeconfig context")
    parser.add_argument("--namespace", required=True, help="Dedicated, already-created namespace")
    parser.add_argument("--image", required=True, help="Image already on the node with /bin/sh")
    parser.add_argument("--executable", help="kubectl path when not on PATH")
    arguments = parser.parse_args()
    asyncio.run(
        main(
            arguments.kubeconfig,
            arguments.context,
            arguments.namespace,
            arguments.image,
            arguments.executable,
        )
    )
