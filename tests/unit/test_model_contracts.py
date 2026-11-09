"""Validation and serialization contracts of the public models."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    ArtifactKind,
    ArtifactManifest,
    ExecutionRequest,
    SandboxPolicy,
    TerminationPolicy,
    validate_artifact_key,
)
from isolattice.models import MAX_MANIFEST_BYTES

_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)
_SEGMENT = (
    st.from_regex(r"[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,19}[A-Za-z0-9_-])?", fullmatch=True)
    .filter(lambda segment: not segment.endswith(".manifest.json"))
    .filter(lambda segment: _DEVICE.match(segment) is None)
)
_DIGEST = "0" * 64


def _manifest(**changes: Any) -> ArtifactManifest:
    values: dict[str, Any] = {
        "kind": ArtifactKind.WORKSPACE_SNAPSHOT,
        "backend": "fake",
        "format": "tar",
        "archive": "k",
        "sha256": _DIGEST,
        "size": 1,
        "created_at": "2000-01-01T00:00:00+00:00",
    } | changes
    return ArtifactManifest(**values)


@given(st.lists(_SEGMENT, min_size=1, max_size=6))
def test_valid_keys_round_trip(parts: list[str]) -> None:
    key = "/".join(parts)
    assert validate_artifact_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/a",
        "a/",
        "a//b",
        "../a",
        "a/../b",
        ".hidden",
        "a b",
        "a\\b",
        "x" * 513,
        "k.manifest.json",
        "trailing.",
        "a../b",
        "CON",
        "runs/nul.txt",
        "Com1",
        "lpt9.tar",
    ],
)
def test_unsafe_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="key"):
        validate_artifact_key(key)


def test_non_string_keys_are_rejected() -> None:
    with pytest.raises(TypeError, match="string"):
        validate_artifact_key(b"k")


@given(
    kind=st.sampled_from(ArtifactKind),
    parts=st.lists(_SEGMENT, min_size=1, max_size=3),
    digest=st.from_regex(r"[0-9a-f]{64}", fullmatch=True),
    size=st.integers(min_value=0, max_value=2**62),
)
def test_manifests_round_trip_through_json(
    kind: ArtifactKind, parts: list[str], digest: str, size: int
) -> None:
    manifest = _manifest(kind=kind, archive="/".join(parts), sha256=digest, size=size)
    assert ArtifactManifest.from_json(manifest.to_json()) == manifest


def test_manifest_fields_are_validated() -> None:
    with pytest.raises(TypeError, match="ArtifactKind"):
        _manifest(kind="workspace-snapshot")
    for name in ("backend", "format", "created_at"):
        with pytest.raises(ValueError, match=name):
            _manifest(**{name: ""})
        with pytest.raises(ValueError, match=name):
            _manifest(**{name: "a\0b"})
    with pytest.raises(ValueError, match="sha256"):
        _manifest(sha256="A" * 64)
    with pytest.raises(ValueError, match="sha256"):
        _manifest(sha256=1)


def test_manifest_json_is_bounded_and_strict() -> None:
    with pytest.raises(ValueError, match="size limit"):
        ArtifactManifest.from_json(b" " * (MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match="valid JSON"):
        ArtifactManifest.from_json(b"\xff")
    with pytest.raises(ValueError, match="valid JSON"):
        ArtifactManifest.from_json(b"{")
    record = json.loads(_manifest().to_json())
    with pytest.raises(ValueError, match="unexpected fields"):
        ArtifactManifest.from_json(json.dumps(record | {"extra": 1}).encode())
    with pytest.raises(ValueError, match="unexpected fields"):
        ArtifactManifest.from_json(b"[]")
    with pytest.raises(ValueError, match="malformed"):
        ArtifactManifest.from_json(json.dumps(record | {"size": "1"}).encode())
    with pytest.raises(ValueError, match="ArtifactKind"):
        ArtifactManifest.from_json(json.dumps(record | {"kind": "other"}).encode())


def test_policy_and_request_types_are_validated() -> None:
    with pytest.raises(TypeError, match="workspace"):
        SandboxPolicy(workspace=1)  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="TerminationPolicy"):
        SandboxPolicy(termination={})  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="number"):
        TerminationPolicy(graceful_timeout=True)
    with pytest.raises(TypeError, match="number"):
        TerminationPolicy(force_timeout="1")  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="argv"):
        ExecutionRequest(argv="python")  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(TypeError, match="environment"):
        ExecutionRequest(argv=("python",), environment=[("A", "B")])  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(ValueError, match="cwd"):
        ExecutionRequest(argv=("python",), cwd="/a\0b")
    with pytest.raises(ValueError, match="cwd"):
        ExecutionRequest(argv=("python",), cwd=1)  # pyrefly: ignore[bad-argument-type]
