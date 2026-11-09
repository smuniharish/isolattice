# VMs and microVMs versus Isolattice

Virtual machines and microVMs such as [Firecracker](https://firecracker-microvm.github.io/)
and [Kata Containers](https://katacontainers.io/) give each workload its own guest
kernel behind a hypervisor. Every implemented Isolattice backend shares the host (or
Podman machine or node) kernel.

## When to use which

- **Use a VM or microVM** for hostile, untrusted or multi-tenant code. A guest kernel
  behind a hypervisor is a much stronger boundary than any shared-kernel sandbox, and
  Isolattice backends share the host or node kernel, so kernel exploits are out
  of their scope.
- **Use Isolattice** for workloads you mostly trust but want contained: limits, no
  network, read-only root, explicit environment and verified cleanup, with low startup
  cost and no hypervisor to operate.
- **Combine them:** run Isolattice inside a VM (for example, a Podman machine or a
  dedicated worker VM) to get both the VM boundary and per-execution policy.

## Comparison

| | VM or microVM | Isolattice |
| --- | --- | --- |
| Kernel | Separate guest kernel | Shared host kernel |
| Boundary | Hypervisor | Namespaces, cgroups, containers, AppContainer, Job Objects |
| Startup and density | Higher cost per workload | Container or process cost |
| Operations | Images, hypervisor, networking to run | Python package plus a backend |
| Hostile multi-tenant code | Appropriate with a hardened setup | Not qualified |

Isolattice has no VM or microVM backend. Kubernetes clusters can use a VM-backed
runtime class configured outside Isolattice; the `KubernetesBackend` does not select or
verify one. See the [threat model](../security/threat-model.md).
