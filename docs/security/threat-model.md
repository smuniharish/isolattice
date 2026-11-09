# Threat model

!!! warning "Not for hostile multi-tenant code"
    Containers, namespaces and AppContainers share the host or node kernel, so
    kernel, engine, runtime and cluster exploits are outside this threat model;
    the engine, cluster, image and host are trusted. The Windows Job Object
    backend is containment only, not a security boundary. Use a VM or microVM
    boundary for hostile, untrusted or multi-tenant workloads.

The canonical threat matrix, with each control and its residual risk, is the
[security threat model](../architecture/17-security-threat-model.md). The rules the
API follows are in the [security model](../concepts/security-model.md). This page is a
short practical summary.

## Who is trusted

| Trusted | Untrusted |
| --- | --- |
| Your application and its `SandboxPolicy` | The workload and every process it starts |
| The Isolattice runtime and the selected backend and its helpers | Workload output, exit codes and files |
| The host kernel, container engine, Kubernetes control plane and nodes | Archives and manifests read back from an artifact store |
| The artifact store's access control | Engine and cluster responses until Isolattice re-inspects them |

## What Isolattice defends against

- **Silently weaker isolation.** Policies the backend cannot enforce are rejected and
  required capabilities fail closed. See
  [capability enforcement](capability-enforcement.md).
- **Escaping cleanup.** Workloads that fork, detach or daemonize are terminated as a
  boundary, not by PID. See [process escape prevention](process-escape-prevention.md).
- **Resource exhaustion.** Memory, CPU and process limits, instance budgets, scratch
  caps, bounded output and deadlines, where the backend reports them.
- **Spec tampering.** Container and Pod backends inspect what the engine created and
  check the kernel state inside the boundary before starting the workload.
- **Malicious archives.** Restored snapshots are size- and SHA-256-verified and their
  members validated before extraction.
- **Secret leakage through Isolattice.** Workloads start with an explicit environment,
  and events and errors never carry argv, environment values, stdin or output.

## What it does not defend against

- Kernel, hypervisor, engine, cluster or artifact-store compromise.
- Side channels and host-wide resource contention.
- Anything a backend reports as `LIMITED` or `UNSUPPORTED` (see
  [guarantees](guarantees.md) and [platform limitations](platform-limitations.md)).
- Data you pass to the workload: it can print or exfiltrate whatever it is given if the
  backend allows network access.

macOS and remote execution are proposed and have no threat model yet.
