"""Forward Podman log entries written after a checkpoint.

Run as a standalone script by the Podman backend for restored executions:
``python -I _podman_log_filter.py AFTER_NS podman ... logs --follow --timestamps ID``.
Entries timestamped at or before ``AFTER_NS`` were already delivered by the
checkpointed execution and are dropped; later entries are forwarded without
their timestamp to the matching stream. The script exits with Podman's status.
"""

from __future__ import annotations

import calendar
import re
import subprocess
import sys
import threading
from typing import BinaryIO

_TIME = re.compile(
    rb"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d) ?"
)


def timestamp_ns(match: re.Match[bytes]) -> int:
    year, month, day, hour, minute, second = (int(match.group(index)) for index in range(1, 7))
    fraction = (match.group(7) or b"").ljust(9, b"0")
    zone = match.group(8)
    offset = 0
    if zone != b"Z":
        sign = 1 if zone[:1] == b"+" else -1
        offset = sign * (int(zone[1:3]) * 3600 + int(zone[4:6]) * 60)
    seconds = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)) - offset
    return seconds * 1_000_000_000 + int(fraction)


def parse_timestamp(text: bytes) -> int:
    match = _TIME.fullmatch(text.strip())
    if match is None:
        raise ValueError("invalid Podman log timestamp")
    return timestamp_ns(match)


def forward(source: BinaryIO, sink: BinaryIO, after: int | None) -> None:
    keep = after is None
    for line in iter(source.readline, b""):
        match = _TIME.match(line)
        if match is not None:
            keep = after is None or timestamp_ns(match) > after
            line = line[match.end() :]
        if keep:
            sink.write(line)
            sink.flush()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: _podman_log_filter.py AFTER_NS COMMAND...", file=sys.stderr)
        return 2
    after = int(argv[1]) if argv[1] else None
    with subprocess.Popen(
        argv[2:], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("the log client was started without output pipes")
        threads = [
            threading.Thread(target=forward, args=(process.stdout, sys.stdout.buffer, after)),
            threading.Thread(target=forward, args=(process.stderr, sys.stderr.buffer, after)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return process.wait()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
