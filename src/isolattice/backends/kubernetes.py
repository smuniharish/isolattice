"""Explicit Kubernetes Pod execution through ``kubectl`` with kernel-verified limits.

Each execution is one owned Pod in an explicit namespace of an explicit
kubeconfig context. The Pod is inspected after admission, its kernel-visible
isolation and cgroup limits are verified from inside before the workload is
exec'd, and termination uses UID-preconditioned deletion confirmed by the API
server reporting the Pod gone.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, NoReturn, override

from .._async import raise_combined, settle
from ..backend import Backend, BackendSession, Boundary
from ..commands import CommandRunner, SubprocessRunner
from ..errors import (
    SandboxBackendError,
    SandboxCapabilityError,
    SandboxExecutionError,
    TerminationVerificationError,
)
from ..models import (
    BackendReport,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    ExecutionRequest,
    NetworkAccess,
    SandboxPolicy,
    TerminationPolicy,
    TerminationReport,
    _positive_number,
)
from ..termination import BoundaryState, Observation, TerminationDriver, terminate_boundary
from . import _bootstrap
from ._cli import CommandError, json_object, mapping, reap_client, run_command, string_list

if TYPE_CHECKING:
    from collections.abc import Callable

_PRODUCT = "Kubernetes"
_OWNER_LABEL = "io.isolattice.sandbox"
_EXECUTION_LABEL = "io.isolattice.execution"
_CONTAINER = "workload"
_SCRATCH_VOLUME = "scratch"
_USER = 65534
_PAGE = 4096
_ATTACH_ATTEMPTS = 5
_UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_DNS_LABEL = re.compile(r"[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?\Z")
_QUANTITY = re.compile(r"([0-9]+(?:\.[0-9]+)?)(m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?\Z")
_SCALE: dict[str | None, Decimal] = {
    None: Decimal(1),
    "m": Decimal("0.001"),
    **{suffix: Decimal(1000) ** power for power, suffix in enumerate("kMGTPE", 1)},
    **{f"{suffix}i": Decimal(1024) ** power for power, suffix in enumerate("KMGTPE", 1)},
}


def _quantity(value: object) -> Decimal | None:
    """Parse a canonical Kubernetes quantity; exponent forms are rejected."""
    if not isinstance(value, str):
        return None
    match = _QUANTITY.fullmatch(value)
    if match is None:
        return None
    number: str = match.group(1)
    suffix: str | None = match.group(2)
    try:
        return Decimal(number) * _SCALE[suffix]
    except InvalidOperation:
        return None


_DEFAULT_RUNNER = SubprocessRunner()


@dataclass(frozen=True, slots=True)
class _Cluster:
    executable: str
    kubeconfig: str
    context: str
    namespace: str
    image: str
    command_timeout: float
    startup_timeout: float
    server: str
    runner: CommandRunner = _DEFAULT_RUNNER

    def command(self, *args: str) -> tuple[str, ...]:
        return (
            self.executable,
            f"--kubeconfig={self.kubeconfig}",
            f"--context={self.context}",
            f"--namespace={self.namespace}",
            *args,
        )


async def _invoke(
    cluster: _Cluster,
    operation: str,
    *args: str,
    stdin: bytes | None = None,
    accepted: tuple[int, ...] = (0,),
) -> tuple[bytes, bytes, int]:
    result = await run_command(
        cluster.runner,
        cluster.command(*args),
        product=_PRODUCT,
        operation=operation,
        timeout=cluster.command_timeout,
        accepted=accepted,
        stdin=stdin,
    )
    return result.stdout, result.stderr, result.returncode


@dataclass(frozen=True, slots=True)
class _Limits:
    memory_bytes: int | None = None
    millicpu: int | None = None
    scratch_bytes: int | None = None

    @classmethod
    def from_policy(cls, policy: SandboxPolicy) -> _Limits:
        memory = policy.memory_bytes
        if memory is not None and not 6 * 1024 * 1024 <= memory < 2**63:
            raise SandboxCapabilityError("Kubernetes memory budget must be 6 MiB to 2^63-1")
        millicpu: int | None = None
        if policy.cpu_cores is not None:
            exact = Decimal(str(policy.cpu_cores)) * 1000
            if exact != exact.to_integral_value() or not 10 <= exact < 2**53:
                raise SandboxCapabilityError(
                    "Kubernetes CPU budget must be a whole number of millicores of at least 10m"
                )
            millicpu = int(exact)
        scratch = policy.scratch_bytes
        if scratch is not None and (scratch % _PAGE or scratch >= 2**63):
            raise SandboxCapabilityError(
                "Kubernetes scratch budget must be a multiple of 4096 bytes so the "
                "kernel-reported tmpfs size equals the request"
            )
        return cls(memory, millicpu, scratch)

    def resources(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.memory_bytes is not None:
            values["memory"] = str(self.memory_bytes)
        if self.millicpu is not None:
            values["cpu"] = f"{self.millicpu}m"
        return values

    def checks(self, *, await_start: bool) -> _bootstrap.KernelChecks:
        return _bootstrap.KernelChecks(
            uid=_USER,
            gid=_USER,
            memory_bytes=self.memory_bytes,
            cpu_quota=self.millicpu * 100 if self.millicpu is not None else None,
            scratch_kib=self.scratch_bytes // 1024 if self.scratch_bytes is not None else None,
            await_start=await_start,
        )


def _report(
    available: bool, error: str | None = None, *, cluster: _Cluster | None = None
) -> BackendReport:
    limited = CapabilityState.LIMITED if available else CapabilityState.UNSUPPORTED
    supported = CapabilityState.SUPPORTED if available else CapabilityState.UNSUPPORTED
    unsupported = CapabilityState.UNSUPPORTED
    capabilities = {
        Capability.FILESYSTEM_ISOLATION: CapabilityEvidence(
            limited,
            "read-only image root without host paths; the kubelet adds a writable "
            "/dev/termination-log file on the node disk, /dev/shm and read-only /etc/hosts",
        ),
        Capability.NETWORK_ISOLATION: CapabilityEvidence(
            unsupported,
            "Pods always join the cluster network; NetworkPolicy enforcement depends on "
            "the CNI plug-in and cannot be verified by Isolattice",
        ),
        Capability.PROCESS_ISOLATION: CapabilityEvidence(
            limited,
            "private PID namespace; zero capabilities, no-new-privileges, seccomp filter mode "
            "and the unprivileged UID are read from /proc/self/status before exec",
        ),
        Capability.VERIFIED_TERMINATION: CapabilityEvidence(
            limited,
            "UID-preconditioned Pod deletion, confirmed when the API server no longer "
            "reports the Pod; the API server, kubelet and container runtime are trusted",
        ),
        Capability.ENVIRONMENT_ISOLATION: CapabilityEvidence(
            limited,
            "the image environment, HOSTNAME and the KUBERNETES_SERVICE_* variables are "
            "always present; service links and service-account tokens are disabled",
        ),
        Capability.RESOURCE_LIMITS: CapabilityEvidence(
            limited,
            "per-container cgroup v2 memory and CPU limits verified from inside the workload",
        ),
        Capability.MEMORY_LIMIT: CapabilityEvidence(
            supported,
            "container memory.max with swap disabled, read from the workload cgroup before "
            "exec, failing closed",
        ),
        Capability.CPU_LIMIT: CapabilityEvidence(
            supported,
            "container cpu.max quota over 100 ms periods, read from the workload cgroup "
            "before exec, failing closed",
        ),
        Capability.PROCESS_LIMIT: CapabilityEvidence(
            unsupported, "Kubernetes only offers a node-wide per-Pod PID limit"
        ),
        Capability.SNAPSHOTS: CapabilityEvidence(unsupported, "not implemented for Kubernetes"),
        Capability.CHECKPOINTS: CapabilityEvidence(unsupported, "not implemented for Kubernetes"),
        Capability.INSTANCE_LIMITS: CapabilityEvidence(
            unsupported, "Pods of one sandbox do not share an aggregate cgroup"
        ),
        Capability.DISK_LIMIT: CapabilityEvidence(
            limited,
            "memory-backed emptyDir at /tmp whose tmpfs size is read from /proc/self/mounts; "
            "the kubelet's /dev/termination-log file is writable and not size-capped",
        ),
        Capability.WORKSPACE: CapabilityEvidence(unsupported, "not implemented for Kubernetes"),
    }
    return BackendReport(
        name="kubernetes",
        available=available,
        capabilities=capabilities,
        limitations=(
            "Pods share the node kernel; kernel, runtime or cluster exploits are out of scope.",
            "Networking is always enabled; policies must request network=ENABLED.",
            "Each execution is a separate Pod; nothing persists between executions.",
            "The cluster control plane, node, image and admission chain are trusted.",
            "The image must already be on the node and provide a POSIX /bin/sh.",
            "Workload output is also kept in node container logs until the Pod is deleted.",
            "Environment values and command arguments are visible to namespace readers.",
            f"Kubernetes API {cluster.server}; namespace {cluster.namespace}."
            if cluster is not None
            else "Cluster identity has not been established.",
        ),
        error=error,
    )


def _status_error(stderr: bytes) -> str | None:
    text = stderr.decode("utf-8", "replace")
    for reason in ("NotFound", "Conflict"):
        if f"({reason})" in text:
            return reason
    return None


class KubernetesBackend(Backend):
    """Run each execution as an owned Pod in one explicit namespace.

    ``kubeconfig``, ``context`` and ``namespace`` are always passed to
    ``kubectl`` so ambient configuration is never consulted. ``image`` must be
    present on the node because Pods are created with ``imagePullPolicy: Never``.
    """

    def __init__(
        self,
        *,
        kubeconfig: str,
        context: str,
        namespace: str,
        image: str,
        executable: str | None = None,
        command_timeout: float = 15.0,
        startup_timeout: float = 60.0,
        runner: CommandRunner | None = None,
    ) -> None:
        for name, value in (
            ("kubeconfig", kubeconfig),
            ("context", context),
            ("namespace", namespace),
            ("image", image),
        ):
            if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value:
                raise ValueError(f"{name} must be a nonempty explicit value")
        if _DNS_LABEL.fullmatch(namespace) is None:
            raise ValueError("namespace must be a DNS-1123 label")
        if executable is not None and (not isinstance(executable, str) or not executable):
            raise ValueError("executable must be a nonempty path or command")
        _positive_number(command_timeout, "command_timeout")
        _positive_number(startup_timeout, "startup_timeout")
        self._kubeconfig = kubeconfig
        self._context = context
        self._namespace = namespace
        self._image = image
        self._executable = executable
        self._command_timeout = command_timeout
        self._startup_timeout = startup_timeout
        if runner is not None and not isinstance(runner, CommandRunner):
            raise TypeError("runner must be a CommandRunner")
        self._runner = runner if runner is not None else _DEFAULT_RUNNER

    async def _probe(self) -> _Cluster:
        executable = self._executable or shutil.which("kubectl")
        if executable is None:
            raise SandboxBackendError("kubectl executable was not found")
        cluster = _Cluster(
            executable,
            self._kubeconfig,
            self._context,
            self._namespace,
            self._image,
            self._command_timeout,
            self._startup_timeout,
            "",
            self._runner,
        )
        stdout, _, _ = await _invoke(cluster, "server discovery", "version", "--output=json")
        version = mapping(
            json_object(stdout, _PRODUCT, "server discovery").get("serverVersion"),
            _PRODUCT,
            "server version",
        )
        git_version = version.get("gitVersion")
        if not isinstance(git_version, str):
            raise SandboxBackendError("Kubernetes did not report its server version")
        stdout, _, _ = await _invoke(
            cluster, "namespace discovery", "get", "namespace", self._namespace, "--output=json"
        )
        namespace = json_object(stdout, _PRODUCT, "namespace discovery")
        metadata = mapping(namespace.get("metadata"), _PRODUCT, "namespace metadata")
        status = mapping(namespace.get("status"), _PRODUCT, "namespace status")
        if metadata.get("name") != self._namespace or status.get("phase") != "Active":
            raise SandboxBackendError("the configured Kubernetes namespace is not active")
        return _Cluster(
            executable,
            self._kubeconfig,
            self._context,
            self._namespace,
            self._image,
            self._command_timeout,
            self._startup_timeout,
            git_version,
            self._runner,
        )

    @override
    async def discover(self) -> BackendReport:
        try:
            cluster = await self._probe()
        except (SandboxBackendError, SandboxCapabilityError) as error:
            return _report(False, str(error))
        return _report(True, cluster=cluster)

    @override
    async def prepare(self, policy: SandboxPolicy, sandbox_id: str) -> BackendSession:
        if policy.network is not NetworkAccess.ENABLED:
            raise SandboxCapabilityError(
                "Kubernetes Pods always join the cluster network and Isolattice cannot "
                "verify NetworkPolicy enforcement; set network=NetworkAccess.ENABLED "
                "to accept that explicitly"
            )
        if not policy.read_only_root:
            raise SandboxCapabilityError("a writable container root is not implemented")
        if policy.workspace:
            raise SandboxCapabilityError("Kubernetes workspaces are not implemented")
        if policy.instance_budget is not None:
            raise SandboxCapabilityError("Kubernetes instance budgets are not implemented")
        if policy.process_limit is not None:
            raise SandboxCapabilityError(
                "Kubernetes has no per-Pod process limit; only a node-wide PID limit exists"
            )
        limits = _Limits.from_policy(policy)
        budgets = {
            Capability.MEMORY_LIMIT: limits.memory_bytes,
            Capability.CPU_LIMIT: limits.millicpu,
        }
        for capability in policy.required_capabilities:
            if capability in budgets and budgets[capability] is None:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} needs an explicit budget"
                )
        cluster = await self._probe()
        report = _report(True, cluster=cluster)
        for capability in policy.required_capabilities:
            evidence = report.capabilities[capability]
            if evidence.state is not CapabilityState.SUPPORTED:
                raise SandboxCapabilityError(
                    f"required capability {capability.value} is {evidence.state.value}: "
                    f"{evidence.detail}"
                )
        grace = max(1, math.ceil(policy.termination.graceful_timeout))
        return _KubernetesSession(cluster, limits, grace)


class _KubernetesSession(BackendSession):
    backend_name = "kubernetes"

    def __init__(self, cluster: _Cluster, limits: _Limits, grace_seconds: int) -> None:
        self._cluster = cluster
        self._limits = limits
        self._grace_seconds = grace_seconds

    def _deadline(self, request: ExecutionRequest) -> int | None:
        if request.timeout is None:
            return None
        # A server-side backstop if this client disappears; the runtime enforces the timeout.
        return math.ceil(request.timeout + self._cluster.startup_timeout + self._grace_seconds)

    def _manifest(
        self, request: ExecutionRequest, name: str, sandbox_id: str, execution_id: str
    ) -> dict[str, object]:
        resources = self._limits.resources()
        container: dict[str, object] = {
            "name": _CONTAINER,
            "image": self._cluster.image,
            "imagePullPolicy": "Never",
            "command": list(
                _bootstrap.command(
                    self._limits.checks(await_start=True), request.argv, execution_id
                )
            ),
            "workingDir": request.cwd,
            "env": [{"name": key, "value": value} for key, value in request.environment.items()],
            "stdin": True,
            "stdinOnce": True,
            "tty": False,
            "resources": {"limits": resources, "requests": resources},
            "securityContext": {
                "allowPrivilegeEscalation": False,
                "privileged": False,
                "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]},
            },
        }
        spec: dict[str, object] = {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": self._grace_seconds,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": _USER,
                "runAsGroup": _USER,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [container],
        }
        deadline = self._deadline(request)
        if deadline is not None:
            spec["activeDeadlineSeconds"] = deadline
        if self._limits.scratch_bytes is not None:
            spec["volumes"] = [
                {
                    "name": _SCRATCH_VOLUME,
                    "emptyDir": {"medium": "Memory", "sizeLimit": str(self._limits.scratch_bytes)},
                }
            ]
            container["volumeMounts"] = [{"name": _SCRATCH_VOLUME, "mountPath": "/tmp"}]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self._cluster.namespace,
                "labels": {_OWNER_LABEL: sandbox_id, _EXECUTION_LABEL: execution_id},
            },
            "spec": spec,
        }

    @override
    async def create_boundary(
        self,
        request: ExecutionRequest,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> Boundary:
        name = f"isolattice-{execution_id}"
        if _DNS_LABEL.fullmatch(name) is None or _DNS_LABEL.fullmatch(sandbox_id) is None:
            raise SandboxBackendError("Isolattice identifiers are not valid Kubernetes names")
        manifest = json.dumps(self._manifest(request, name, sandbox_id, execution_id))
        try:
            stdout, _, _ = await _invoke(
                self._cluster,
                "Pod creation",
                "create",
                "--filename=-",
                "--output=json",
                stdin=manifest.encode(),
            )
            try:
                pod = json_object(stdout, _PRODUCT, "Pod creation")
                metadata = mapping(pod.get("metadata"), _PRODUCT, "Pod metadata")
                uid = metadata.get("uid")
                if not isinstance(uid, str) or _UID.fullmatch(uid) is None:
                    raise SandboxBackendError("Kubernetes did not return a Pod UID")
            except SandboxBackendError as error:
                raise CommandError(
                    "Kubernetes returned an invalid Pod", possibly_accepted=True
                ) from error
        except BaseException as error:
            if isinstance(error, CommandError) and not error.possibly_accepted:
                raise
            await self._reconcile_after(error, name, sandbox_id, execution_id, register)
        boundary = Boundary(uid, name, sandbox_id, execution_id)
        register(boundary)
        self._validate(boundary, pod, request)
        return boundary

    async def _reconcile_after(
        self,
        error: BaseException,
        name: str,
        sandbox_id: str,
        execution_id: str,
        register: Callable[[Boundary], None],
    ) -> NoReturn:
        """Register any Pod the uncertain create allocated, then re-raise."""
        try:
            candidate = await self._reconcile(name, sandbox_id, execution_id)
        except BaseException as reconciliation:
            unknown = TerminationVerificationError(
                f"Kubernetes creation outcome is unknown for owned Pod {name}; "
                "the configured namespace could not be reconciled"
            )
            causes = BaseExceptionGroup(
                "Kubernetes creation and reconciliation failures", [error, reconciliation]
            )
            if isinstance(causes, Exception):
                raise unknown from causes
            raise BaseExceptionGroup(
                "Kubernetes creation was interrupted and ownership is unknown",
                [error, reconciliation, unknown],
            ) from None
        if candidate is not None:
            register(candidate)
            if not isinstance(error, Exception):
                error.add_note("Kubernetes allocated an owned Pod; cleanup is required")
                raise error
            raise SandboxBackendError(
                "Kubernetes creation failed after allocation; cleanup is required"
            ) from error
        unknown = TerminationVerificationError(
            f"Kubernetes creation outcome is unknown for owned Pod {name}; "
            "reconcile this name in the configured namespace"
        )
        if not isinstance(error, Exception):
            raise BaseExceptionGroup(
                "Kubernetes creation was interrupted and ownership is unknown", [error, unknown]
            ) from None
        raise unknown from error

    async def _reconcile(self, name: str, sandbox_id: str, execution_id: str) -> Boundary | None:
        stdout, _, _ = await _invoke(
            self._cluster,
            "Pod reconciliation",
            "get",
            "pods",
            f"--selector={_EXECUTION_LABEL}={execution_id}",
            "--output=json",
        )
        listing = json_object(stdout, _PRODUCT, "Pod reconciliation")
        items = listing.get("items")
        if not isinstance(items, list):
            raise SandboxBackendError("Kubernetes returned an invalid Pod list")
        candidates: list[Boundary] = []
        for item in items:
            metadata = mapping(mapping(item, _PRODUCT, "Pod").get("metadata"), _PRODUCT, "meta")
            uid = metadata.get("uid")
            if metadata.get("name") != name:
                continue
            if not isinstance(uid, str) or _UID.fullmatch(uid) is None:
                raise SandboxBackendError("Kubernetes returned a Pod without a valid UID")
            candidate = Boundary(uid, name, sandbox_id, execution_id)
            if await self._inspect(candidate) is not None:
                candidates.append(candidate)
        if len(candidates) > 1:
            raise SandboxBackendError("Kubernetes reported several Pods for one owned name")
        return candidates[0] if candidates else None

    async def _inspect(self, boundary: Boundary) -> dict[str, object] | None:
        """Return the owned Pod, or ``None`` when no Pod with its UID exists."""
        stdout, _, _ = await _invoke(
            self._cluster,
            "Pod inspection",
            "get",
            "pod",
            boundary.name,
            "--ignore-not-found",
            "--output=json",
        )
        if not stdout.strip():
            return None
        pod = json_object(stdout, _PRODUCT, "Pod inspection")
        metadata = mapping(pod.get("metadata"), _PRODUCT, "Pod metadata")
        if metadata.get("uid") != boundary.boundary_id:
            return None
        labels = mapping(metadata.get("labels"), _PRODUCT, "Pod labels")
        if (
            metadata.get("namespace") != self._cluster.namespace
            or labels.get(_OWNER_LABEL) != boundary.sandbox_id
            or labels.get(_EXECUTION_LABEL) != boundary.execution_id
        ):
            raise SandboxBackendError("refusing to operate on a Pod without ownership labels")
        return pod

    def _validate(
        self, boundary: Boundary, pod: dict[str, object], request: ExecutionRequest
    ) -> None:
        """Check the admitted Pod; mutating admission must not weaken any control."""
        metadata = mapping(pod.get("metadata"), _PRODUCT, "Pod metadata")
        labels = mapping(metadata.get("labels"), _PRODUCT, "Pod labels")
        if (
            metadata.get("name") != boundary.name
            or metadata.get("namespace") != self._cluster.namespace
            or labels.get(_OWNER_LABEL) != boundary.sandbox_id
            or labels.get(_EXECUTION_LABEL) != boundary.execution_id
        ):
            raise SandboxCapabilityError("Kubernetes did not apply the owned Pod identity")
        spec = mapping(pod.get("spec"), _PRODUCT, "Pod spec")
        context = mapping(spec.get("securityContext"), _PRODUCT, "Pod security context")
        seccomp = mapping(context.get("seccompProfile"), _PRODUCT, "Pod seccomp profile")
        if (
            spec.get("restartPolicy") != "Never"
            or spec.get("automountServiceAccountToken") is not False
            or spec.get("enableServiceLinks") is not False
            or any(spec.get(key) is True for key in ("hostNetwork", "hostPID", "hostIPC"))
            or spec.get("shareProcessNamespace") is True
            or spec.get("runtimeClassName") is not None
            or spec.get("terminationGracePeriodSeconds") != self._grace_seconds
            or spec.get("activeDeadlineSeconds") != self._deadline(request)
            or spec.get("initContainers") not in (None, [])
            or spec.get("ephemeralContainers") not in (None, [])
            or context.get("runAsNonRoot") is not True
            or context.get("runAsUser") != _USER
            or context.get("runAsGroup") != _USER
            or context.get("sysctls") not in (None, [])
            or seccomp != {"type": "RuntimeDefault"}
        ):
            raise SandboxCapabilityError("Kubernetes did not apply the required Pod isolation")
        expected_volumes: list[object] = []
        expected_mounts: list[object] = []
        if self._limits.scratch_bytes is not None:
            expected_mounts = [{"name": _SCRATCH_VOLUME, "mountPath": "/tmp"}]
            volumes = spec.get("volumes")
            volume = mapping(
                volumes[0] if isinstance(volumes, list) and len(volumes) == 1 else None,
                _PRODUCT,
                "scratch volume",
            )
            empty_dir = mapping(volume.get("emptyDir"), _PRODUCT, "scratch emptyDir")
            if (
                set(volume) != {"name", "emptyDir"}
                or volume.get("name") != _SCRATCH_VOLUME
                or empty_dir.get("medium") != "Memory"
                or _quantity(empty_dir.get("sizeLimit")) != self._limits.scratch_bytes
            ):
                raise SandboxCapabilityError("Kubernetes changed the size-capped scratch volume")
            expected_volumes = [volume]
        if (spec.get("volumes") or []) != expected_volumes:
            raise SandboxCapabilityError("Kubernetes added an unexpected Pod volume")
        containers = spec.get("containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise SandboxCapabilityError("Kubernetes added or removed Pod containers")
        container = mapping(containers[0], _PRODUCT, "Pod container")
        self._validate_container(container, request, boundary.execution_id, expected_mounts)

    def _validate_container(
        self,
        container: dict[str, object],
        request: ExecutionRequest,
        execution_id: str,
        expected_mounts: list[object],
    ) -> None:
        security = mapping(container.get("securityContext"), _PRODUCT, "container security")
        capabilities = mapping(security.get("capabilities"), _PRODUCT, "container capabilities")
        if (
            security.get("allowPrivilegeEscalation") is not False
            or security.get("privileged") is True
            or security.get("readOnlyRootFilesystem") is not True
            or capabilities.get("drop") != ["ALL"]
            or capabilities.get("add") not in (None, [])
            or security.get("procMount") not in (None, "Default")
            or security.get("runAsUser") not in (None, _USER)
            or security.get("runAsGroup") not in (None, _USER)
            or security.get("runAsNonRoot") is False
            or mapping(security.get("seccompProfile") or {}, _PRODUCT, "seccomp").get("type")
            not in (None, "RuntimeDefault")
        ):
            raise SandboxCapabilityError("Kubernetes weakened the container security context")
        command = _bootstrap.command(
            self._limits.checks(await_start=True), request.argv, execution_id
        )
        environment = [{"name": key, "value": value} for key, value in request.environment.items()]
        if (
            container.get("name") != _CONTAINER
            or container.get("image") != self._cluster.image
            or container.get("imagePullPolicy") != "Never"
            or string_list(container.get("command")) != list(command)
            or container.get("args") not in (None, [])
            or container.get("workingDir") != request.cwd
            or (container.get("env") or []) != environment
            or container.get("envFrom") not in (None, [])
            or container.get("stdin") is not True
            or container.get("stdinOnce") is not True
            or container.get("tty") is True
            or (container.get("volumeMounts") or []) != expected_mounts
            or container.get("volumeDevices") not in (None, [])
            or any(container.get(key) is not None for key in ("lifecycle", "startupProbe"))
        ):
            raise SandboxCapabilityError(
                "Kubernetes changed the requested image, command or execution controls"
            )
        resources = mapping(container.get("resources"), _PRODUCT, "container resources")
        expected = {
            "memory": Decimal(self._limits.memory_bytes)
            if self._limits.memory_bytes is not None
            else None,
            "cpu": Decimal(self._limits.millicpu) / 1000
            if self._limits.millicpu is not None
            else None,
        }
        for section in ("limits", "requests"):
            values = mapping(resources.get(section) or {}, _PRODUCT, f"container {section}")
            for key, value in expected.items():
                if value is not None and _quantity(values.get(key)) != value:
                    raise SandboxCapabilityError(
                        "Kubernetes did not apply the requested resource limits"
                    )

    @override
    async def start_boundary(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process:
        boundary.start_requested = True
        driver = _KubernetesDriver(self._cluster, self, boundary)
        try:
            async with asyncio.timeout(
                self._cluster.startup_timeout + self._cluster.command_timeout
            ):
                observation = await driver.observe()
                while observation.state is BoundaryState.CREATED:
                    await asyncio.sleep(0.25)
                    observation = await driver.observe()
                for _ in range(_ATTACH_ATTEMPTS):
                    if observation.state is not BoundaryState.RUNNING:
                        break
                    process = await self._attach(boundary, request)
                    if process is not None:
                        return process
                    await asyncio.sleep(0.5)
                    observation = await driver.observe()
        except TimeoutError as error:
            raise SandboxExecutionError(
                "Kubernetes workload did not start within the startup timeout"
            ) from error
        raise await self._startup_failure(boundary)

    async def _attach(
        self, boundary: Boundary, request: ExecutionRequest
    ) -> asyncio.subprocess.Process | None:
        """Attach and release the gated prelude; ``None`` if attach ended before output.

        The kubelet can report a running container before its attach endpoint
        knows it. The prelude consumes nothing before the start line, so an
        attach that ended without any output may be retried.
        """
        expected = _bootstrap.marker(boundary.execution_id)
        process: asyncio.subprocess.Process | None = None
        failure: BaseException | None = None
        try:
            process = await self._cluster.runner.spawn(
                self._cluster.command(
                    "attach",
                    boundary.name,
                    f"--container={_CONTAINER}",
                    "--stdin",
                    "--quiet",
                    f"--pod-running-timeout={math.ceil(self._cluster.startup_timeout)}s",
                ),
                stdin=True,
            )
            if process.stdin is None or process.stdout is None:
                raise SandboxBackendError("Kubernetes attach client has no stdin or output pipe")
            try:
                # The prelude blocks after verification until this start line arrives,
                # so no workload output is produced before the client is attached.
                process.stdin.write(expected)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the attach already ended; the read below reports it
            if await process.stdout.readexactly(len(expected)) != expected:
                raise SandboxCapabilityError(
                    "Kubernetes workload prelude did not verify kernel isolation before exec"
                )
            if request.stdin is None:
                process.stdin.close()
                await process.stdin.wait_closed()
            return process
        except asyncio.IncompleteReadError as error:
            if error.partial:
                failure = SandboxCapabilityError(
                    "Kubernetes workload prelude output ended before the start marker"
                )
                failure.__cause__ = error
        except OSError as error:
            failure = SandboxExecutionError(
                f"Kubernetes attach startup failed: {type(error).__name__}"
            )
            failure.__cause__ = error
        except BaseException as error:
            failure = error
        cleanup = (
            await settle(asyncio.create_task(reap_client(process, _PRODUCT)))
            if process is not None
            else None
        )
        raise_combined(
            "Kubernetes attach startup and cleanup failed",
            failure,
            cleanup.error if cleanup is not None else None,
            cleanup.interrupted if cleanup is not None else None,
        )
        return None

    async def _startup_failure(self, boundary: Boundary) -> Exception:
        try:
            observation = await self._settled(boundary)
        except SandboxBackendError as error:
            return error
        if observation.exit_code == _bootstrap.FAILURE_EXIT:
            return SandboxCapabilityError(
                "Kubernetes workload prelude rejected the kernel-reported isolation or limits"
            )
        return SandboxExecutionError("Kubernetes attach ended before the workload started")

    async def _settled(self, boundary: Boundary) -> Observation:
        """Wait for the kubelet to publish a terminal container state."""
        driver = _KubernetesDriver(self._cluster, self, boundary)
        loop = asyncio.get_running_loop()
        end = loop.time() + self._cluster.command_timeout
        while True:
            observation = await driver.observe()
            if observation.state is BoundaryState.EXITED or loop.time() >= end:
                return observation
            await asyncio.sleep(0.25)

    @override
    async def workload_exited(self, boundary: Boundary) -> None:
        # The container stops with its init process, which ends the attach stream.
        return None

    @override
    async def exit_code(self, boundary: Boundary, client_exit_code: int) -> int:
        if client_exit_code != 0:
            raise SandboxExecutionError(
                "Kubernetes attach failed; workload output may be incomplete"
            )
        observation = await self._settled(boundary)
        if observation.state is not BoundaryState.EXITED or observation.exit_code is None:
            raise SandboxExecutionError("Kubernetes attach ended without a terminated container")
        return observation.exit_code

    @override
    async def terminate(
        self,
        boundary: Boundary,
        policy: TerminationPolicy,
        force_requested: asyncio.Event,
    ) -> TerminationReport:
        return await terminate_boundary(
            _KubernetesDriver(self._cluster, self, boundary), policy, force_requested
        )


class _KubernetesDriver(TerminationDriver):
    # Stopping a Pod is a UID-preconditioned deletion, which also releases it.
    signal_releases = True

    def __init__(self, cluster: _Cluster, session: _KubernetesSession, boundary: Boundary) -> None:
        self._cluster = cluster
        self._session = session
        self._boundary = boundary
        self.boundary_id = boundary.boundary_id
        self.start_requested = boundary.start_requested

    @override
    async def observe(self) -> Observation:
        pod = await self._session._inspect(self._boundary)
        if pod is None:
            return Observation(BoundaryState.ABSENT)
        status = mapping(pod.get("status") or {}, _PRODUCT, "Pod status")
        statuses = status.get("containerStatuses")
        if not isinstance(statuses, list):
            statuses = []
        matching = [
            item for item in statuses if isinstance(item, dict) and item.get("name") == _CONTAINER
        ]
        if len(matching) > 1:
            return Observation(BoundaryState.UNKNOWN, detail="duplicate container status")
        state = mapping(matching[0].get("state") or {}, _PRODUCT, "state") if matching else {}
        if "running" in state:
            return Observation(BoundaryState.RUNNING)
        if "terminated" in state:
            code = mapping(state["terminated"], _PRODUCT, "terminated state").get("exitCode")
            if type(code) is int:
                return Observation(BoundaryState.EXITED, code)
            return Observation(BoundaryState.UNKNOWN, detail="terminated without an exit code")
        if set(state) <= {"waiting"}:
            # Never started (pending, waiting or rejected by the kubelet); deletion
            # prevents a later start and is only confirmed after the kubelet stops it.
            return Observation(BoundaryState.CREATED)
        return Observation(BoundaryState.UNKNOWN, detail="Kubernetes returned an uncertain state")

    async def _delete(self, grace_seconds: int | None) -> None:
        options: dict[str, object] = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": self.boundary_id},
            "propagationPolicy": "Background",
        }
        if grace_seconds is not None:
            options["gracePeriodSeconds"] = grace_seconds
        path = f"/api/v1/namespaces/{self._cluster.namespace}/pods/{self._boundary.name}"
        _, stderr, returncode = await _invoke(
            self._cluster,
            "Pod deletion",
            "delete",
            f"--raw={path}",
            "--filename=-",
            stdin=json.dumps(options).encode(),
            accepted=(0, 1),
        )
        # NotFound or a UID-precondition conflict both mean this Pod no longer exists.
        if returncode != 0 and _status_error(stderr) is None:
            raise SandboxBackendError("Kubernetes rejected the UID-preconditioned Pod deletion")

    @override
    async def signal(self, *, force: bool) -> None:
        # The kubelet sends SIGTERM, then SIGKILL after the grace period; a forced stop
        # shortens it to one second without bypassing kubelet confirmation.
        await self._delete(1 if force else None)

    @override
    async def remove(self) -> None:
        await self._delete(None)
        loop = asyncio.get_running_loop()
        end = loop.time() + self._cluster.command_timeout
        while await self.exists():
            if loop.time() >= end:
                raise SandboxBackendError("Kubernetes has not confirmed the Pod deletion")
            await asyncio.sleep(0.25)

    @override
    async def exists(self) -> bool:
        return await self._session._inspect(self._boundary) is not None
