"""Contract tests for Podman instance features that need no engine."""

import asyncio
import io
import json
import tarfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    Capability,
    CapabilityState,
    PodmanBackend,
    ResourceBudget,
    SandboxArtifactError,
    SandboxCapabilityError,
    SandboxPolicy,
)
from isolattice.backends import _podman_log_filter as log_filter
from isolattice.backends import podman


def _spec(**changes: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "process": {
            "user": {"uid": 65534, "gid": 65534, "additionalGids": [65534]},
            "noNewPrivileges": True,
            "capabilities": {},
        },
        "root": {"path": "/var/lib/containers/storage/overlay/x/merged", "readonly": True},
        "linux": {
            "namespaces": [{"type": kind} for kind in sorted(podman._REQUIRED_NAMESPACES)],
            "seccomp": {"defaultAction": "SCMP_ACT_ERRNO"},
            "maskedPaths": ["/proc/kcore"],
        },
        "mounts": [
            {"destination": "/proc", "type": "proc", "source": "proc", "options": []},
            {
                "destination": "/dev/shm",
                "type": "bind",
                "source": "/var/lib/containers/storage/overlay-containers/x/userdata/shm",
                "options": ["bind", "ro"],
            },
        ],
    }
    for path, value in changes.items():
        target = spec
        *parents, leaf = path.split("__")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return spec


def _archive(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as bundle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return path


def test_valid_spec_is_accepted() -> None:
    podman._validate_spec(json.dumps(_spec()).encode())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"process__user": {"uid": 0, "gid": 0}}, "unprivileged user"),
        ({"process__user": {"uid": 65534, "gid": 65534, "additionalGids": [0]}}, "unprivileged"),
        ({"process__noNewPrivileges": False}, "no-new-privileges"),
        ({"process__capabilities": {"bounding": ["CAP_SYS_ADMIN"]}}, "capabilities"),
        ({"root__readonly": False}, "read-only root"),
        ({"linux__namespaces": [{"type": "pid"}]}, "private namespaces"),
        (
            {
                "linux__namespaces": [
                    {"type": kind, "path": "/proc/1/ns/net" if kind == "network" else ""}
                    for kind in sorted(podman._REQUIRED_NAMESPACES)
                ]
            },
            "existing namespace",
        ),
        ({"linux__devices": [{"path": "/dev/sda"}]}, "devices"),
        ({"linux__seccomp": None}, "seccomp"),
        ({"linux__maskedPaths": []}, "masked paths"),
        (
            {"mounts": [{"destination": "/workspace", "type": "bind", "source": "/home"}]},
            "mount destination",
        ),
        (
            {"mounts": [{"destination": "/dev/shm", "type": "bind", "source": "/etc"}]},
            "outside engine storage",
        ),
        (
            {
                "mounts": [
                    {
                        "destination": "/dev/shm",
                        "options": ["rbind"],
                        "source": "/var/lib/containers/storage/../../../etc",
                    }
                ]
            },
            "outside engine storage",
        ),
    ],
)
def test_unsafe_specs_are_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(SandboxArtifactError, match=message):
        podman._validate_spec(json.dumps(_spec(**changes)).encode())


def test_spec_must_be_json() -> None:
    with pytest.raises(SandboxArtifactError, match="JSON"):
        podman._validate_spec(b"\xff")


def test_checkpoint_archive_returns_last_log_timestamp(tmp_path: Path) -> None:
    log = (
        b"2026-01-01T00:00:00.5+00:00 stdout F first\n"
        b"2026-01-01T05:30:01.000000001+05:30 stderr F last\n"
    )
    archive = _archive(
        tmp_path / "c.tar.gz", {"./spec.dump": json.dumps(_spec()).encode(), "ctr.log": log}
    )
    assert podman._inspect_checkpoint(archive) == 1767225601000000001


def test_checkpoint_archive_with_empty_log_keeps_all_output(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "c.tar.gz", {"spec.dump": json.dumps(_spec()).encode(), "ctr.log": b""}
    )
    assert podman._inspect_checkpoint(archive) is None


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ({"spec.dump": b"{}"}, "lacks its spec or container log"),
        ({"ctr.log": b""}, "lacks its spec or container log"),
        ({"spec.dump": b"{}", "./spec.dump": b"{}", "ctr.log": b""}, "repeats"),
        ({"spec.dump": json.dumps(_spec()).encode(), "ctr.log": b"bogus F x\n"}, "timestamp"),
    ],
)
def test_invalid_checkpoint_archives_are_rejected(
    tmp_path: Path, members: dict[str, bytes], message: str
) -> None:
    with pytest.raises(SandboxArtifactError, match=message):
        podman._inspect_checkpoint(_archive(tmp_path / "c.tar.gz", members))


def test_non_archive_checkpoint_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.tar.gz"
    path.write_bytes(b"not a tar")
    with pytest.raises(SandboxArtifactError, match="readable Podman export"):
        podman._inspect_checkpoint(path)


def test_oversized_spec_member_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(podman, "_MAX_SPEC_BYTES", 4)
    archive = _archive(tmp_path / "c.tar.gz", {"spec.dump": b"12345", "ctr.log": b""})
    with pytest.raises(SandboxArtifactError, match="too large"):
        podman._inspect_checkpoint(archive)


def test_budget_rejects_aggregate_process_limit() -> None:
    with pytest.raises(SandboxCapabilityError, match="aggregate process"):
        podman._Budget.from_budget(ResourceBudget(process_limit=4))


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        (
            SandboxPolicy(
                workspace=True,
                scratch_bytes=1024,
                required_capabilities=(Capability.DISK_LIMIT,),
            ),
            "no quota",
        ),
        (
            SandboxPolicy(
                workspace=True,
                required_capabilities=(Capability.CHECKPOINTS,),
            ),
            "cannot be combined",
        ),
        (
            SandboxPolicy(
                instance_budget=ResourceBudget(memory_bytes=67108864),
                required_capabilities=(Capability.CHECKPOINTS,),
            ),
            "cannot be combined",
        ),
        (
            SandboxPolicy(
                instance_budget=ResourceBudget(process_limit=4),
            ),
            "aggregate process",
        ),
        (
            SandboxPolicy(
                instance_budget=ResourceBudget(memory_bytes=1024),
            ),
            "6 MiB",
        ),
    ],
)
def test_unsupported_instance_policies_fail_before_engine_access(
    policy: SandboxPolicy, message: str
) -> None:
    backend = PodmanBackend(connection="not-contacted", image="not-contacted")

    async def check() -> None:
        with pytest.raises(SandboxCapabilityError, match=message):
            await backend.prepare(policy, "0" * 32)

    asyncio.run(check())


def test_rootful_report_gates_instance_limits_and_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(podman, "_client_refuses_checkpoints", lambda: False)
    rootful = podman._Engine(
        "podman",
        "c",
        1.0,
        "a" * 64,
        frozenset(),
        rootless=False,
        cgroup_version="v2",
        controllers=frozenset({"memory", "cpu"}),
    )
    report = podman._report(True, engine=rootful)
    assert report.capabilities[Capability.INSTANCE_LIMITS].state is CapabilityState.SUPPORTED
    assert report.capabilities[Capability.CHECKPOINTS].state is CapabilityState.LIMITED
    rootless = podman._report(True, engine=replace(rootful, rootless=True))
    for capability in (Capability.INSTANCE_LIMITS, Capability.CHECKPOINTS):
        assert rootless.capabilities[capability].state is CapabilityState.UNSUPPORTED
    no_controllers = podman._report(True, engine=replace(rootful, controllers=frozenset()))
    assert (
        no_controllers.capabilities[Capability.INSTANCE_LIMITS].state is CapabilityState.UNSUPPORTED
    )
    unavailable = podman._report(False, error="down")
    for capability in (Capability.WORKSPACE, Capability.SNAPSHOTS, Capability.DISK_LIMIT):
        assert unavailable.capabilities[capability].state is CapabilityState.UNSUPPORTED
    monkeypatch.setattr(podman, "_client_refuses_checkpoints", lambda: True)
    non_root = podman._report(True, engine=rootful).capabilities[Capability.CHECKPOINTS]
    assert non_root.state is CapabilityState.UNSUPPORTED
    assert "runs as root" in non_root.detail


def test_client_checkpoint_rule_applies_only_to_non_root_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(podman.sys, "platform", "win32")
    assert not podman._client_refuses_checkpoints()
    monkeypatch.setattr(podman.sys, "platform", "linux")
    for euid, refused in ((0, False), (1000, True)):
        monkeypatch.setattr(podman.os, "geteuid", lambda euid=euid: euid, raising=False)
        assert podman._client_refuses_checkpoints() is refused


def test_non_root_linux_client_rejects_checkpoint_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(podman, "_client_refuses_checkpoints", lambda: True)
    backend = PodmanBackend(connection="not-contacted", image="not-contacted", allow_rootful=True)

    async def check() -> None:
        with pytest.raises(SandboxCapabilityError, match="runs as root"):
            await backend.prepare(
                SandboxPolicy(required_capabilities=(Capability.CHECKPOINTS,)), "0" * 32
            )

    asyncio.run(check())


def test_budget_arguments_and_inspection_match() -> None:
    budget = podman._Budget.from_budget(ResourceBudget(memory_bytes=67108864, cpu_cores=0.5))
    assert budget.controllers == {"memory", "cpu"}
    assert budget.arguments() == (
        "--memory",
        "67108864",
        "--memory-swap",
        "67108864",
        "--cpus",
        "0.5",
    )
    inspected = {
        "memory_limit": 67108864,
        "memory_swap": 67108864,
        "cpu_quota": 50000,
        "cpu_period": 100000,
    }
    assert budget.matches(inspected)
    assert not budget.matches({**inspected, "memory_swap": 0})
    assert not budget.matches({**inspected, "cpu_quota": True})


@given(st.integers(min_value=1000, max_value=10**8))
def test_budget_cpu_round_trips_exact_quota(quota: int) -> None:
    budget = podman._Budget(cpu_quota=quota)
    cpus = budget.arguments()[1]
    assert podman._cpu_quota(float(cpus), "instance") == quota


_ZONES = st.integers(min_value=-14 * 60, max_value=14 * 60).map(lambda m: timedelta(minutes=m))


@given(
    st.datetimes(
        # Hypothesis requires naive bounds for naive datetimes.
        min_value=datetime(1971, 1, 1),  # noqa: DTZ001
        max_value=datetime(2200, 1, 1),  # noqa: DTZ001
        timezones=st.none(),
    ),
    st.integers(min_value=0, max_value=999_999_999),
    _ZONES,
)
def test_timestamps_parse_to_absolute_nanoseconds(
    moment: datetime, nanos: int, offset: timedelta
) -> None:
    local = moment.replace(microsecond=0, tzinfo=timezone(offset))
    text = local.strftime("%Y-%m-%dT%H:%M:%S")
    sign = "+" if offset >= timedelta(0) else "-"
    minutes = abs(int(offset.total_seconds())) // 60
    stamp = f"{text}.{nanos:09d}{sign}{minutes // 60:02d}:{minutes % 60:02d}".encode()
    expected = int(local.astimezone(UTC).timestamp()) * 1_000_000_000 + nanos
    assert log_filter.parse_timestamp(stamp) == expected
    assert log_filter.parse_timestamp(stamp.replace(b"+00:00", b"Z")) == expected


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=10**6),
            st.binary(max_size=20).filter(lambda data: b"\n" not in data),
        ),
        max_size=20,
    ),
    st.one_of(st.none(), st.integers(min_value=0, max_value=10**6)),
)
def test_filter_forwards_exactly_entries_after_mark(
    entries: list[tuple[int, bytes]], after: int | None
) -> None:
    base = 1_700_000_000 * 1_000_000_000
    source = io.BytesIO(
        b"".join(
            datetime.fromtimestamp((base + offset) // 10**9, UTC)
            .strftime("%Y-%m-%dT%H:%M:%S")
            .encode()
            + f".{(base + offset) % 10**9:09d}Z ".encode()
            + content
            + b"\n"
            for offset, content in entries
        )
    )
    sink = io.BytesIO()
    mark = None if after is None else base + after
    log_filter.forward(source, sink, mark)
    assert sink.getvalue() == b"".join(
        content + b"\n" for offset, content in entries if mark is None or base + offset > mark
    )


def test_filter_untimestamped_lines_follow_previous_decision() -> None:
    source = io.BytesIO(
        b"2026-01-01T00:00:00Z old\ncontinued old\n2026-01-01T00:00:02Z new\ncontinued new\n"
    )
    sink = io.BytesIO()
    log_filter.forward(source, sink, log_filter.parse_timestamp(b"2026-01-01T00:00:01Z"))
    assert sink.getvalue() == b"new\ncontinued new\n"


def test_filter_script_reports_usage_and_child_status(capsys: pytest.CaptureFixture[str]) -> None:
    import sys

    assert log_filter.main(["filter"]) == 2
    assert "usage" in capsys.readouterr().err
    assert log_filter.main(["filter", "", sys.executable, "-c", "raise SystemExit(5)"]) == 5
