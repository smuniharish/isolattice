"""Inspect Windows Job Object evidence and show that unenforceable policies fail closed."""

import argparse
import asyncio
import tempfile
from pathlib import Path

from isolattice import (
    Capability,
    NetworkAccess,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    WindowsJobBackend,
)


async def main(working_directory: Path) -> None:
    backend = WindowsJobBackend(working_directory=working_directory)
    async with Sandbox(backend=backend) as sandbox:
        (report,) = await sandbox.discover()
        print(f"Backend: {report.name} (available: {report.available})")
        for capability, evidence in report.capabilities.items():
            print(f"{capability.value}: {evidence.state.value} - {evidence.detail}")
        for limitation in report.limitations:
            print(f"Limitation: {limitation}")
        if not report.available:
            raise RuntimeError(report.error or "the Windows backend is unavailable")

        rejected = (
            ("Default restricted policy", SandboxPolicy()),
            (
                "Required filesystem isolation",
                SandboxPolicy(
                    network=NetworkAccess.ENABLED,
                    read_only_root=False,
                    required_capabilities=(Capability.FILESYSTEM_ISOLATION,),
                ),
            ),
        )
        for label, policy in rejected:
            try:
                await sandbox.create(policy=policy)
            except SandboxCapabilityError:
                print(f"{label}: rejected")
            else:
                raise RuntimeError(f"{label} was not rejected")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    with tempfile.TemporaryDirectory(prefix="isolattice-") as directory:
        asyncio.run(main(Path(directory)))
