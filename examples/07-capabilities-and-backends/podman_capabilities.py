"""Inspect backend-specific evidence without claiming production qualification."""

import argparse
import asyncio

from isolattice import PodmanBackend


async def main(connection: str, image: str) -> None:
    report = await PodmanBackend(connection=connection, image=image).discover()
    if not report.available:
        raise RuntimeError(f"configured backend is unavailable: {report.error}")
    print("Backend:", report.name)
    for capability, evidence in report.capabilities.items():
        print(f"{capability.value}: {evidence.state.value} ({evidence.detail})")
    print("Qualification:", report.limitations[0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", required=True)
    parser.add_argument("--image", required=True)
    arguments = parser.parse_args()
    asyncio.run(main(arguments.connection, arguments.image))
