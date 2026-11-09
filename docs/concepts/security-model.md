# Security model

## Required means required

`SandboxPolicy` has two kinds of settings:

- **Policy settings**, such as `network`, `read_only_root`, `memory_bytes` and
  `workspace`. Each backend enforces them or rejects the policy.
- **Required capabilities** in `required_capabilities`. A required capability
  must be reported `SUPPORTED`. `LIMITED` or `UNSUPPORTED` stops creation with
  `SandboxCapabilityError` before anything runs.

There is no warning-only mode. If you can accept a weaker guarantee, leave it
out of `required_capabilities` and read the backend's evidence for it.

## Explicit selection

You choose a backend by constructing it and passing it to
`Sandbox(backend=...)`; nothing is selected or substituted for you. Backends
that give the engine host root, such as rootful Podman, also need
`allow_rootful=True` on the backend.

## Shared kernels

Containers, namespaces and AppContainers share the host or node kernel, so
kernel, engine, runtime and cluster exploits are outside the threat model; the
engine, cluster, image and host are trusted. The Windows Job Object backend is
containment and resource control only, not a security boundary: workloads keep
the caller's filesystem, network and registry access.

## What is untrusted

- The workload, its output, its exit code and anything it writes.
- Archives restored from an artifact store. Isolattice recomputes their size and
  SHA-256, and validates their members before use.
- Engine and cluster responses about the workload. Isolattice re-inspects the
  container or Pod configuration it created before starting the workload.

## What stays out of the workload

- Your environment. Podman and the native backends pass only
  `ExecutionRequest.environment`.
- Engine sockets, kubeconfigs and credentials. Backends hold them only on the
  host side.
- Workload data in diagnostics. Errors and events never include argv,
  environment values, stdin or output.

## Defaults

`SandboxPolicy()` asks for no network, a read-only root, a 30-second timeout and
1 MiB output caps. Limits are not set by default; set them and list them as
required.

Read the [threat model](../security/threat-model.md),
[guarantees](../security/guarantees.md) and
[policy model](../architecture/05-policy-model.md).
