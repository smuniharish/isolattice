"""Check that isolattice is installed and usable in this Python environment.

Runs offline and changes nothing outside a temporary directory. Prints one
PASS, FAIL or INFO line per check:

    python scripts/verify_setup.py          # core checks
    python scripts/verify_setup.py --s3     # also require the s3 extra
    python scripts/verify_setup.py --json   # one JSON object on stdout

Exit codes: 0 every check passed, 1 a check failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import shutil
import sys
import tempfile
from importlib import metadata
from pathlib import Path

S3 = {"aiobotocore": ((3, 10, 0), (4,))}
CLIENTS = ("podman", "docker", "kubectl")

failures: list[str] = []
results: list[dict[str, str]] = []
as_json = False


def emit(status: str, message: str) -> None:
    results.append({"status": status, "message": message})
    if not as_json:
        print(f"{status}  {message}")


def report(passed: bool, message: str) -> bool:
    emit("PASS" if passed else "FAIL", message)
    if not passed:
        failures.append(message)
    return passed


def installed(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def release(version: str) -> tuple[int, ...]:
    """The numeric release of a version string: "3.10.0rc1" -> (3, 10, 0)."""
    numbers = []
    for part in version.split(".")[:3]:
        digits = re.match(r"\d+", part)
        numbers.append(int(digits.group()) if digits else 0)
    return tuple(numbers)


def dotted(version: tuple[int, ...]) -> str:
    return ".".join(map(str, version))


def check_environment(*, s3: bool) -> bool:
    report(
        sys.version_info >= (3, 12),
        f"Python {platform.python_version()} (3.12 or newer required)",
    )
    version = installed("isolattice")
    if version is None:
        return report(False, "isolattice is not installed: pip install isolattice")
    report(release(version)[:2] == (0, 1), f"isolattice {version} (0.1 expected by this skill)")
    for distribution, (lowest, below) in S3.items():
        found = installed(distribution)
        compatible = found is not None and lowest <= release(found) < below
        message = (
            f"{distribution} {found or 'is not installed'} "
            f"(>={dotted(lowest)},<{dotted(below)} for the s3 extra)"
        )
        if s3:
            report(compatible, message + ("" if compatible else '; pip install "isolattice[s3]"'))
        else:
            emit("INFO", message)
    clients = [name for name in CLIENTS if shutil.which(name)]
    emit(
        "INFO",
        f"engine clients on PATH: {', '.join(clients) or 'none'}; platform {sys.platform}",
    )
    return not failures


async def check_policy() -> None:
    from isolattice import Capability, ResourceBudget, SandboxPolicy

    policy = SandboxPolicy(
        memory_bytes=64 * 1024 * 1024,
        instance_budget=ResourceBudget(memory_bytes=128 * 1024 * 1024),
        required_capabilities=(Capability.MEMORY_LIMIT, Capability.INSTANCE_LIMITS),
    )
    refused = False
    try:
        SandboxPolicy(required_capabilities=(Capability.INSTANCE_LIMITS,))
    except ValueError:
        refused = True
    report(
        policy.read_only_root and policy.network.value == "none" and refused,
        "policies default to no network and a read-only root, and fail closed",
    )


async def check_store(workdir: Path) -> None:
    from isolattice import LocalArtifactStore
    from isolattice.testing import check_artifact_store

    root = workdir / "store"
    root.mkdir()
    scratch = workdir / "scratch"
    scratch.mkdir()
    await check_artifact_store(LocalArtifactStore(root), scratch)
    report(True, "LocalArtifactStore passes check_artifact_store")


async def check_s3() -> None:
    from isolattice import S3ArtifactStore, S3Credentials

    credentials = S3Credentials(access_key_id="verify", secret_access_key="never-printed")  # noqa: S106 - offline placeholder
    store = S3ArtifactStore(bucket="verify-setup", credentials=credentials)
    report(
        "never-printed" not in repr(credentials) and "never-printed" not in repr(store),
        "S3ArtifactStore constructs offline and keeps secrets out of repr",
    )


async def main(*, s3: bool) -> None:
    if not check_environment(s3=s3):
        return
    with tempfile.TemporaryDirectory(prefix="isolattice-verify-") as directory:
        await check_policy()
        await check_store(Path(directory))
    if s3 and installed("aiobotocore"):
        await check_s3()


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--s3", action="store_true", help="require the s3 extra")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = parse_arguments()
    as_json = arguments.json
    try:
        asyncio.run(main(s3=arguments.s3))
    except Exception as error:
        report(False, f"{type(error).__name__}: {error}")
    summary = "All checks passed." if not failures else f"{len(failures)} check(s) failed."
    if as_json:
        print(json.dumps({"ok": not failures, "summary": summary, "checks": results}))
    else:
        print(summary)
    sys.exit(1 if failures else 0)
