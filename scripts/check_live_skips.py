"""Fail a live CI job when a configured live test skipped or nothing ran.

    python scripts/check_live_skips.py report.xml [--allow TEST_NAME ...]
        [--allow-reason TEXT ...]

``--allow`` names a test function (parametrized IDs included) whose skip is
expected in that job's configuration, for example a rootless-only check on a
job that only provisions a rootful engine. ``--allow-reason`` permits any skip
whose message contains the text, for a host limitation that applies to several
tests and must not hide their other skip causes.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence


def unexpected_skips(
    report: ElementTree.Element, allowed: Iterable[str], reasons: Iterable[str] = ()
) -> list[str]:
    """Return ``classname::name`` of skipped tests not covered by ``allowed`` or ``reasons``."""
    permitted = frozenset(allowed)
    expected_reasons = tuple(reasons)
    unexpected = []
    for case in report.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        name = case.get("name", "")
        if name.split("[", 1)[0] in permitted:
            continue
        message = skipped.get("message", "")
        if any(reason in message for reason in expected_reasons):
            continue
        unexpected.append(f"{case.get('classname', '')}::{name}")
    return unexpected


def executed(report: ElementTree.Element) -> int:
    return sum(1 for case in report.iter("testcase") if case.find("skipped") is None)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--allow", action="append", default=[], metavar="TEST_NAME")
    parser.add_argument("--allow-reason", action="append", default=[], metavar="TEXT")
    arguments = parser.parse_args(argv)
    # The JUnit report is produced locally by this repository's own pytest run.
    report = ElementTree.parse(arguments.report).getroot()  # noqa: S314
    if executed(report) == 0:
        print("no live test executed", file=sys.stderr)
        return 1
    unexpected = unexpected_skips(report, arguments.allow, arguments.allow_reason)
    for case in unexpected:
        print(f"configured live test skipped: {case}", file=sys.stderr)
    return 1 if unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
