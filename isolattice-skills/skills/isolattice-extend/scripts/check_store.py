"""Check that a custom isolattice ArtifactStore keeps its contract.

    python scripts/check_store.py package.module:factory [--path DIR] [--json]

``factory`` is a class or function that takes no arguments and returns the
store. ``--path`` adds a directory to the import path. The check publishes,
fetches, bounds and deletes one disposable artifact under ``check-store/``.

Exit codes: 0 the store conforms, 1 a promise is broken, 2 the factory cannot
be loaded or does not build an ArtifactStore.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import tempfile
from pathlib import Path

from isolattice import ArtifactStore, SandboxArtifactError
from isolattice.testing import ConformanceError, check_artifact_store


def load(target: str) -> ArtifactStore:
    module_name, _, attribute = target.partition(":")
    if not module_name or not attribute:
        raise ValueError("expected module:factory")
    factory = getattr(importlib.import_module(module_name), attribute)
    store = factory()
    if not isinstance(store, ArtifactStore):
        raise TypeError(f"{target} built {type(store).__name__}, not an ArtifactStore")
    return store


async def check(store: ArtifactStore) -> str | None:
    with tempfile.TemporaryDirectory(prefix="isolattice-check-store-") as directory:
        try:
            await check_artifact_store(store, Path(directory), key_prefix="check-store")
        except ConformanceError as error:
            return str(error)
        except SandboxArtifactError as error:
            return f"an operation the contract allows failed: {error}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", help="module:factory that builds the store")
    parser.add_argument("--path", action="append", default=[], help="add an import directory")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    arguments = parser.parse_args(argv)
    sys.path[:0] = [str(Path(path).resolve()) for path in arguments.path]
    try:
        store = load(arguments.target)
    except Exception as error:
        print(f"cannot load {arguments.target}: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    broken = asyncio.run(check(store))
    name = type(store).__name__
    if arguments.json:
        print(json.dumps({"ok": broken is None, "store": name, "broken": broken}))
    elif broken is None:
        print(f"PASS  {name} keeps the ArtifactStore contract.")
    else:
        print(f"FAIL  {name}: {broken}")
    return 0 if broken is None else 1


if __name__ == "__main__":
    sys.exit(main())
