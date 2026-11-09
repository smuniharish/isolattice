---
category: 3
status: index
---

# 03 Security

This category has no scripts of its own. The runnable security probes live with the
backend that enforces them, because each backend enforces different controls:

| Script | Security behavior it checks |
| --- | --- |
| [`08-native-backends/linux_namespaces.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/linux_namespaces.py) | uid 65534, no capabilities, `no_new_privs`, seccomp, hidden home, read-only root, blocked network |
| [`08-native-backends/windows_appcontainer.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_appcontainer.py) | Denied user profile, registry writes and network; no host environment |
| [`08-native-backends/windows_restricted_token.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_restricted_token.py) | Low integrity level; denied writes and parent-process termination access |
| [`09-container-integrations/docker_isolated_run.py`](https://github.com/smuniharish/isolattice/blob/main/examples/09-container-integrations/docker_isolated_run.py) | uid 65534, zero capabilities, read-only root |
| [`10-kubernetes/kubernetes_isolated_run.py`](https://github.com/smuniharish/isolattice/blob/main/examples/10-kubernetes/kubernetes_isolated_run.py) | uid 65534, zero capabilities, read-only root, no service-account token |
| [`18-failure-scenarios/podman_failures.py`](https://github.com/smuniharish/isolattice/blob/main/examples/18-failure-scenarios/podman_failures.py) | Unsupported network policy rejected before allocation |

Each script observes the denial from inside the workload; a policy object alone is not
evidence. See the linked categories for run commands.

## Prerequisites

The prerequisites of the backend you run: see [08](../08-native-backends/README.md),
[09](../09-container-integrations/README.md), [10](../10-kubernetes/README.md) and
[18](../18-failure-scenarios/README.md).

## Acceptance

A security example in this repository must:

- print the backend's capability evidence (`SUPPORTED`, `LIMITED` or `UNSUPPORTED`);
- show that an allowed operation works and that a denied operation actually fails
  inside the workload;
- show that a policy requiring an unavailable capability raises
  `SandboxCapabilityError` instead of running with weaker isolation.

Network behavior differs by backend: Podman, Docker, Linux and AppContainer require
`network=NetworkAccess.NONE`; Kubernetes and Windows Job require
`NetworkAccess.ENABLED`; no backend supports `NetworkAccess.ALLOWLIST`.

## Cleanup

Every probe ends with verified termination of its boundary
(`termination.resources_released`). Negative probes use only benign operations.

## Security

- Shared-kernel backends (containers, Pods, namespaces, AppContainers) exclude
  kernel, engine, runtime or cluster exploits from their threat model.
- Missing privileges or skipped tests are never counted as passed enforcement.
- Platform limitations are part of the expected output, not errors to hide.

See the [security model](https://isolattice.readthedocs.io/en/latest/concepts/security-model/),
[guarantees](https://isolattice.readthedocs.io/en/latest/security/guarantees/) and
[platform limitations](https://isolattice.readthedocs.io/en/latest/security/platform-limitations/).
