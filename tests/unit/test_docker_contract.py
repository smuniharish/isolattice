"""Platform-independent contract tests for the Docker API adapter."""

import asyncio
import copy
from collections.abc import Callable
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isolattice import (
    Capability,
    CapabilityState,
    DockerBackend,
    ExecutionRequest,
    ResourceBudget,
    SandboxCapabilityError,
    SandboxPolicy,
)
from isolattice.backend import Boundary
from isolattice.backends import _bootstrap, docker

IMAGE_ID = "a" * 64
SANDBOX = "b" * 32
EXECUTION = "c" * 32


def engine(*, podman: bool = True) -> docker._Engine:
    return docker._Engine(
        executable="docker",
        host="unix:///run/docker.sock",
        command_timeout=5,
        image_id=IMAGE_ID,
        image_environment=("PATH=/usr/bin:/bin",),
        rootless=False,
        cgroup_v2=True,
        server="Podman Engine 6.1.4" if podman else "Docker Engine 29.0.0",
    )


def test_unavailable_report_supports_nothing() -> None:
    report = docker._report(False, "unreachable")
    assert not report.available
    assert report.error == "unreachable"
    assert set(report.capabilities) == set(Capability)
    assert {evidence.state for evidence in report.capabilities.values()} == {
        CapabilityState.UNSUPPORTED
    }


def test_available_report_never_claims_isolation_or_unimplemented_features() -> None:
    report = docker._report(True, engine=engine())
    states = {name: evidence.state for name, evidence in report.capabilities.items()}
    for capability in (
        Capability.FILESYSTEM_ISOLATION,
        Capability.NETWORK_ISOLATION,
        Capability.PROCESS_ISOLATION,
        Capability.VERIFIED_TERMINATION,
        Capability.ENVIRONMENT_ISOLATION,
        Capability.DISK_LIMIT,
    ):
        assert states[capability] is CapabilityState.LIMITED
    for capability in (
        Capability.WORKSPACE,
        Capability.SNAPSHOTS,
        Capability.CHECKPOINTS,
        Capability.INSTANCE_LIMITS,
    ):
        assert states[capability] is CapabilityState.UNSUPPORTED
    assert report.limitations[0].startswith("Containers share the host kernel")
    assert "Docker API served by Podman Engine 6.1.4; rootful engine." in report.limitations


def test_limits_need_cgroup_v2() -> None:
    unverified = docker._report(
        True, engine=docker._Engine("d", "h", 1, IMAGE_ID, (), True, False, "x")
    )
    for capability in (Capability.MEMORY_LIMIT, Capability.CPU_LIMIT, Capability.PROCESS_LIMIT):
        assert unverified.capabilities[capability].state is CapabilityState.UNSUPPORTED


@given(st.integers(min_value=1000, max_value=10**12))
def test_exact_cpu_quotas_round_trip(quota: int) -> None:
    cores = float(Decimal(quota) / 100000)
    if Decimal(str(cores)) * 100000 == quota:
        assert docker._cpu_quota(cores) == quota


@given(st.floats(min_value=1e-9, max_value=0.0099, allow_nan=False))
def test_tiny_cpu_budgets_are_rejected(cores: float) -> None:
    with pytest.raises(SandboxCapabilityError):
        docker._cpu_quota(cores)


@given(
    st.sampled_from(["host", "image"]),
    st.one_of(st.just(""), st.text(min_size=1).map(lambda text: "-" + text)),
)
def test_constructor_requires_explicit_values(field: str, value: str) -> None:
    arguments = {"host": "unix:///run/docker.sock", "image": "alpine:3.22", field: value}
    with pytest.raises(ValueError, match=f"{field} must be a nonempty explicit value"):
        DockerBackend(host=arguments["host"], image=arguments["image"])


def test_allow_rootful_must_be_a_bool() -> None:
    with pytest.raises(TypeError):
        DockerBackend(host="h", image="i", allow_rootful=1)  # pyrefly: ignore[bad-argument-type]


@pytest.mark.parametrize(
    "policy",
    [
        SandboxPolicy(workspace=True),
        SandboxPolicy(instance_budget=ResourceBudget(memory_bytes=2**26)),
        SandboxPolicy(read_only_root=False),
        SandboxPolicy(required_capabilities=(Capability.MEMORY_LIMIT,)),
        SandboxPolicy(memory_bytes=1024),
    ],
)
def test_unsupported_policies_are_rejected_before_contacting_the_engine(
    policy: SandboxPolicy, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(self: DockerBackend) -> docker._Engine:
        raise AssertionError("the engine must not be contacted")

    monkeypatch.setattr(DockerBackend, "_probe", forbidden)
    backend = DockerBackend(host="unix:///run/docker.sock", image="alpine:3.22")
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(backend.prepare(policy, SANDBOX))


@pytest.mark.parametrize(
    "capability",
    [Capability.FILESYSTEM_ISOLATION, Capability.WORKSPACE, Capability.SNAPSHOTS],
)
def test_required_limited_capabilities_fail_closed(
    capability: Capability, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def probe(self: DockerBackend) -> docker._Engine:
        return engine()

    monkeypatch.setattr(DockerBackend, "_probe", probe)
    workspace = capability in (Capability.WORKSPACE, Capability.SNAPSHOTS)
    policy = SandboxPolicy(
        workspace=workspace,
        required_capabilities=(capability,),
    )
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(DockerBackend(host="h", image="i").prepare(policy, SANDBOX))


def test_kernel_prelude_checks_every_isolation_signal() -> None:
    script = _bootstrap.KernelChecks(
        uid=65534, gid=65534, memory_bytes=2**26, cpu_quota=25000, process_limit=8
    ).script()
    for fragment in (
        "CapInh|CapPrm|CapEff|CapBnd|CapAmb",
        'NoNewPrivs) [ "$value" = 1 ]',
        'Seccomp) [ "$value" = 2 ]',
        "65534[[:space:]]65534[[:space:]]65534[[:space:]]65534",
        '[ "$seen" = 9 ]',
        f"-le {2**26}",
        "memory.swap.max",
        '"25000 100000"',
        '[ "$value" = "8" ]',
    ):
        assert fragment in script
    assert script.rstrip().endswith('exec "$@"')


@given(st.lists(st.text(alphabet=st.characters(blacklist_characters="\0"), min_size=1), min_size=1))
def test_prelude_passes_argv_verbatim_after_the_marker(argv: list[str]) -> None:
    wrapped = _bootstrap.command(_bootstrap.KernelChecks(uid=1, gid=1), argv, EXECUTION)
    assert wrapped[0] == "/bin/sh"
    assert wrapped[1] == "-c"
    assert wrapped[4] == f"{_bootstrap.READY_PREFIX}{EXECUTION}"
    assert list(wrapped[5:]) == argv


def valid_inspection(
    session: docker._DockerSession, request: ExecutionRequest
) -> dict[str, object]:
    command = _bootstrap.command(session._limits.checks(), request.argv, EXECUTION)
    environment = ["PATH=/usr/bin:/bin", "container=podman"]
    environment.extend(f"{key}={value}" for key, value in request.environment.items())
    return {
        "Id": "d" * 64,
        "Name": f"/isolattice-{SANDBOX}-{EXECUTION}",
        "Image": f"sha256:{IMAGE_ID}",
        "Mounts": [],
        "Config": {
            "Labels": {docker._OWNER_LABEL: SANDBOX, docker._EXECUTION_LABEL: EXECUTION},
            "User": "65534:65534",
            "WorkingDir": request.cwd,
            "Entrypoint": [command[0]],
            "Cmd": list(command[1:]),
            "Tty": False,
            "OpenStdin": False,
            "Healthcheck": {"Test": ["NONE"]},
            "Env": environment,
            "Volumes": None,
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "PidMode": "private",
            "UTSMode": "private",
            "IpcMode": "private",
            "CgroupnsMode": "private",
            "UsernsMode": "",
            "AutoRemove": False,
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges"],
            "Binds": [],
            "Devices": [],
            "Tmpfs": {"/tmp": "rw,size=1048576,mode=1777,nosuid,nodev"},
            "LogConfig": {"Type": "none"},
            "RestartPolicy": {"Name": "no"},
            "Memory": 2**26,
            "MemorySwap": 2**26,
            "OomKillDisable": False,
        },
    }


def _set(path: tuple[str, ...], value: object) -> Callable[[dict[str, object]], None]:
    def mutate(data: dict[str, object]) -> None:
        target = data
        for key in path[:-1]:
            nested = target[key]
            assert isinstance(nested, dict)
            target = nested
        target[path[-1]] = value

    return mutate


TAMPERING = {
    "network": _set(("HostConfig", "NetworkMode"), "bridge"),
    "writable root": _set(("HostConfig", "ReadonlyRootfs"), False),
    "privileged": _set(("HostConfig", "Privileged"), True),
    "host pid": _set(("HostConfig", "PidMode"), "host"),
    "host ipc": _set(("HostConfig", "IpcMode"), "host"),
    "host cgroupns": _set(("HostConfig", "CgroupnsMode"), "host"),
    "userns": _set(("HostConfig", "UsernsMode"), "host"),
    "added capability": _set(("HostConfig", "CapAdd"), ["SYS_ADMIN"]),
    "seccomp disabled": _set(
        ("HostConfig", "SecurityOpt"), ["no-new-privileges", "seccomp=unconfined"]
    ),
    "no nnp": _set(("HostConfig", "SecurityOpt"), []),
    "bind": _set(("HostConfig", "Binds"), ["/:/host"]),
    "device": _set(("HostConfig", "Devices"), [{"PathOnHost": "/dev/kmsg"}]),
    "mount": _set(("Mounts",), [{"Type": "volume"}]),
    "unsized tmpfs": _set(("HostConfig", "Tmpfs"), {"/tmp": "rw,nosuid,nodev"}),
    "extra tmpfs": _set(
        ("HostConfig", "Tmpfs"),
        {"/tmp": "rw,size=1048576,nosuid,nodev", "/var/tmp": "rw"},
    ),
    "logging": _set(("HostConfig", "LogConfig"), {"Type": "json-file"}),
    "restart": _set(("HostConfig", "RestartPolicy"), {"Name": "always"}),
    "memory": _set(("HostConfig", "Memory"), 2**27),
    "swap": _set(("HostConfig", "MemorySwap"), -1),
    "oom disabled": _set(("HostConfig", "OomKillDisable"), True),
    "user": _set(("Config", "User"), "0:0"),
    "image": _set(("Image",), f"sha256:{'e' * 64}"),
    "cwd": _set(("Config", "WorkingDir"), "/"),
    "entrypoint": _set(("Config", "Entrypoint"), ["/bin/bash"]),
    "command": _set(("Config", "Cmd"), ["-c", "true"]),
    "tty": _set(("Config", "Tty"), True),
    "stdin": _set(("Config", "OpenStdin"), True),
    "healthcheck": _set(("Config", "Healthcheck"), {"Test": ["CMD", "true"]}),
    "proxy injected": _set(
        ("Config", "Env"),
        ["PATH=/usr/bin:/bin", "container=podman", "GREETING=hi", "HTTP_PROXY=http://proxy"],
    ),
    "value changed": _set(
        ("Config", "Env"), ["PATH=/usr/bin:/bin", "container=podman", "GREETING=bye"]
    ),
    "name": _set(("Name",), "/other"),
}


@pytest.mark.parametrize("tamper", TAMPERING.values(), ids=TAMPERING.keys())
def test_inspection_rejects_any_weakened_container(
    tamper: Callable[[dict[str, object]], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    session = docker._DockerSession(
        engine(), docker._Limits(memory_bytes=2**26), scratch_bytes=1024 * 1024
    )
    request = ExecutionRequest(argv=("/bin/echo", "hi"), environment={"GREETING": "hi"}, cwd="/w")
    good = valid_inspection(session, request)
    boundary = Boundary("d" * 64, f"isolattice-{SANDBOX}-{EXECUTION}", SANDBOX, EXECUTION)
    current: dict[str, object] = good

    async def inspect(self: docker._DockerSession, target: Boundary) -> dict[str, object]:
        return current

    monkeypatch.setattr(docker._DockerSession, "_inspect", inspect)
    asyncio.run(session._validate(boundary, request))
    current = copy.deepcopy(good)
    tamper(current)
    with pytest.raises(SandboxCapabilityError):
        asyncio.run(session._validate(boundary, request))


def test_docker_engine_does_not_get_the_podman_environment_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = docker._DockerSession(engine(podman=False), docker._Limits(), scratch_bytes=None)
    request = ExecutionRequest(argv=("/bin/true",))
    data = valid_inspection(session, request)
    host = data["HostConfig"]
    assert isinstance(host, dict)
    host.update(Tmpfs=None, Memory=0, MemorySwap=0)
    boundary = Boundary("d" * 64, f"isolattice-{SANDBOX}-{EXECUTION}", SANDBOX, EXECUTION)

    async def inspect(self: docker._DockerSession, target: Boundary) -> dict[str, object]:
        return data

    monkeypatch.setattr(docker._DockerSession, "_inspect", inspect)
    with pytest.raises(SandboxCapabilityError, match="environment"):
        asyncio.run(session._validate(boundary, request))
