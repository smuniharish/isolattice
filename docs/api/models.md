# Policies and models

Policies, requests and results are frozen dataclasses validated on
construction. Building them performs no I/O.

## Policies

::: isolattice.SandboxPolicy

::: isolattice.TerminationPolicy

::: isolattice.ResourceBudget

::: isolattice.NetworkAccess

## Execution

::: isolattice.ExecutionRequest

::: isolattice.ExecutionResult

::: isolattice.OutputChunk

::: isolattice.TerminationReport

## Capabilities

::: isolattice.Capability

::: isolattice.CapabilityState

::: isolattice.CapabilityEvidence

::: isolattice.BackendReport

## Artifacts

::: isolattice.ArtifactKind

::: isolattice.ArtifactManifest

::: isolattice.validate_artifact_key
