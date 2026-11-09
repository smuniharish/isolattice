# Security policy

## Current status

Isolattice 0.1.0 ships Podman, Docker, Kubernetes, Linux namespace, Windows
AppContainer and Windows Job Object backends. Containers, Pods, namespaces and
AppContainers share the host (or node) kernel, so kernel, engine, runtime or
cluster exploits are outside the threat model, and the engine, cluster, image
and host are trusted. No backend is certified for hostile multi-tenant use.

- Podman instance budgets and checkpoints work only on rootful engines.
- The Linux backend shares the host kernel and does not deny `ptrace` within
  its own sandbox.
- The AppContainer backend shares the interactive session and desktop;
  brokered system services remain reachable, and a caller crash leaves its
  profile behind.
- The Windows Job Object backend is **not a security boundary**: workloads keep
  the user's full filesystem, network and registry access and can escape
  through brokers such as WMI. Its restricted low-integrity token only blocks
  writes to medium-integrity objects.
- The Docker backend was tested through Podman's Docker-compatible API and a
  nested Docker Engine (`docker:dind`), not on a native Docker host.
- The Kubernetes backend cannot isolate the network, trusts the control plane
  and node, and was tested only on a single-node kind cluster.
- Container snapshots contain everything the workload wrote to its root; protect
  them like workload data.
- Workspaces have no disk quota, and macOS has no backend.

Rootless operation is the default requirement. Explicit rootful opt-in grants
the library greater engine authority and is not an equivalent security profile.
Code that may attempt kernel or engine exploits needs a VM or microVM boundary
around the whole sandbox host. See the
[Podman](docs/backends/podman.md), [Docker](docs/backends/docker.md),
[Kubernetes](docs/backends/kubernetes.md), [Linux](docs/backends/linux.md) and
[Windows](docs/backends/windows.md) backend limitations.

## Reporting

Do not publish exploit payloads, credentials or tenant data in public issues.
Use GitHub's private vulnerability reporting for this repository **if enabled**.
Enabling and verifying that channel is a maintainer gate before a runtime release;
no private reporting email is currently configured.

If private reporting is unavailable, open a non-sensitive issue requesting a
private channel, without vulnerability details. Maintainers must establish the
channel before receiving sensitive material.

## Future support and disclosure

Before releasing enforcement code, publish supported versions, a private contact,
response/disclosure policy, and backend-specific security qualifications.
Shared-kernel native/container isolation does not protect against host kernel
compromise. See the [threat model](docs/architecture/17-security-threat-model.md).
