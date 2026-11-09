"""A strict isolattice workload policy and a helper that explains refusals.

Copy this file into your project and adapt ``workload_policy``. Keep every
guarantee the application depends on in ``required_capabilities``.
"""

from __future__ import annotations

from isolattice import (
    BackendReport,
    Capability,
    CapabilityState,
    NetworkAccess,
    SandboxPolicy,
    TerminationPolicy,
)

MIB = 1024 * 1024


def workload_policy() -> SandboxPolicy:
    """The policy every untrusted-looking workload of this application runs under."""
    return SandboxPolicy(
        network=NetworkAccess.NONE,
        read_only_root=True,
        memory_bytes=256 * MIB,
        cpu_cores=1.0,
        process_limit=64,
        execution_timeout=60.0,
        stdout_limit=MIB,
        stderr_limit=MIB,
        required_capabilities=(
            Capability.FILESYSTEM_ISOLATION,
            Capability.NETWORK_ISOLATION,
            Capability.PROCESS_ISOLATION,
            Capability.VERIFIED_TERMINATION,
            Capability.MEMORY_LIMIT,
            Capability.CPU_LIMIT,
            Capability.PROCESS_LIMIT,
        ),
        termination=TerminationPolicy(cleanup_deadline=90.0),
    )


def unmet_requirements(report: BackendReport, policy: SandboxPolicy) -> list[str]:
    """Explain each required capability the discovered backend cannot fully enforce."""
    unmet = []
    for capability in policy.required_capabilities:
        evidence = report.capabilities.get(capability)
        if evidence is None:
            unmet.append(f"{capability.value}: not reported by {report.name}")
        elif evidence.state is not CapabilityState.SUPPORTED:
            unmet.append(f"{capability.value}: {evidence.state.value} ({evidence.detail})")
    return unmet
