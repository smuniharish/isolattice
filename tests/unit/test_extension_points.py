"""Contract tests for the local artifact store and public extension points."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sys
from pathlib import Path
from typing import override

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from isolattice import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactStore,
    CommandRunner,
    EventKind,
    LocalArtifactStore,
    LoggingEventSink,
    SandboxArtifactError,
    SandboxEvent,
    SubprocessRunner,
)
from isolattice import storage as storage_module
from isolattice.testing import ConformanceError, check_artifact_store

_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)


def _manifest(key: str, payload: bytes) -> ArtifactManifest:
    return ArtifactManifest(
        kind=ArtifactKind.WORKSPACE_SNAPSHOT,
        backend="fake",
        format="tar",
        archive=key,
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        created_at="2000-01-01T00:00:00+00:00",
    )


def _store(tmp_path: Path) -> LocalArtifactStore:
    root = tmp_path / "store"
    root.mkdir()
    return LocalArtifactStore(root)


def _archive(tmp_path: Path, payload: bytes, name: str = "archive") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_local_store_passes_the_conformance_check(tmp_path: Path) -> None:
    store = _store(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    asyncio.run(check_artifact_store(store, work))
    assert [path for path in store.root.rglob("*") if path.is_file()] == []


def test_local_store_validates_root_and_keys(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="root"):
        LocalArtifactStore(1)  # pyrefly: ignore[bad-argument-type]
    missing = LocalArtifactStore(tmp_path / "missing")
    assert missing.root == (tmp_path / "missing").resolve()
    payload = b"data"
    with pytest.raises(SandboxArtifactError, match="not a directory"):
        asyncio.run(missing.publish("k", _archive(tmp_path, payload), _manifest("k", payload)))
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="key"):
        asyncio.run(store.exists("../escape"))


@settings(max_examples=40, deadline=None)
@given(
    parts=st.lists(
        st.from_regex(r"[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,11}[A-Za-z0-9_-])?", fullmatch=True)
        .filter(lambda part: not part.endswith(".manifest.json"))
        .filter(lambda part: _DEVICE.match(part) is None),
        min_size=1,
        max_size=4,
    ),
    payload=st.binary(max_size=2048),
)
def test_local_store_round_trips_any_valid_key(parts: list[str], payload: bytes) -> None:
    import tempfile

    key = "/".join(parts)

    async def check(root: Path) -> None:
        store_root = root / "store"
        store_root.mkdir()
        store = LocalArtifactStore(store_root)
        await store.publish(key, _archive(root, payload), _manifest(key, payload))
        assert await store.exists(key)
        assert await store.fetch(key, root / "out", max_bytes=len(payload)) == _manifest(
            key, payload
        )
        assert (root / "out").read_bytes() == payload
        assert await store.delete(key)
        assert not await store.exists(key)

    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(check(Path(directory)))


def test_publication_falls_back_to_copying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    payload = b"payload" * 1000
    real_link = os.link
    archive = _archive(tmp_path, payload)

    def no_cross_device(source: os.PathLike[str] | str, target: os.PathLike[str] | str) -> None:
        if Path(source) == archive:
            raise OSError("cross-device link")
        real_link(source, target)

    monkeypatch.setattr(os, "link", no_cross_device)
    asyncio.run(store.publish("copied", archive, _manifest("copied", payload)))
    assert (store.root / "copied").read_bytes() == payload
    assert not list(store.root.glob(".*partial"))


def test_publication_races_and_failures_leave_no_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    payload = b"payload"
    archive = _archive(tmp_path, payload)
    real_link = os.link

    async def check() -> None:
        (store.root / "archive-race").write_bytes(b"other")
        with pytest.raises(SandboxArtifactError, match="already exists"):
            await store.publish("archive-race", archive, _manifest("archive-race", payload))
        assert (store.root / "archive-race").read_bytes() == b"other"

        (store.root / "manifest-race.manifest.json").write_bytes(b"{}")
        with pytest.raises(SandboxArtifactError, match="already exists"):
            await store.publish("manifest-race", archive, _manifest("manifest-race", payload))
        assert not (store.root / "manifest-race").exists()

        def manifest_link_fails(
            source: os.PathLike[str] | str, target: os.PathLike[str] | str
        ) -> None:
            if str(target).endswith(".manifest.json"):
                raise PermissionError("denied")
            real_link(source, target)

        monkeypatch.setattr(os, "link", manifest_link_fails)
        with pytest.raises(SandboxArtifactError, match="could not store"):
            await store.publish("denied", archive, _manifest("denied", payload))
        assert not (store.root / "denied").exists()
        monkeypatch.setattr(os, "link", real_link)

        (store.root / "blocked").write_bytes(b"")
        with pytest.raises(SandboxArtifactError, match="could not create"):
            await store.publish("blocked/k", archive, _manifest("blocked/k", payload))

    asyncio.run(check())
    assert not [path for path in store.root.rglob("*") if path.name.endswith(".partial")]


@pytest.mark.skipif(sys.platform == "win32", reason="directory symlinks need privileges")
def test_symbolic_links_cannot_redirect_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "link").symlink_to(outside, target_is_directory=True)
    payload = b"payload"
    (outside / "k").write_bytes(payload)
    (outside / "k.manifest.json").write_bytes(_manifest("link/k", payload).to_json())

    async def check() -> None:
        with pytest.raises(SandboxArtifactError, match="leaves the store root"):
            await store.publish("link/new", _archive(tmp_path, payload), _manifest("x", payload))
        with pytest.raises(SandboxArtifactError, match="leaves the store root"):
            await store.fetch("link/k", tmp_path / "out", max_bytes=100)

    asyncio.run(check())
    assert not (outside / "new").exists()


def test_fetch_bounds_and_reports_damage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    payload = b"payload" * 100

    async def check() -> None:
        await store.publish("k", _archive(tmp_path, payload), _manifest("k", payload))
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("k", tmp_path / "small", max_bytes=len(payload) - 1)
        with pytest.raises(SandboxArtifactError, match="does not exist"):
            await store.fetch("missing", tmp_path / "missing", max_bytes=10)

        record = store.root / "k.manifest.json"
        original = record.read_bytes()
        record.write_bytes(b"not json")
        with pytest.raises(SandboxArtifactError, match="malformed"):
            await store.fetch("k", tmp_path / "malformed", max_bytes=len(payload))

        record.write_bytes(_manifest("k", b"x").to_json())
        with pytest.raises(SandboxArtifactError, match="size limit"):
            await store.fetch("k", tmp_path / "grown", max_bytes=10)
        assert not (tmp_path / "grown").exists() or (tmp_path / "grown").stat().st_size <= 10
        record.write_bytes(original)

        (tmp_path / "taken").write_bytes(b"")
        with pytest.raises(SandboxArtifactError, match="archive is unreadable"):
            await store.fetch("k", tmp_path / "taken", max_bytes=len(payload))

        real_open = Path.open

        def unreadable(path: Path, *args: object, **kwargs: object) -> object:
            if path == record:
                raise PermissionError("denied")
            return real_open(path, *args, **kwargs)  # pyrefly: ignore[no-matching-overload]

        monkeypatch.setattr(Path, "open", unreadable)
        with pytest.raises(SandboxArtifactError, match="manifest is unreadable"):
            await store.fetch("k", tmp_path / "denied", max_bytes=len(payload))
        monkeypatch.setattr(Path, "open", real_open)

    asyncio.run(check())


def test_delete_reports_presence_and_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    payload = b"payload"

    async def check() -> None:
        assert not await store.delete("absent")
        await store.publish("k", _archive(tmp_path, payload), _manifest("k", payload))

        def denied(_: Path, *, missing_ok: bool = False) -> None:
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "unlink", denied)
        with pytest.raises(SandboxArtifactError, match="could not delete"):
            await store.delete("k")

    asyncio.run(check())


def test_copy_exclusive_enforces_its_limit(tmp_path: Path) -> None:
    source = _archive(tmp_path, b"x" * 10)
    with pytest.raises(SandboxArtifactError, match="size limit"):
        storage_module._copy_exclusive(source, tmp_path / "copy", 9)


class _BrokenStore(ArtifactStore):
    def __init__(self, inner: ArtifactStore, flaw: str) -> None:
        self.inner = inner
        self.flaw = flaw

    @override
    async def publish(self, key: str, archive: Path, manifest: ArtifactManifest) -> None:
        if self.flaw == "overwrite" and await self.inner.exists(key):
            return
        await self.inner.publish(key, archive, manifest)

    @override
    async def fetch(self, key: str, destination: Path, *, max_bytes: int) -> ArtifactManifest:
        if self.flaw == "unbounded":
            max_bytes = 1 << 30
        if self.flaw == "missing-ok" and not await self.inner.exists(key):
            await asyncio.to_thread(destination.write_bytes, b"")
            return _manifest(key, b"")
        manifest = await self.inner.fetch(key, destination, max_bytes=max_bytes)
        if self.flaw == "manifest":
            return _manifest(key, b"other")
        if self.flaw == "archive":
            await asyncio.to_thread(destination.write_bytes, b"other")
        return manifest

    @override
    async def exists(self, key: str) -> bool:
        if self.flaw == "always":
            return True
        if self.flaw == "never":
            return False
        if self.flaw == "sticky" and getattr(self, "deleted", False):
            return True
        return await self.inner.exists(key)

    @override
    async def delete(self, key: str) -> bool:
        self.deleted = True
        existed = await self.inner.delete(key)
        if self.flaw == "silent-delete":
            return False
        if self.flaw == "phantom-delete":
            return True
        return existed


@pytest.mark.parametrize(
    ("flaw", "message"),
    [
        ("always", "fresh key"),
        ("never", "published key was reported as missing"),
        ("overwrite", "existing key did not fail"),
        ("manifest", "fetched manifest differs"),
        ("archive", "fetched archive differs"),
        ("unbounded", "beyond max_bytes"),
        ("missing-ok", "missing key did not fail"),
        ("silent-delete", "reported no artifact"),
        ("sticky", "deleted key was reported as existing"),
        ("phantom-delete", "missing key reported an artifact"),
    ],
)
def test_conformance_check_detects_contract_violations(
    flaw: str, message: str, tmp_path: Path
) -> None:
    store = _BrokenStore(_store(tmp_path), flaw)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(ConformanceError, match=message):
        asyncio.run(check_artifact_store(store, work))


def test_conformance_check_requires_a_store(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="ArtifactStore"):
        asyncio.run(check_artifact_store(object(), tmp_path))  # pyrefly: ignore[bad-argument-type]


def test_events_are_immutable_and_validated() -> None:
    detail = {"key": "k"}
    event = SandboxEvent(EventKind.ARTIFACT_PUBLISHED, "s", "fake", detail=detail)
    detail["key"] = "changed"
    assert event.detail["key"] == "k"
    with pytest.raises(TypeError):
        event.detail["key"] = "x"  # pyrefly: ignore[unsupported-operation]
    assert not event.is_warning
    assert SandboxEvent(EventKind.TERMINATION_UNKNOWN, "s", "fake").is_warning
    with pytest.raises(TypeError, match="EventKind"):
        SandboxEvent("sandbox.created", "s", "fake")  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="mapping"):
        SandboxEvent(EventKind.SANDBOX_CREATED, "s", "fake", detail=[])  # pyrefly: ignore[bad-argument-type]


def test_logging_sink_levels_and_structured_extra(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(TypeError, match=r"logging\.Logger"):
        LoggingEventSink("isolattice")  # pyrefly: ignore[bad-argument-type]
    sink = LoggingEventSink()
    with caplog.at_level(logging.INFO, logger="isolattice"):
        sink.emit(SandboxEvent(EventKind.SANDBOX_CREATED, "s1", "fake"))
        sink.emit(
            SandboxEvent(
                EventKind.SANDBOX_CLEANUP_FAILED, "s1", "fake", "e1", detail={"b": 2, "a": 1}
            )
        )
    first, second = caplog.records
    assert first.levelno == logging.INFO
    assert first.getMessage() == "sandbox.created sandbox=s1 execution=- backend=fake "
    assert second.levelno == logging.WARNING
    assert second.getMessage().endswith("execution=e1 backend=fake a=1 b=2")
    assert second.isolattice_event.execution_id == "e1"  # pyrefly: ignore[missing-attribute]
    custom = logging.getLogger("custom-isolattice-test")
    with caplog.at_level(logging.INFO, logger="custom-isolattice-test"):
        LoggingEventSink(custom).emit(SandboxEvent(EventKind.SANDBOX_CLOSED, "s", "fake"))
    assert caplog.records[-1].name == "custom-isolattice-test"


def test_subprocess_runner_pipes_and_validates() -> None:
    async def check() -> None:
        runner: CommandRunner = SubprocessRunner()
        with pytest.raises(ValueError, match="argv"):
            await runner.spawn([], stdin=False)
        process = await runner.spawn(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin=True
        )
        stdout, stderr = await process.communicate(b"hello")
        assert (process.returncode, stdout.strip(), stderr) == (0, b"HELLO", b"")
        process = await runner.spawn(
            [sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], stdin=False
        )
        stdout, _ = await process.communicate()
        assert stdout.strip() == b"''"

    asyncio.run(check())
