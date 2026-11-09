"""Checks the workload policy must keep. Run with pytest."""

from __future__ import annotations

import pytest
from policy_template import unmet_requirements, workload_policy

from isolattice import (
    BackendReport,
    Capability,
    CapabilityEvidence,
    CapabilityState,
    NetworkAccess,
    SandboxPolicy,
)


def test_the_policy_disables_network_and_keeps_the_root_read_only() -> None:
    policy = workload_policy()

    assert policy.network is NetworkAccess.NONE
    assert policy.read_only_root


def test_the_policy_requires_every_limit_it_sets() -> None:
    policy = workload_policy()
    required = set(policy.required_capabilities)

    assert (policy.memory_bytes is None) or Capability.MEMORY_LIMIT in required
    assert (policy.cpu_cores is None) or Capability.CPU_LIMIT in required
    assert (policy.process_limit is None) or Capability.PROCESS_LIMIT in required
    assert Capability.VERIFIED_TERMINATION in required


def test_output_and_time_are_bounded() -> None:
    policy = workload_policy()

    assert policy.stdout_limit <= 16 * 1024 * 1024
    assert policy.stderr_limit <= 16 * 1024 * 1024
    assert policy.execution_timeout <= 3600


def test_a_limited_capability_is_reported_as_unmet() -> None:
    policy = workload_policy()
    capabilities = {
        capability: CapabilityEvidence(CapabilityState.SUPPORTED, "verified")
        for capability in policy.required_capabilities
    }
    capabilities[Capability.NETWORK_ISOLATION] = CapabilityEvidence(
        CapabilityState.LIMITED, "network namespace without egress rules"
    )
    report = BackendReport(name="example", available=True, capabilities=capabilities)

    assert unmet_requirements(report, policy) == [
        f"{Capability.NETWORK_ISOLATION.value}: limited (network namespace without egress rules)"
    ]


def test_a_required_capability_without_its_setting_is_refused() -> None:
    with pytest.raises(ValueError, match="needs its explicit policy setting"):
        SandboxPolicy(required_capabilities=(Capability.WORKSPACE,))
