"""Writes sandbox audit entries for compliance: the starting point of eval 1.

Today the application calls ``record`` by hand after each isolattice run, and
entries are missing whenever a run fails or is cancelled.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

AUDIT_FILE = Path("audit.jsonl")


def record(sandbox_id: str, outcome: str) -> None:
    with AUDIT_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps({"sandbox": sandbox_id, "outcome": outcome, "at": time.time()}))
        file.write("\n")
