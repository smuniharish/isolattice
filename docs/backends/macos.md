# macOS

**Supported host; native backend proposed.** macOS is a tested Isolattice host:
CI runs the full core suite on macOS 15 and 26, on Apple silicon and Intel, with
Python 3.12 to 3.15 (see [tested platforms](../reference/tested-platforms.md)).
On macOS, run workloads through a container backend:

- [Podman](podman.md) with a local Podman machine (a Linux VM, for example one
  managed by [Podman Desktop](../guides/podman-desktop.md)) or an `ssh://`
  connection to a remote engine;
- [Docker API](docker.md) against a local or remote Docker-compatible engine;
- [Kubernetes](kubernetes.md) through a kubeconfig context.

The engine enforces the boundary inside its Linux VM or on the remote host, so the
capabilities are the same as on any other client.

## Native backend

There is no native macOS sandbox backend and no macOS-specific API.

!!! note "Status"
    Only an evaluation of Seatbelt (`sandbox-exec`) and App Sandbox exists. Neither
    has been shown to provide a supported launcher for arbitrary Python workloads or
    authoritative verification that every descendant process has exited.

A future backend would have to:

- enforce filesystem and network restrictions without assuming Seatbelt contains every
  descendant;
- verify process-tree emptiness by a containment identity, not process groups or PID
  enumeration;
- reject creation when a required guarantee cannot be enforced, never falling back
  silently to another backend.

Any implementation needs architecture approval and real macOS validation first.

## Related

- [Process containment](../architecture/10-process-containment.md)
- [Platform limitations](../security/platform-limitations.md)
- [Apple App Sandbox](https://developer.apple.com/documentation/security/app-sandbox)
