"""Runs user-supplied report scripts with subprocess: the starting point of eval 1."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_report(script: Path, customer: str) -> str:
    completed = subprocess.run(
        f"{sys.executable} {script} --customer {customer}",
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return completed.stdout


if __name__ == "__main__":
    print(run_report(Path(sys.argv[1]), sys.argv[2]))
