---
name: container-backend
description: "Use for Docker, Podman, Podman Desktop or Kubernetes workload security and lifecycle adapters."
---

# Container and orchestration backends

Read [container architecture](../../../docs/architecture/16-container-backend-architecture.md)
and [Kubernetes](../../../docs/backends/kubernetes.md).

## Workflow

1. Preserve explicit engine provenance and evaluate maintained client async behavior.
2. Validate images, mounts, resources, users, devices, networking and privileges.
3. Deny workload management-socket/host-mode access conflicting with policy.
4. Track immutable IDs/ownership and reconcile uncertain SDK operations.
5. Verify runtime boundary emptiness before required release.
6. For Kubernetes, qualify CNI/CSI/admission/node evidence, not just API objects.
7. Test rootless differences, stream cancellation, engine/node loss and orphans.

Podman Desktop is optional management. Kubernetes is orchestration, not a local
engine alias. Forced API deletion is not proof of process termination.
Blocking clients need owned bounded adapters.

Done requires real configured runtime tests, explicit limitations, docs and
integration examples; no silent Docker/Podman replacement.
