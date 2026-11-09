"""Pytest configuration, Hypothesis profiles and live-engine fixtures."""

import os
import sys

import pytest
from hypothesis import HealthCheck, settings

settings.register_profile(
    "default",
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    max_examples=300,
    derandomize=True,
    print_blob=True,
)
settings.register_profile(
    "thorough",
    parent=settings.get_profile("default"),
    max_examples=2_000,
    print_blob=True,
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


def _connection(prefix: str) -> tuple[str, str]:
    connection = os.environ.get(f"{prefix}_CONNECTION")
    image = os.environ.get(f"{prefix}_IMAGE")
    if not connection and not image:
        pytest.skip(f"set both {prefix}_CONNECTION and {prefix}_IMAGE")
    if not connection or not image:
        pytest.fail(f"both {prefix}_CONNECTION and {prefix}_IMAGE are required")
    return connection, image


def _resource_connection() -> tuple[str, str, bool]:
    connection, image = _connection("ISOLATTICE_TEST_PODMAN_RESOURCE")
    rootful = os.environ.get("ISOLATTICE_TEST_PODMAN_RESOURCE_ROOTFUL", "0")
    if rootful not in ("0", "1"):
        pytest.fail("ISOLATTICE_TEST_PODMAN_RESOURCE_ROOTFUL must be 0 or 1")
    return connection, image, rootful == "1"


@pytest.fixture
def podman_config() -> tuple[str, str]:
    return _connection("ISOLATTICE_TEST_PODMAN")


@pytest.fixture
def podman_resources_config() -> tuple[str, str, bool]:
    return _resource_connection()


@pytest.fixture
def podman_rootful_config() -> tuple[str, str]:
    """Explicit rootful engine for pod budgets and CRIU; only owned resources are touched."""
    return _connection("ISOLATTICE_TEST_PODMAN_ROOTFUL")


def _checkpoint_connection() -> tuple[str, str]:
    connection, image = _connection("ISOLATTICE_TEST_PODMAN_ROOTFUL")
    if sys.platform == "linux" and os.geteuid() != 0:
        pytest.skip("Podman's Linux client refuses checkpoint and restore unless run as root")
    return connection, image


@pytest.fixture
def podman_checkpoint_config() -> tuple[str, str]:
    """Rootful engine driven by a client Podman lets checkpoint (root on Linux)."""
    return _checkpoint_connection()


def _required(prefix: str, *names: str) -> tuple[str, ...]:
    values = tuple(os.environ.get(f"{prefix}_{name}", "") for name in names)
    if not any(values):
        pytest.skip(f"set {', '.join(f'{prefix}_{name}' for name in names)}")
    if not all(values):
        pytest.fail(f"all of {', '.join(f'{prefix}_{name}' for name in names)} are required")
    return values


def _flag(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in ("0", "1"):
        pytest.fail(f"{name} must be 0 or 1")
    return value == "1"


@pytest.fixture
def docker_config() -> tuple[str, str, str | None, bool]:
    """Explicit Docker API endpoint; only labelled containers are created or removed."""
    host, image = _required("ISOLATTICE_TEST_DOCKER", "HOST", "IMAGE")
    executable = os.environ.get("ISOLATTICE_TEST_DOCKER_EXECUTABLE") or None
    return host, image, executable, _flag("ISOLATTICE_TEST_DOCKER_ROOTFUL")


@pytest.fixture
def kubernetes_config() -> tuple[str, str, str, str, str | None]:
    """Explicit kubeconfig, context, namespace and node-local image of a disposable cluster."""
    kubeconfig, context, namespace, image = _required(
        "ISOLATTICE_TEST_KUBERNETES", "KUBECONFIG", "CONTEXT", "NAMESPACE", "IMAGE"
    )
    executable = os.environ.get("ISOLATTICE_TEST_KUBERNETES_EXECUTABLE") or None
    return kubeconfig, context, namespace, image, executable


@pytest.fixture
def s3_config() -> tuple[str, str, str, str]:
    """Explicit S3-compatible endpoint and existing bucket; tests use a unique key prefix."""
    endpoint, bucket, access_key_id, secret_access_key = _required(
        "ISOLATTICE_TEST_S3", "ENDPOINT", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"
    )
    return endpoint, bucket, access_key_id, secret_access_key


@pytest.fixture
def linux_cgroup_root() -> str:
    """Delegated, process-free cgroup subtree, e.g. from isolattice-delegated-run.sh."""
    root = os.environ.get("ISOLATTICE_TEST_LINUX_CGROUP_ROOT")
    if not root:
        pytest.skip("set ISOLATTICE_TEST_LINUX_CGROUP_ROOT to a delegated cgroup subtree")
    return root


@pytest.fixture
def example_config(request: pytest.FixtureRequest) -> tuple[str, str, bool]:
    profile: object = request.param
    if profile == "podman":
        connection, image = _connection("ISOLATTICE_TEST_PODMAN")
        return connection, image, False
    if profile == "podman-resources":
        return _resource_connection()
    if profile == "podman-rootful":
        connection, image = _connection("ISOLATTICE_TEST_PODMAN_ROOTFUL")
        return connection, image, True
    if profile == "podman-checkpoint":
        connection, image = _checkpoint_connection()
        return connection, image, True
    pytest.fail(f"unknown example profile: {profile!r}")
