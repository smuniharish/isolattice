"""Run an untrusted-style workload in unprivileged Linux namespaces.

Requires Linux with unprivileged user namespaces, Landlock, libseccomp and a
cgroup v2 subtree delegated to the calling user. Run it through the delegated
systemd scope wrapper, which exports ISOLATTICE_LINUX_CGROUP_ROOT::

    scripts/linux/isolattice-delegated-run.sh python3 \
        examples/08-native-backends/linux_namespaces.py

Namespaces share the host kernel; kernel exploits are outside its threat model.
"""

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from isolattice import (
    Capability,
    ExecutionRequest,
    LinuxNamespaceBackend,
    LocalArtifactStore,
    ResourceBudget,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
)

_PROBE = """
id -u
hostname
grep -E '^(CapEff|NoNewPrivs|Seccomp):' /proc/self/status | tr -d '\\t'
test -e "$HOME_ON_HOST" && echo "home visible" || echo "home hidden"
( : > /etc/isolattice ) 2>/dev/null && echo "etc writable" || echo "etc read-only"
python3 -c 'import socket; socket.create_connection(("1.1.1.1", 53), 2)' 2>/dev/null \
    && echo "network reachable" || echo "network blocked"
"""


async def main(cgroup_root: str, home: str) -> None:
    backend = LinuxNamespaceBackend(cgroup_root=cgroup_root)
    report = await backend.discover()
    print(f"Backend: {report.name} (available: {report.available})")
    if not report.available:
        raise RuntimeError(f"the Linux namespace backend is unavailable: {report.error}")

    policy = SandboxPolicy(
        memory_bytes=64 * 1024 * 1024,
        process_limit=16,
        scratch_bytes=8 * 1024 * 1024,
        workspace=True,
        instance_budget=ResourceBudget(memory_bytes=128 * 1024 * 1024, process_limit=32),
        execution_timeout=30,
        required_capabilities=(
            Capability.FILESYSTEM_ISOLATION,
            Capability.NETWORK_ISOLATION,
            Capability.PROCESS_ISOLATION,
            Capability.VERIFIED_TERMINATION,
            Capability.MEMORY_LIMIT,
            Capability.PROCESS_LIMIT,
            Capability.INSTANCE_LIMITS,
            Capability.SNAPSHOTS,
        ),
    )
    with tempfile.TemporaryDirectory(prefix="isolattice-") as directory:
        store = LocalArtifactStore(directory)
        async with Sandbox(backend=backend, artifact_store=store) as sandbox:
            key = "workspace.tar"
            async with await sandbox.create(policy=policy) as instance:
                probe = await instance.run(
                    ExecutionRequest(
                        argv=("/bin/sh", "-c", _PROBE), environment={"HOME_ON_HOST": home}
                    )
                )
                lines = probe.stdout.decode().split("\n")
                expected = [
                    "65534",
                    "isolattice",
                    "CapEff:0000000000000000",
                    "NoNewPrivs:1",
                    "Seccomp:2",
                    "home hidden",
                    "etc read-only",
                    "network blocked",
                ]
                if lines[: len(expected)] != expected:
                    raise RuntimeError(f"unexpected sandbox identity: {lines}")
                print("Identity: uid 65534, no capabilities, no_new_privs, seccomp filter")
                print("Filesystem: host home hidden, root read-only")
                print("Network: blocked")

                memory = await instance.run(
                    ExecutionRequest(
                        argv=("python3", "-c", "b = bytearray(256 * 1024 * 1024)"),
                    )
                )
                if memory.exit_code != 137:
                    raise RuntimeError("the memory budget was not enforced")
                print("Memory budget: allocation beyond 64 MiB was OOM-killed")

                try:
                    await instance.run(
                        ExecutionRequest(argv=("/bin/sh", "-c", "sleep 600 & sleep 600"), timeout=1)
                    )
                except SandboxTimeoutError as error:
                    stopped = error.report
                else:
                    raise RuntimeError("the workload was expected to exceed its deadline")
                if stopped is None or not stopped.resources_released:
                    raise RuntimeError("process-tree termination was not verified")
                print(f"Timeout cleanup: {stopped.state}")

                await instance.run(
                    ExecutionRequest(argv=("/bin/sh", "-c", "echo kept > /workspace/result"))
                )
                await instance.snapshot(key)
            async with await sandbox.create(policy=policy, workspace_snapshot=key) as restored:
                result = await restored.run(ExecutionRequest(argv=("cat", "/workspace/result")))
                if result.stdout != b"kept\n":
                    raise RuntimeError("the workspace snapshot was not restored")
                print("Workspace snapshot: restored into a new instance")

            try:
                await sandbox.create(
                    policy=SandboxPolicy(
                        required_capabilities=(Capability.CHECKPOINTS,),
                    )
                )
            except SandboxCapabilityError:
                print("Required checkpoints: rejected")
            else:
                raise RuntimeError("an unsupported capability was accepted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cgroup-root",
        default=os.environ.get("ISOLATTICE_LINUX_CGROUP_ROOT"),
        help="Delegated, process-free cgroup v2 directory owned by the calling user",
    )
    arguments = parser.parse_args()
    if not arguments.cgroup_root:
        parser.error("--cgroup-root or ISOLATTICE_LINUX_CGROUP_ROOT is required")
    asyncio.run(main(arguments.cgroup_root, str(Path.home())))
