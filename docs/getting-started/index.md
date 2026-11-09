# Getting started

Isolattice runs commands inside sandboxes and enforces the policy you ask for,
or refuses. These pages take you from installation to a running sandbox.

1. [Installation](installation.md): install the package and a backend.
2. [Quickstart](quickstart.md): run, stream, time out and cancel a command.

Then pick a backend:

| You have | Use |
| --- | --- |
| Podman or Podman Desktop | [Podman](../backends/podman.md) |
| A Docker Engine API endpoint | [Docker API](../backends/docker.md) |
| A Kubernetes cluster | [Kubernetes](../backends/kubernetes.md) |
| Linux with a delegated cgroup v2 subtree | [Linux namespaces](../backends/linux.md) |
| Windows 10 or 11 | [Windows](../backends/windows.md) |

!!! warning "Before you run anything important"

    Built-in backends share the host or node kernel, and the Windows Job Object
    backend is not a security boundary; none defends against kernel exploits.
    Read the [threat model](../security/threat-model.md) and
    [platform limitations](../security/platform-limitations.md).
