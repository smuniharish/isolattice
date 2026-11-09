# Kubernetes versus Isolattice

Kubernetes schedules workloads across a cluster. Isolattice's
[`KubernetesBackend`](../backends/kubernetes.md) uses one context and namespace and
runs one restricted Pod per execution: no service-account token, all capabilities
dropped, a read-only root, verified memory and CPU limits, and UID-preconditioned
deletion confirmed through the API server.

## When to use which

- **Use Kubernetes directly** (Jobs, Deployments, operators) for services, batch
  pipelines, scheduling, retries, autoscaling and anything that needs networking
  policy, volumes or secrets. Kubernetes is the better tool for orchestration.
- **Use Isolattice with `KubernetesBackend`** when a Python application needs to run
  short commands on a cluster with the same policy, streaming and cleanup API it uses
  locally.
- **Use stronger runtime classes** (such as gVisor or Kata Containers, configured in the
  cluster) when you need more than a shared-kernel boundary.

## Comparison

| | Kubernetes Job or Pod | Isolattice `KubernetesBackend` |
| --- | --- | --- |
| Unit | Any Pod spec | One fixed restricted Pod per execution |
| Network | CNI and NetworkPolicy | Always on; policy must use `NetworkAccess.ENABLED` |
| Limits | Requests and limits | Memory and CPU verified inside the container; no process limit |
| Output | Logs via the API | Streamed and bounded; also kept in node logs until deletion |
| Cleanup | TTL controller or manual delete | Verified deletion with a `TerminationReport` |
| Workspaces, snapshots | Volumes | Not supported |
| Status | Mature platform | Implemented; verified live on kind |

## Example

```python
from isolattice import KubernetesBackend, NetworkAccess, SandboxPolicy

backend = KubernetesBackend(
    kubeconfig="/home/me/.kube/config",
    context="kind-isolattice",
    namespace="sandboxes",
    image="docker.io/library/alpine:3.22",
)
policy = SandboxPolicy(
    network=NetworkAccess.ENABLED,
    memory_bytes=128 * 1024 * 1024,
)
```

The image must already be on the node. See the
[Kubernetes integration guide](../guides/kubernetes-integration.md).
