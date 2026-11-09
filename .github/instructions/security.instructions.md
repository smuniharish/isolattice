---
description: "Use for policy, containment, termination, native backends, artifacts or remote security changes."
applyTo: "src/isolattice/backends/**/*.py,src/isolattice/policy/**/*.py,src/isolattice/runtime/**/*.py,src/isolattice/snapshots/**/*.py,src/isolattice/checkpoints/**/*.py"
---

# Security-sensitive work

Consult the matching platform/process/termination/remote/artifact skill and
[threat model](../../docs/architecture/17-security-threat-model.md) before coding.
Required guarantees must be enforced or rejected. Record accepted preferred
alternatives. Never substitute process groups/PID recursion for containment.

UNKNOWN boundary state is never success. Restrict workload access to management
sockets, brokers, credentials and migration authority. Treat archives and
checkpoint state as untrusted/sensitive; hashes alone are not authenticity.
Document TCB/residual risks and test real configured enforcement.
