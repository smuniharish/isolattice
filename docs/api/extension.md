# Backend extension

Implement `Backend` and `BackendSession` to add a backend. The runtime owns
ordering, deadlines, output streaming and cleanup; a backend only discovers
capabilities, prepares policy-enforcing sessions and reports authoritative
boundary identities. See [Custom backends](../guides/custom-backend.md).

## Backend contract

::: isolattice.Backend

::: isolattice.BackendSession

::: isolattice.Boundary

## Verified termination

::: isolattice.TerminationDriver

::: isolattice.BoundaryState

::: isolattice.Observation

::: isolattice.terminate_boundary

## Engine clients

Container backends start their engine clients through a `CommandRunner`.
Pass your own runner to add auditing, tracing or a wrapper for a remote engine.

::: isolattice.CommandRunner

::: isolattice.SubprocessRunner
