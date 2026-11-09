# Errors

Every Isolattice exception derives from `SandboxError`. Errors keep their cause
chain, and cleanup failures are reported rather than swallowed.

::: isolattice.SandboxError

::: isolattice.SandboxCapabilityError

::: isolattice.SandboxBackendError

::: isolattice.SandboxStateError

::: isolattice.SandboxExecutionError

::: isolattice.SandboxTimeoutError

::: isolattice.SandboxTerminationError

::: isolattice.TerminationVerificationError

::: isolattice.SandboxArtifactError
