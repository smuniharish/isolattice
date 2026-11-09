# Isolattice repository instructions

Follow [AGENTS.md](../AGENTS.md) and the
[architecture decisions](../docs/architecture/22-architecture-decision-records.md).
Container, Pod, namespace and AppContainer backends share the host (or node)
kernel, so kernel, engine, runtime or cluster exploits are out of scope; the
Windows Job Object backend is not a security boundary. The native macOS backend
and remote execution remain proposed.
Get architecture approval for new subsystems; never advertise proposed or
limited capabilities as production-qualified support.

Keep core async-only for execution/lifecycle, platform enforcement behind typed
backend contracts, required policies fail-closed, and cleanup identity-based.
Extension points are ABCs, never Protocols, and are injectable.
Use relevant skills under `.github/skills` before security-sensitive changes.
Inspect files first, preserve user work, retain causes and cleanup failures,
redact sensitive workload data and validate the exact requirement.

Run the commands in the [contributing guide](../docs/development/contributing.md).
Update feature coverage, docs, tests and examples together. Do not infer production
readiness from a passing docs build, mock test or platform skip.
