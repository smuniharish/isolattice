"""In-container verification of kernel-reported isolation before workload exec.

Engine metadata describes the requested configuration; this POSIX ``sh``
prelude reads the kernel's view of the workload process itself and refuses to
exec the workload unless every check passes. Success is signalled by printing
a per-execution marker as the first stdout line, before the workload can write.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

READY_PREFIX = "ISOLATTICE_READY_"
FAILURE_EXIT = 125


@dataclass(frozen=True, slots=True)
class KernelChecks:
    """Kernel state the prelude must observe; ``None`` skips a resource check.

    ``scratch_kib`` requires exactly one tmpfs mounted at ``/tmp`` with that
    size. ``await_start`` makes the prelude block after verification until the
    attached client writes the marker line on stdin, for engines whose
    workloads start before a client can attach.
    """

    uid: int
    gid: int
    memory_bytes: int | None = None
    swap_disabled: bool = True
    cpu_quota: int | None = None
    cpu_period: int = 100000
    process_limit: int | None = None
    scratch_kib: int | None = None
    await_start: bool = False

    def script(self) -> str:
        fail = f"exit {FAILURE_EXIT}"
        ids = {
            "Uid": "[[:space:]]".join([str(self.uid)] * 4),
            "Gid": "[[:space:]]".join([str(self.gid)] * 4),
        }
        lines = [
            "seen=0",
            "while IFS=': \t' read -r key value; do",
            ' case "$key" in',
            "  CapInh|CapPrm|CapEff|CapBnd|CapAmb)",
            f'   case "$value" in *[!0]*|"") {fail};; esac; seen=$((seen+1));;',
            f'  NoNewPrivs) [ "$value" = 1 ] || {fail}; seen=$((seen+1));;',
            f'  Seccomp) [ "$value" = 2 ] || {fail}; seen=$((seen+1));;',
            *(
                f'  {key}) case "$value" in {pattern}) ;; *) {fail};; esac; seen=$((seen+1));;'
                for key, pattern in ids.items()
            ),
            " esac",
            "done < /proc/self/status",
            f'[ "$seen" = 9 ] || {fail}',
        ]
        if self.memory_bytes is not None:
            lines.extend(
                (
                    f"IFS= read -r value < /sys/fs/cgroup/memory.max || {fail}",
                    f'[ "$value" -gt 0 ] && [ "$value" -le {self.memory_bytes} ] || {fail}',
                )
            )
            if self.swap_disabled:
                lines.extend(
                    (
                        f"IFS= read -r value < /sys/fs/cgroup/memory.swap.max || {fail}",
                        f'[ "$value" = 0 ] || {fail}',
                    )
                )
        if self.cpu_quota is not None:
            lines.extend(
                (
                    f"IFS= read -r value < /sys/fs/cgroup/cpu.max || {fail}",
                    f'[ "$value" = "{self.cpu_quota} {self.cpu_period}" ] || {fail}',
                )
            )
        if self.process_limit is not None:
            lines.extend(
                (
                    f"IFS= read -r value < /sys/fs/cgroup/pids.max || {fail}",
                    f'[ "$value" = "{self.process_limit}" ] || {fail}',
                )
            )
        if self.scratch_kib is not None:
            lines.extend(
                (
                    "found=0",
                    "while read -r device point kind options rest; do",
                    ' [ "$point" = /tmp ] || continue',
                    f' [ "$kind" = tmpfs ] || {fail}',
                    f' case ",$options," in ,rw,*,size={self.scratch_kib}k,*) ;; *) {fail};; esac',
                    " found=$((found+1))",
                    "done < /proc/self/mounts",
                    f'[ "$found" = 1 ] || {fail}',
                )
            )
        if self.await_start:
            lines.append(f'IFS= read -r start && [ "$start" = "$1" ] || {fail}')
        lines.append('printf "%s\\n" "$1"; shift; exec "$@"')
        return "\n".join(lines)


def marker(execution_id: str) -> bytes:
    return f"{READY_PREFIX}{execution_id}\n".encode()


def command(checks: KernelChecks, argv: Sequence[str], execution_id: str) -> tuple[str, ...]:
    """Wrap ``argv`` in the verifying prelude; requires ``/bin/sh`` in the image."""
    return (
        "/bin/sh",
        "-c",
        checks.script(),
        "isolattice-bootstrap",
        marker(execution_id).decode().rstrip("\n"),
        *argv,
    )
