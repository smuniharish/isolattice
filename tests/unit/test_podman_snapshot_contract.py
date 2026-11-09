"""Podman container-snapshot command contracts with a scripted engine.

These tests prove pause/resume ordering, failure retention and image ownership
checks. Live tests in tests/integration prove the engine behavior.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from isolattice import (
    SandboxBackendError,
    SandboxError,
    SandboxExecutionError,
    TerminationVerificationError,
)
from isolattice.backend import Boundary
from isolattice.backends import _cli, podman

if TYPE_CHECKING:
    from pathlib import Path

_SANDBOX = "s" * 32
_EXECUTION = "e" * 32
_CONTAINER = "c" * 64
_IMAGE = "a" * 64
_IMPORTED = "b" * 64

Script = Callable[[tuple[str, ...]], _cli.CommandResult]


def _result(stdout: bytes = b"", returncode: int = 0) -> _cli.CommandResult:
    return _cli.CommandResult(stdout, b"", returncode)


def _container(status: str) -> bytes:
    return json.dumps(
        {
            "Id": _CONTAINER,
            "Config": {
                "Labels": {
                    "io.isolattice.sandbox": _SANDBOX,
                    "io.isolattice.execution": _EXECUTION,
                }
            },
            "State": {"Status": status},
        }
    ).encode()


class Engine:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.calls: list[tuple[str, ...]] = []

    async def invoke(
        self,
        engine: podman._Engine,
        operation: str,
        *args: str,
        accepted: tuple[int, ...] = (0,),
        timeout: float | None = None,  # noqa: ASYNC109 - mirrors podman._invoke
    ) -> _cli.CommandResult:
        self.calls.append(args)
        result = self.script(args)
        if result.returncode not in accepted:
            raise _cli.CommandError(f"Podman {operation} failed", possibly_accepted=True)
        return result

    def verbs(self) -> list[str]:
        return [" ".join(call[:2]) if call[0] == "image" else call[0] for call in self.calls]


def _session(
    monkeypatch: pytest.MonkeyPatch, script: Script
) -> tuple[Engine, Boundary, podman._PodmanSession]:
    fake = Engine(script)
    monkeypatch.setattr(podman, "_invoke", fake.invoke)
    engine = podman._Engine("podman", "test", 5.0, _IMAGE, frozenset())
    session = podman._PodmanSession(engine, sandbox_id=_SANDBOX, read_only_root=False)
    boundary = Boundary(_CONTAINER, "name", _SANDBOX, _EXECUTION)
    return fake, boundary, session


def _states(*statuses: str) -> Callable[[], bytes]:
    remaining = list(statuses)
    return lambda: _container(remaining.pop(0) if len(remaining) > 1 else remaining[0])


def test_snapshot_pauses_exports_and_resumes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "root.tar"
    state = _states("running", "paused")

    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "export":
            destination.write_bytes(b"tar")
        return _result(state() if args[:2] == ("container", "inspect") else b"")

    fake, boundary, session = _session(monkeypatch, script)
    asyncio.run(session.snapshot_boundary(boundary, destination))
    assert fake.verbs() == ["container", "pause", "container", "export", "unpause"]


def test_snapshot_rejects_a_stopped_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake, boundary, session = _session(monkeypatch, lambda args: _result(_container("exited")))
    with pytest.raises(SandboxExecutionError, match="running"):
        asyncio.run(session.snapshot_boundary(boundary, tmp_path / "x"))
    assert "pause" not in fake.verbs()


def test_export_failure_still_resumes_and_is_retained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _states("running", "paused")

    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "export":
            return _result(returncode=125)
        return _result(state() if args[:2] == ("container", "inspect") else b"")

    fake, boundary, session = _session(monkeypatch, script)
    with pytest.raises(SandboxBackendError, match="container export"):
        asyncio.run(session.snapshot_boundary(boundary, tmp_path / "x"))
    assert fake.verbs()[-1] == "unpause"


def test_failed_resume_of_a_paused_container_surfaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "root.tar"
    state = _states("running", "paused")

    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "export":
            destination.write_bytes(b"tar")
        if args[0] == "unpause":
            return _result(returncode=125)
        if args[:2] == ("container", "exists"):
            return _result()
        return _result(state() if args[:2] == ("container", "inspect") else b"")

    _, boundary, session = _session(monkeypatch, script)
    with pytest.raises((SandboxError, BaseExceptionGroup)) as raised:
        asyncio.run(session.snapshot_boundary(boundary, destination))
    assert "container unpause" in repr(raised.value)


def test_unpause_after_concurrent_stop_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "root.tar"
    state = _states("running", "paused", "exited")

    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "export":
            destination.write_bytes(b"tar")
        if args[0] == "unpause":
            return _result(returncode=125)
        if args[:2] == ("container", "exists"):
            return _result()
        return _result(state() if args[:2] == ("container", "inspect") else b"")

    _, boundary, session = _session(monkeypatch, script)
    asyncio.run(session.snapshot_boundary(boundary, destination))


def _image(identifier: str, owner: str) -> bytes:
    return json.dumps({"Id": identifier, "Labels": {"io.isolattice.sandbox": owner}}).encode()


def test_import_uses_only_an_owned_verified_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "import":
            return _result(f"sha256:{_IMPORTED}\n".encode())
        if args[:2] == ("image", "inspect"):
            return _result(_image(_IMPORTED, _SANDBOX))
        return _result()

    fake, _, session = _session(monkeypatch, script)
    asyncio.run(session.import_root(tmp_path / "root.tar"))
    assert f"LABEL=io.isolattice.sandbox={_SANDBOX}" in fake.calls[0]
    assert session._image_id == _IMPORTED
    with pytest.raises(SandboxBackendError, match="another root"):
        asyncio.run(session.import_root(tmp_path / "root.tar"))


@pytest.mark.parametrize(
    ("stdout", "inspected", "message"),
    [
        (b"not-an-id\n", _image(_IMPORTED, _SANDBOX), "full immutable ID"),
        (f"sha256:{_IMPORTED}".encode(), _image(_IMAGE, _SANDBOX), "owned image"),
        (f"sha256:{_IMPORTED}".encode(), _image(_IMPORTED, "other"), "owned image"),
    ],
)
def test_import_rejects_unverifiable_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdout: bytes, inspected: bytes, message: str
) -> None:
    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "import":
            return _result(stdout)
        return _result(inspected)

    _, _, session = _session(monkeypatch, script)
    with pytest.raises(SandboxBackendError, match=message):
        asyncio.run(session.import_root(tmp_path / "root.tar"))
    assert session._image_id == _IMAGE


def test_close_removes_owned_images_even_after_a_failed_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listed = [f"sha256:{_IMPORTED}\n".encode(), b""]

    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[0] == "import":
            return _result(returncode=125)
        if args[:2] == ("image", "ls"):
            return _result(listed.pop(0))
        return _result()

    fake, _, session = _session(monkeypatch, script)
    with pytest.raises(SandboxBackendError):
        asyncio.run(session.import_root(tmp_path / "root.tar"))
    asyncio.run(session.close())
    removals = [call for call in fake.calls if call[:2] == ("image", "rm")]
    assert removals == [("image", "rm", _IMPORTED)]
    assert all(
        f"label=io.isolattice.sandbox={_SANDBOX}" in call
        for call in fake.calls[1:]
        if call[:2] == ("image", "ls")
    )


def test_close_reports_an_image_that_survives_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    def script(args: tuple[str, ...]) -> _cli.CommandResult:
        if args[:2] == ("image", "ls"):
            return _result(f"{_IMPORTED}\n".encode())
        return _result()

    _, _, session = _session(monkeypatch, script)
    session._import_attempted = True
    with pytest.raises((TerminationVerificationError, BaseExceptionGroup)) as raised:
        asyncio.run(session.close())
    assert (
        raised.group_contains(TerminationVerificationError)
        if isinstance(raised.value, BaseExceptionGroup)
        else isinstance(raised.value, TerminationVerificationError)
    )


def test_sessions_without_imports_never_touch_images(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _, session = _session(monkeypatch, lambda args: _result())
    asyncio.run(session.close())
    assert not [call for call in fake.calls if call[0] == "image"]
