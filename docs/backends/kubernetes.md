# Kubernetes backend

`KubernetesBackend` runs each execution as one owned Pod in one explicit namespace,
driven through `kubectl`. It applies container memory and CPU limits and a size-capped
`/tmp`, verifies them from inside the Pod before the workload starts, and deletes the Pod
by UID. Networking cannot be restricted, and there are no workspaces, snapshots,
checkpoints, process limits or instance budgets.

!!! warning "Shared node kernel"
    Pods share the node kernel; kernel, runtime or cluster exploits are out of
    scope, and the cluster, nodes and image are trusted. Every policy must set
    `network=NetworkAccess.ENABLED`. Status: implemented; verified live on kind.

## Requirements

- The `kubectl` CLI on `PATH`, or passed as `executable=`.
- A kubeconfig, context and `Active` namespace you are allowed to create Pods in.
- An image already on the node that provides a POSIX `/bin/sh`. Pods use
  `imagePullPolicy: Never`.
- cgroup v2 on the node for verified memory and CPU limits.

## Configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `kubeconfig` | required | Kubeconfig path passed to every `kubectl` call |
| `context` | required | Context name; the current context is never used |
| `namespace` | required | DNS-1123 namespace that must be `Active` |
| `image` | required | Image present on the node |
| `executable` | `None` | Path to `kubectl`; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds allowed for each control command |
| `startup_timeout` | `60.0` | Seconds allowed for a Pod to start |
| `runner` | `None` | [`CommandRunner`](../guides/custom-integrations.md#custom-command-runner) that starts CLI processes |

## Example

```python
import asyncio

from isolattice import (
    ExecutionRequest,
    KubernetesBackend,
    NetworkAccess,
    Sandbox,
    SandboxPolicy,
)


async def main() -> None:
    backend = KubernetesBackend(
        kubeconfig="/home/me/.kube/kind-isolattice",
        context="kind-isolattice",
        namespace="isolattice-test",
        image="docker.io/library/alpine:3.22",
    )
    policy = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        memory_bytes=64 * 1024**2,
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "id -u")))
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

## Enforced configuration

Pods disable service links and service-account token mounting and run as UID 65534
with every capability dropped, `allowPrivilegeEscalation: false`, the `RuntimeDefault`
seccomp profile, a read-only root and a memory-backed `emptyDir` at `/tmp`. After
admission the Pod spec is inspected; a shell prelude then reads capabilities,
`NoNewPrivs`, seccomp mode, UID, `memory.max`, `cpu.max` and the `/tmp` size before
executing argv. Any mismatch fails closed and deletes the Pod.

`prepare` rejects `network` other than `ENABLED`, `read_only_root=False`,
`workspace=True`, `instance_budget` and `process_limit`. Termination deletes the Pod
with a UID precondition, so a recreated Pod with the same name is never touched, and
succeeds only when the API server no longer reports that UID. The grace period is
`ceil(TerminationPolicy.graceful_timeout)` seconds (at least 1).

## Capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `MEMORY_LIMIT`, `CPU_LIMIT` | SUPPORTED | Read from the workload cgroup before exec |
| `RESOURCE_LIMITS` | LIMITED | Per-container memory and CPU only |
| `FILESYSTEM_ISOLATION` | LIMITED | The kubelet adds a writable `/dev/termination-log`, `/dev/shm` and `/etc/hosts` |
| `PROCESS_ISOLATION` | LIMITED | Private PID namespace; cluster components are trusted |
| `VERIFIED_TERMINATION` | LIMITED | API absence after UID-preconditioned deletion; the kubelet and runtime are trusted |
| `ENVIRONMENT_ISOLATION` | LIMITED | Image environment, `HOSTNAME` and `KUBERNETES_SERVICE_*` remain |
| `DISK_LIMIT` | LIMITED | `/tmp` is capped; `/dev/termination-log` is not |
| `NETWORK_ISOLATION` | UNSUPPORTED | Pods join the cluster network; NetworkPolicy enforcement depends on the CNI and cannot be verified |
| `PROCESS_LIMIT` | UNSUPPORTED | Kubernetes only has a node-wide per-Pod PID limit |
| `WORKSPACE`, `SNAPSHOTS`, `CHECKPOINTS` | UNSUPPORTED | Not implemented for Kubernetes |
| `INSTANCE_LIMITS` | UNSUPPORTED | Pods of one sandbox share no aggregate cgroup |

## Limitations

- Networking is always enabled. On kind under WSL2, kindnet does not enforce
  NetworkPolicy because the kernel lacks `nfqueue`.
- Each execution is a separate Pod; nothing persists between executions.
- The control plane, node, image and admission chain are trusted. API absence does not
  prove a lost node stopped the process.
- Workload output stays in node container logs until the Pod is deleted.
- Environment values and command arguments are visible to namespace readers.
- Jobs, RBAC provisioning, volumes and node selection are not implemented.

## Live testing

[test_kubernetes_backend.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_kubernetes_backend.py)
uses the `kubernetes` marker and skips unless `ISOLATTICE_TEST_KUBERNETES_KUBECONFIG`,
`ISOLATTICE_TEST_KUBERNETES_CONTEXT`, `ISOLATTICE_TEST_KUBERNETES_NAMESPACE` and
`ISOLATTICE_TEST_KUBERNETES_IMAGE` are set. `ISOLATTICE_TEST_KUBERNETES_EXECUTABLE` is
optional. See [conftest.py](https://github.com/smuniharish/isolattice/blob/main/tests/conftest.py).

```sh
uv run pytest -m kubernetes
```

The suite and the
[example](https://github.com/smuniharish/isolattice/blob/main/examples/10-kubernetes/kubernetes_isolated_run.py)
passed on a single-node local kind cluster, not a production cluster.

## Related

- [Kubernetes integration guide](../guides/kubernetes-integration.md)
- [Kubernetes compared with Isolattice](../comparisons/kubernetes-vs-isolattice.md)
- [Network isolation](../guides/network-isolation.md)
- [Container backend architecture](../architecture/16-container-backend-architecture.md)
