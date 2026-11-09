"""Platform-independent contract tests for the Kubernetes adapter."""

import asyncio
import copy
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    KubernetesBackend,
    NetworkAccess,
    ResourceBudget,
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxPolicy,
)
from isolattice.backend import Boundary
from isolattice.backends import _bootstrap, kubernetes
from isolattice.termination import BoundaryState, Observation

SANDBOX = "b" * 32
EXECUTION = "c" * 32
POD_UID = "0f0e0d0c-0b0a-4908-8706-050403020100"
NAME = f"isolattice-{EXECUTION}"


def cluster() -> kubernetes._Cluster:
    return kubernetes._Cluster(
        executable="kubectl",
        kubeconfig="kubeconfig",
        context="kind-test",
        namespace="isolattice-test",
        image="alpine:3.22",
        command_timeout=5,
        startup_timeout=30,
        server="v1.37.0",
    )


def backend() -> KubernetesBackend:
    return KubernetesBackend(
        kubeconfig="kubeconfig", context="kind-test", namespace="isolattice-test", image="alpine"
    )


def enabled(**kwargs: Any) -> SandboxPolicy:
    return SandboxPolicy(
        network=NetworkAccess.ENABLED,
        **kwargs,
    )


def test_unavailable_report_supports_nothing() -> None:
    report = kubernetes._report(False, "unreachable")
    assert not report.available
    assert report.error == "unreachable"
    assert set(report.capabilities) == set(Capability)
    assert {evidence.state for evidence in report.capabilities.values()} == {
        CapabilityState.UNSUPPORTED
    }


def test_available_report_never_claims_network_isolation_or_unimplemented_features() -> None:
    report = kubernetes._report(True, cluster=cluster())
    states = {name: evidence.state for name, evidence in report.capabilities.items()}
    for capability in (
        Capability.NETWORK_ISOLATION,
        Capability.PROCESS_LIMIT,
        Capability.SNAPSHOTS,
        Capability.CHECKPOINTS,
        Capability.INSTANCE_LIMITS,
        Capability.WORKSPACE,
    ):
        assert states[capability] is CapabilityState.UNSUPPORTED
    for capability in (
        Capability.FILESYSTEM_ISOLATION,
        Capability.PROCESS_ISOLATION,
        Capability.VERIFIED_TERMINATION,
        Capability.ENVIRONMENT_ISOLATION,
        Capability.DISK_LIMIT,
    ):
        assert states[capability] is CapabilityState.LIMITED
    assert report.limitations[0].startswith("Pods share the node kernel")
    assert "Kubernetes API v1.37.0; namespace isolattice-test." in report.limitations


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("64Mi", Decimal(64 * 2**20)),
        ("250m", Decimal("0.25")),
        ("1", Decimal(1)),
        ("1.5", Decimal("1.5")),
        ("8k", Decimal(8000)),
        ("2Gi", Decimal(2 * 2**30)),
    ],
)
def test_quantities_parse_exactly(text: str, value: Decimal) -> None:
    assert kubernetes._quantity(text) == value


@pytest.mark.parametrize("text", [None, 5, "", "1e3", "-1", "1 Mi", "Mi", "1.Mi", "1mi"])
def test_noncanonical_quantities_are_rejected(text: object) -> None:
    assert kubernetes._quantity(text) is None


@given(st.integers(min_value=0, max_value=2**62))
def test_byte_quantities_round_trip(value: int) -> None:
    assert kubernetes._quantity(str(value)) == value
    if value % 2**20 == 0:
        assert kubernetes._quantity(f"{value // 2**20}Mi") == value


@given(st.integers(min_value=10, max_value=10**6))
def test_millicpu_budgets_round_trip(millicpu: int) -> None:
    cores = float(Decimal(millicpu) / 1000)
    limits = kubernetes._Limits.from_policy(enabled(cpu_cores=cores))
    assert limits.millicpu == millicpu
    assert limits.resources()["cpu"] == f"{millicpu}m"
    assert limits.checks(await_start=True).cpu_quota == millicpu * 100


@given(st.floats(min_value=1e-9, max_value=0.0099, allow_nan=False))
def test_tiny_cpu_budgets_are_rejected(cores: float) -> None:
    with pytest.raises(SandboxCapabilityError):
        kubernetes._Limits.from_policy(enabled(cpu_cores=cores))


@pytest.mark.parametrize("cores", [0.0105, 1.0001])
def test_fractional_millicores_are_rejected(cores: float) -> None:
    with pytest.raises(SandboxCapabilityError):
        kubernetes._Limits.from_policy(enabled(cpu_cores=cores))


@given(st.integers(min_value=1, max_value=2**40))
def test_scratch_must_be_page_aligned(scratch: int) -> None:
    policy = enabled(scratch_bytes=scratch)
    if scratch % 4096:
        with pytest.raises(SandboxCapabilityError):
            kubernetes._Limits.from_policy(policy)
    else:
        limits = kubernetes._Limits.from_policy(policy)
        assert limits.checks(await_start=False).scratch_kib == scratch // 1024


def test_small_memory_budgets_are_rejected() -> None:
    with pytest.raises(SandboxCapabilityError):
        kubernetes._Limits.from_policy(enabled(memory_bytes=6 * 2**20 - 1))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kubeconfig", ""),
        ("context", "-x"),
        ("namespace", "Upper"),
        ("namespace", "a" * 64),
        ("image", "bad\0image"),
    ],
)
def test_constructor_requires_explicit_values(field: str, value: str) -> None:
    arguments = {
        "kubeconfig": "kubeconfig",
        "context": "ctx",
        "namespace": "isolattice-test",
        "image": "alpine",
        field: value,
    }
    with pytest.raises(ValueError, match=f"^{field} must be "):
        KubernetesBackend(
            kubeconfig=arguments["kubeconfig"],
            context=arguments["context"],
            namespace=arguments["namespace"],
            image=arguments["image"],
        )


def test_kubectl_always_receives_explicit_configuration() -> None:
    assert cluster().command("get", "pods") == (
        "kubectl",
        "--kubeconfig=kubeconfig",
        "--context=kind-test",
        "--namespace=isolattice-test",
        "get",
        "pods",
    )


@pytest.mark.parametrize(
    "policy",
    [
        SandboxPolicy(),
        enabled(read_only_root=False),
        enabled(workspace=True),
        enabled(instance_budget=ResourceBudget(memory_bytes=2**26)),
        enabled(process_limit=16),
        enabled(memory_bytes=1024),
        enabled(scratch_bytes=1000),
        enabled(required_capabilities=(Capability.MEMORY_LIMIT,)),
    ],
)
def test_unsupported_policies_are_rejected_before_contacting_the_cluster(
    policy: SandboxPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(self: KubernetesBackend) -> kubernetes._Cluster:
        raise AssertionError("the cluster must not be contacted")

    monkeypatch.setattr(KubernetesBackend, "_probe", forbidden)
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(backend().prepare(policy, SANDBOX))


@pytest.mark.parametrize(
    "capability",
    [Capability.NETWORK_ISOLATION, Capability.FILESYSTEM_ISOLATION, Capability.DISK_LIMIT],
)
def test_required_limited_capabilities_fail_closed(
    capability: Capability, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(self: KubernetesBackend) -> kubernetes._Cluster:
        return cluster()

    monkeypatch.setattr(KubernetesBackend, "_probe", probe)
    policy = enabled(required_capabilities=(capability,), scratch_bytes=4096)
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(backend().prepare(policy, SANDBOX))


def test_required_memory_and_cpu_are_accepted_with_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe(self: KubernetesBackend) -> kubernetes._Cluster:
        return cluster()

    monkeypatch.setattr(KubernetesBackend, "_probe", probe)
    policy = enabled(
        memory_bytes=2**26,
        cpu_cores=0.25,
        required_capabilities=(Capability.MEMORY_LIMIT, Capability.CPU_LIMIT),
    )
    session = asyncio.run(backend().prepare(policy, SANDBOX))
    assert session.backend_name == "kubernetes"


def test_prelude_checks_scratch_mount_and_gates_on_start() -> None:
    script = _bootstrap.KernelChecks(uid=65534, gid=65534, scratch_kib=8192, await_start=True)
    text = script.script()
    assert "/proc/self/mounts" in text
    assert ",rw,*,size=8192k,*" in text
    gate = text.index('IFS= read -r start && [ "$start" = "$1" ]')
    assert gate < text.index('exec "$@"')
    plain = _bootstrap.KernelChecks(uid=65534, gid=65534).script()
    assert "read -r start" not in plain
    assert "/proc/self/mounts" not in plain


def session(**kwargs: object) -> kubernetes._KubernetesSession:
    limits = kubernetes._Limits.from_policy(enabled(**kwargs))
    return kubernetes._KubernetesSession(cluster(), limits, 3)


def admitted(
    current: kubernetes._KubernetesSession, request: ExecutionRequest
) -> dict[str, object]:
    """Simulate API-server defaulting on the requested manifest."""
    pod = json.loads(json.dumps(current._manifest(request, NAME, SANDBOX, EXECUTION)))
    pod["metadata"]["uid"] = POD_UID
    pod["spec"]["dnsPolicy"] = "ClusterFirst"
    pod["spec"]["serviceAccountName"] = "default"
    container = pod["spec"]["containers"][0]
    container["terminationMessagePath"] = "/dev/termination-log"
    container["resources"]["limits"]["memory"] = "64Mi"
    container["resources"]["requests"]["cpu"] = "250m"
    if "volumes" in pod["spec"]:
        pod["spec"]["volumes"][0]["emptyDir"]["sizeLimit"] = "8Mi"
    return dict(pod)


REQUEST = ExecutionRequest(("/bin/echo", "hi"), environment={"GREETING": "hi"}, timeout=5)
BOUNDARY = Boundary(POD_UID, NAME, SANDBOX, EXECUTION)


def limited_session() -> kubernetes._KubernetesSession:
    return session(memory_bytes=2**26, cpu_cores=0.25, scratch_bytes=8 * 2**20)


def test_manifest_requests_every_control() -> None:
    current = limited_session()
    pod = current._manifest(REQUEST, NAME, SANDBOX, EXECUTION)
    spec = pod["spec"]
    assert isinstance(spec, dict)
    assert spec["restartPolicy"] == "Never"
    assert spec["automountServiceAccountToken"] is False
    assert spec["enableServiceLinks"] is False
    assert spec["activeDeadlineSeconds"] == 5 + 30 + 3
    container = spec["containers"][0]
    assert container["imagePullPolicy"] == "Never"
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["resources"]["limits"] == container["resources"]["requests"]
    assert container["command"][:2] == ["/bin/sh", "-c"]


def test_admitted_pod_with_server_defaults_is_accepted() -> None:
    current = limited_session()
    current._validate(BOUNDARY, admitted(current, REQUEST), REQUEST)


def _set(path: tuple[object, ...], value: object) -> Callable[[dict[str, object]], None]:
    def mutate(data: dict[str, object]) -> None:
        target: Any = data
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


C = ("spec", "containers", 0)
TAMPERING = {
    "name": _set(("metadata", "name"), "other"),
    "namespace": _set(("metadata", "namespace"), "default"),
    "owner": _set(("metadata", "labels", kubernetes._OWNER_LABEL), "x"),
    "restart": _set(("spec", "restartPolicy"), "Always"),
    "token": _set(("spec", "automountServiceAccountToken"), True),
    "links": _set(("spec", "enableServiceLinks"), True),
    "host network": _set(("spec", "hostNetwork"), True),
    "host pid": _set(("spec", "hostPID"), True),
    "shared pid": _set(("spec", "shareProcessNamespace"), True),
    "runtime class": _set(("spec", "runtimeClassName"), "unconfined"),
    "grace": _set(("spec", "terminationGracePeriodSeconds"), 30),
    "deadline": _set(("spec", "activeDeadlineSeconds"), None),
    "init": _set(("spec", "initContainers"), [{"name": "x"}]),
    "root": _set(("spec", "securityContext", "runAsUser"), 0),
    "nonroot": _set(("spec", "securityContext", "runAsNonRoot"), False),
    "sysctls": _set(("spec", "securityContext", "sysctls"), [{"name": "a", "value": "1"}]),
    "seccomp": _set(("spec", "securityContext", "seccompProfile"), {"type": "Unconfined"}),
    "volume": _set(("spec", "volumes"), [{"name": "scratch", "hostPath": {"path": "/"}}]),
    "disk scratch": _set(("spec", "volumes", 0, "emptyDir", "medium"), ""),
    "scratch size": _set(("spec", "volumes", 0, "emptyDir", "sizeLimit"), "16Mi"),
    "extra container": lambda pod: pod["spec"]["containers"].append({}),
    "escalation": _set((*C, "securityContext", "allowPrivilegeEscalation"), True),
    "privileged": _set((*C, "securityContext", "privileged"), True),
    "writable root": _set((*C, "securityContext", "readOnlyRootFilesystem"), False),
    "caps kept": _set((*C, "securityContext", "capabilities"), {"drop": []}),
    "caps added": _set((*C, "securityContext", "capabilities", "add"), ["SYS_ADMIN"]),
    "unmasked proc": _set((*C, "securityContext", "procMount"), "Unmasked"),
    "container root": _set((*C, "securityContext", "runAsUser"), 0),
    "image": _set((*C, "image"), "evil"),
    "pull": _set((*C, "imagePullPolicy"), "Always"),
    "command": _set((*C, "command"), ["/bin/sh", "-c", "id"]),
    "args": _set((*C, "args"), ["x"]),
    "cwd": _set((*C, "workingDir"), "/etc"),
    "env": _set((*C, "env"), [{"name": "HTTP_PROXY", "value": "x"}]),
    "env from": _set((*C, "envFrom"), [{"secretRef": {"name": "s"}}]),
    "tty": _set((*C, "tty"), True),
    "stdin once": _set((*C, "stdinOnce"), False),
    "mount": _set((*C, "volumeMounts"), [{"name": "scratch", "mountPath": "/"}]),
    "lifecycle": _set((*C, "lifecycle"), {"postStart": {}}),
    "memory": _set((*C, "resources", "limits", "memory"), "128Mi"),
    "cpu": _set((*C, "resources", "requests", "cpu"), "500m"),
    "no limits": _set((*C, "resources"), {}),
}


@pytest.mark.parametrize("mutation", TAMPERING.values(), ids=TAMPERING.keys())
def test_weakened_admission_is_rejected(mutation: Callable[[dict[str, object]], None]) -> None:
    current = limited_session()
    pod = copy.deepcopy(admitted(current, REQUEST))
    mutation(pod)
    with pytest.raises((SandboxCapabilityError, SandboxBackendError)):
        current._validate(BOUNDARY, pod, REQUEST)


def test_unlimited_pod_must_have_no_volumes() -> None:
    current = session()
    pod = admitted(current, REQUEST)
    current._validate(BOUNDARY, pod, REQUEST)
    _set(("spec", "volumes"), [{"name": "x", "emptyDir": {}}])(pod)
    with pytest.raises(SandboxCapabilityError):
        current._validate(BOUNDARY, pod, REQUEST)


def pod_with(state: object, *, uid: str = POD_UID, labels: object = None) -> bytes:
    return json.dumps(
        {
            "metadata": {
                "uid": uid,
                "namespace": "isolattice-test",
                "labels": labels
                if labels is not None
                else {kubernetes._OWNER_LABEL: SANDBOX, kubernetes._EXECUTION_LABEL: EXECUTION},
            },
            "status": {"containerStatuses": state},
        }
    ).encode()


def observe(stdout: bytes, monkeypatch: pytest.MonkeyPatch) -> Observation:
    async def invoke(*args: object, **kwargs: object) -> tuple[bytes, bytes, int]:
        return stdout, b"", 0

    monkeypatch.setattr(kubernetes, "_invoke", invoke)
    driver = kubernetes._KubernetesDriver(cluster(), session(), BOUNDARY)
    return asyncio.run(driver.observe())


@pytest.mark.parametrize(
    ("stdout", "state", "code"),
    [
        (b"", BoundaryState.ABSENT, None),
        (pod_with([], uid="ffffffff-0b0a-4908-8706-050403020100"), BoundaryState.ABSENT, None),
        (pod_with(None), BoundaryState.CREATED, None),
        (pod_with([{"name": "workload", "state": {"waiting": {}}}]), BoundaryState.CREATED, None),
        (pod_with([{"name": "workload", "state": {"running": {}}}]), BoundaryState.RUNNING, None),
        (
            pod_with([{"name": "workload", "state": {"terminated": {"exitCode": 7}}}]),
            BoundaryState.EXITED,
            7,
        ),
        (
            pod_with([{"name": "workload", "state": {"terminated": {"exitCode": "7"}}}]),
            BoundaryState.UNKNOWN,
            None,
        ),
        (
            pod_with([{"name": "workload", "state": {}}, {"name": "workload", "state": {}}]),
            BoundaryState.UNKNOWN,
            None,
        ),
        (pod_with([{"name": "workload", "state": {"other": {}}}]), BoundaryState.UNKNOWN, None),
    ],
)
def test_driver_maps_container_states(
    stdout: bytes, state: BoundaryState, code: int | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = observe(stdout, monkeypatch)
    assert observation.state is state
    assert observation.exit_code == code


def test_driver_refuses_pods_without_ownership_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SandboxBackendError):
        observe(pod_with([], labels={kubernetes._OWNER_LABEL: "other"}), monkeypatch)


def test_kubernetes_driver_releases_on_stop() -> None:
    assert kubernetes._KubernetesDriver.signal_releases is True


@pytest.mark.parametrize(
    ("returncode", "stderr", "accepted"),
    [
        (0, b"", True),
        (1, b'Error from server (NotFound): pods "x" not found', True),
        (1, b"Error from server (Conflict): precondition failed", True),
        (1, b"Error from server (Forbidden): denied", False),
        (1, b"connection refused", False),
    ],
)
def test_deletion_is_uid_preconditioned(
    returncode: int, stderr: bytes, accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def invoke(*args: object, **kwargs: object) -> tuple[bytes, bytes, int]:
        calls.append((args, kwargs))
        return b"", stderr, returncode

    monkeypatch.setattr(kubernetes, "_invoke", invoke)
    driver = kubernetes._KubernetesDriver(cluster(), session(), BOUNDARY)
    if accepted:
        asyncio.run(driver.signal(force=True))
    else:
        with pytest.raises(SandboxBackendError):
            asyncio.run(driver.signal(force=True))
    args, kwargs = calls[0]
    assert f"--raw=/api/v1/namespaces/isolattice-test/pods/{NAME}" in args
    body = json.loads(kwargs["stdin"])  # pyrefly: ignore[bad-argument-type]
    assert body["preconditions"] == {"uid": POD_UID}
    assert body["gracePeriodSeconds"] == 1
