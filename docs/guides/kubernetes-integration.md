# Kubernetes integration

This guide runs executions as Pods in a Kubernetes namespace with `KubernetesBackend`.
Each execution is one owned Pod created and observed through `kubectl`.

!!! warning "Shared node kernel"
    Pods share the node kernel; kernel, runtime or cluster exploits are out of
    scope, and the backend cannot isolate the network. It has been verified live
    on a single-node kind cluster. See the
    [Kubernetes backend](../backends/kubernetes.md) page.

## 1. Prepare a cluster and namespace

A local [kind](https://kind.sigs.k8s.io/) cluster works for development:

```powershell
kind create cluster --name isolattice
kubectl --context kind-isolattice create namespace isolattice-test
```

Pods use `imagePullPolicy: Never`, so load the image onto the nodes:

```powershell
podman pull docker.io/library/alpine:3.22
podman save -o alpine.tar docker.io/library/alpine:3.22
kind load image-archive alpine.tar --name isolattice
```

The identity in the kubeconfig needs permission to create, get, attach to and delete
Pods in the namespace, and to get the namespace.

## 2. Run a command

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
        kubeconfig="/home/me/.kube/config",
        context="kind-isolattice",
        namespace="isolattice-test",
        image="docker.io/library/alpine:3.22",
    )
    policy = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        memory_bytes=64 * 1024 * 1024,
        cpu_cores=0.25,
        scratch_bytes=8 * 1024 * 1024,
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "id -u")))
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

`kubeconfig`, `context` and `namespace` are passed to every `kubectl` call; the
ambient `KUBECONFIG` and current context are never used. `startup_timeout` (default
60 seconds) bounds how long a Pod may take to start; `command_timeout` (default 15
seconds) bounds each `kubectl` call.

## What the backend enforces

Each Pod disables service links and service-account token mounting, runs as UID
65534 with all capabilities dropped, `allowPrivilegeEscalation: false`, the
`RuntimeDefault` seccomp profile and a read-only root. After admission, the Pod spec is
compared with the plan, and a prelude inside the container checks capabilities,
`NoNewPrivs`, seccomp, UID, cgroup limits and the `/tmp` size before the workload
starts. Any mismatch deletes the Pod and fails.

| Policy field | Support |
| --- | --- |
| `network` | Must be `ENABLED` |
| `read_only_root` | Must be `True` |
| `memory_bytes`, `cpu_cores` | Container limits; CPU in whole millicores, at least `10m` |
| `process_limit` | Rejected |
| `scratch_bytes` | Memory-backed `emptyDir` at `/tmp`; a multiple of 4096 |
| `workspace`, `instance_budget` | Rejected |
| Snapshots and checkpoints | Rejected |

## Network policy

Isolattice never claims network isolation on Kubernetes, because enforcement depends
on the CNI plug-in and cannot be verified from inside the Pod. If you need it, apply a
deny-all `NetworkPolicy` to the namespace and test that your CNI enforces it. On kind
with WSL2, kindnet cannot enforce policies because the kernel lacks `nfqueue`.

## Termination and reconciliation

Pods carry `io.isolattice.sandbox` and `io.isolattice.execution` labels and are
deleted with a UID precondition, so a recreated Pod with the same name is never
touched. When a request sets `timeout`, the Pod also gets an `activeDeadlineSeconds`
backstop (the timeout plus startup and grace time) in case the client disappears. The
exit code comes from the container status, because `kubectl attach` always exits 0.

To find leftovers after an abrupt shutdown:

```powershell
kubectl --context kind-isolattice -n isolattice-test get pods -l io.isolattice.sandbox
```

## Related pages

- [Kubernetes example](https://github.com/smuniharish/isolattice/blob/main/examples/10-kubernetes/kubernetes_isolated_run.py)
- [Container backend design](../architecture/16-container-backend-architecture.md)
- [Testing with a cluster](../development/testing.md)
