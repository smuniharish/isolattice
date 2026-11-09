# macOS backend

**Proposed, not implemented.** Isolattice has no macOS backend and no macOS API. On
macOS, use a container backend such as [Podman](podman.md) through a Linux VM.

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
