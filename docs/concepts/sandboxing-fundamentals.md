# Sandboxing fundamentals

A sandbox is a **boundary**: it limits what a workload can reach, and it
contains everything the workload starts. Starting a process with a different
working directory or environment creates neither.

## Independent dimensions

Isolation is several separate properties. A backend can be strong in one and
absent in another:

| Dimension | Question | Capability |
| --- | --- | --- |
| Filesystem | Which host files can it read or write? | `FILESYSTEM_ISOLATION`, `WORKSPACE`, `DISK_LIMIT` |
| Network | Can it open connections? | `NETWORK_ISOLATION` |
| Processes | Can it see or signal other processes, and can its children escape? | `PROCESS_ISOLATION`, `VERIFIED_TERMINATION` |
| Environment | Does it inherit your variables and credentials? | `ENVIRONMENT_ISOLATION` |
| Resources | Can it exhaust memory, CPU or process slots? | `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT`, `INSTANCE_LIMITS` |

Treat each dimension separately. For example, a Windows Job Object contains
every descendant process and enforces limits, but it does not restrict files,
network or the registry.

## Containment and termination

A workload's children can outlive it. Killing the first process, or a list of
PIDs, misses any process that has re-parented or detached, and PIDs are reused.
A sound boundary contains every descendant by construction:

- a container's PID namespace and cgroup;
- a Linux cgroup v2 subtree;
- a Windows Job Object that children cannot leave;
- a Kubernetes Pod.

Isolattice terminates the boundary, then observes it again until the boundary
is verified empty or absent. If that cannot be verified, the outcome is
`unknown`, and an unknown outcome is never reported as success.

## Who you are trusting

Every native and container backend shares the host kernel, and on Windows the
host's system services. A kernel or engine flaw can break the boundary. Virtual
machines and microVMs give a different boundary, with a different set of
components to trust; see the
[comparison](../comparisons/vm-and-microvm-vs-isolattice.md).

Further reading:

- [subprocess compared with Isolattice](../comparisons/python-subprocess-vs-isolattice.md)
- [native sandboxing compared with Isolattice](../comparisons/native-sandboxing-vs-isolattice.md)
- the [threat model](../security/threat-model.md)
