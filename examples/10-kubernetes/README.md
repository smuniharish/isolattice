---
category: 10
status: runnable
---

# 10 Kubernetes

Runs a limited workload as an owned Pod with `KubernetesBackend` in an explicit
kubeconfig, context and namespace. Each execution is a separate Pod.

| Script | What it shows | Platform |
| --- | --- | --- |
| [`kubernetes_isolated_run.py`](https://github.com/smuniharish/isolattice/blob/main/examples/10-kubernetes/kubernetes_isolated_run.py) | Cluster identity, rejected `NetworkAccess.NONE`, uid, capabilities, memory/CPU limits, read-only root, `/tmp` cap, no service-account token | Disposable local cluster (live-tested on kind) |

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- `kubectl` on `PATH`, or its path passed with `--executable`.
- A disposable cluster (for example a single-node kind cluster), a kubeconfig file,
  a context and a dedicated namespace that already exists.
- An image already present on the node containing `/bin/sh`, `awk` and `dd` (for
  kind, load it with `kind load docker-image`).

## Run

```shell
uv run python examples/10-kubernetes/kubernetes_isolated_run.py --kubeconfig ~/.kube/config --context kind-isolattice --namespace isolattice-examples --image docker.io/library/alpine:3.22
```

All of `--kubeconfig`, `--context`, `--namespace` and `--image` are required;
`--executable` is optional.

## Expected output

```text
Backend: kubernetes
Cluster: Kubernetes API <server>; namespace <namespace>.
Qualification: Pods share the node kernel; kernel, runtime or cluster exploits are out of scope.
Unverifiable network isolation: rejected
uid=65534
caps=0000000000000000
memory.max=67108864
cpu.max=25000 100000
root=read-only
scratch=full
token=none
stdin=from the host
greeting=hello
Owned Pod released: True
```

## Acceptance

- A policy with `network=NetworkAccess.NONE` must raise `SandboxCapabilityError`,
  because NetworkPolicy enforcement cannot be verified.
- With `network=NetworkAccess.ENABLED`, `memory_bytes=64 MiB`, `cpu_cores=0.25` and
  `scratch_bytes=8 MiB`, the Pod must report uid 65534, no effective capabilities,
  the exact `memory.max` and `cpu.max`, a read-only root, a full `/tmp` after a
  16 MiB write, no mounted service-account token, and the stdin and environment
  values; the Pod must be released.
- [`tests/integration/test_kubernetes_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_kubernetes_backend.py)
  runs the script (`ISOLATTICE_TEST_KUBERNETES_KUBECONFIG`, `_CONTEXT`, `_NAMESPACE`,
  `_IMAGE`, optional `_EXECUTABLE`) and checks that the set of owned Pods is unchanged
  afterwards.

## Cleanup

Pods are deleted with a UID precondition and confirmed gone when the API server no
longer reports them. Only Pods created by this sandbox are deleted.

## Security

- Pods share the node kernel; kernel, runtime or cluster exploits are out of scope,
  and the cluster, node and image are trusted.
- Networking is always enabled: Pods join the cluster network.
- `PROCESS_LIMIT`, `INSTANCE_LIMITS`, workspaces, snapshots and checkpoints are
  unsupported. Jobs, RBAC provisioning, volumes and node selection are not provided.
- The image environment, `HOSTNAME` and `KUBERNETES_SERVICE_*` variables are always
  present; environment values and arguments are visible to namespace readers, and
  output stays in node container logs until the Pod is deleted.
- Use a least-privilege kubeconfig, never cluster-admin credentials.

See the [Kubernetes backend](https://isolattice.readthedocs.io/en/latest/backends/kubernetes/)
and [Kubernetes integration](https://isolattice.readthedocs.io/en/latest/guides/kubernetes-integration/).
