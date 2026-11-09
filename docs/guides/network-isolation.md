# Network isolation

Isolattice expresses network access with one field, `SandboxPolicy.network`, set to a
`NetworkAccess` value. Each backend accepts only the value it can actually provide.

!!! warning "Shared-kernel backends"
    Container, namespace and AppContainer backends share the host or node kernel,
    so kernel and engine exploits are out of scope, and no backend filters traffic
    by destination.

## Values

| Value | Meaning |
| --- | --- |
| `NetworkAccess.NONE` (default) | The workload gets no external network. A private loopback may remain. |
| `NetworkAccess.ENABLED` | The workload uses the network the backend provides, unfiltered. |
| `NetworkAccess.ALLOWLIST` | Destination allowlists. No backend implements this, so every backend rejects it. |

## Per-backend support

| Backend | Accepted value | `NETWORK_ISOLATION` | How |
| --- | --- | --- | --- |
| Linux namespaces | `NONE` | SUPPORTED | A new network namespace with only loopback |
| Windows Job Object | `ENABLED` | UNSUPPORTED | Job Objects do not restrict network access |
| Windows AppContainer | `NONE` | SUPPORTED | No network capability SIDs are granted |
| Podman | `NONE` | LIMITED | `--network=none`, verified by container inspection |
| Docker API | `NONE` | LIMITED | `--network none`, verified by container inspection |
| Kubernetes | `ENABLED` | UNSUPPORTED | Pod networking; `NetworkPolicy` enforcement depends on the CNI plug-in |

A mismatched value raises `SandboxCapabilityError` from `Sandbox.create()`. Nothing
falls back to a weaker mode.

## Require isolation explicitly

Require `Capability.NETWORK_ISOLATION` when your workload must not reach the
network. The backend then refuses to prepare unless its evidence is SUPPORTED on this
host:

```python
from isolattice import Capability, NetworkAccess, SandboxPolicy

offline = SandboxPolicy(
    network=NetworkAccess.NONE,
    required_capabilities=(Capability.NETWORK_ISOLATION,),
)
```

With this policy only the Linux namespace and AppContainer backends can prepare a
sandbox. Podman and Docker report LIMITED evidence, so they reject the requirement
even though they disable networking.

## Check the evidence before creating

`Sandbox.discover()` returns one `BackendReport` with evidence per capability:

```python
from isolattice import Capability, Sandbox


async def describe_network(sandbox: Sandbox) -> None:
    (report,) = await sandbox.discover()
    evidence = report.capabilities[Capability.NETWORK_ISOLATION]
    print(report.name, evidence.state.value, evidence.detail)
```

## Backends that need network access enabled

The Windows Job Object and Kubernetes backends cannot remove the network, so their
policies must say so:

```python
from isolattice import NetworkAccess, SandboxPolicy

policy = SandboxPolicy(network=NetworkAccess.ENABLED)
```

This is an honest declaration, not a feature. On Kubernetes, add a `NetworkPolicy`
to the namespace yourself; Isolattice cannot verify that the CNI plug-in enforces it.
See [Kubernetes integration](kubernetes-integration.md).

## Related pages

- [Filesystem isolation](filesystem-isolation.md)
- [Capability reference](../reference/capabilities.md)
- [Capability model](../concepts/capability-model.md)
