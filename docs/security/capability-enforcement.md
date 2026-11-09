# Capability enforcement

Isolattice never silently weakens a policy. Creation either produces an instance that
enforces what you asked for, or raises `SandboxCapabilityError` before any workload
runs. The rules are in the [security model](../concepts/security-model.md) and the
[capability model](../concepts/capability-model.md).

## The checks at `Sandbox.create`

The backend's `prepare()` rejects the policy with `SandboxCapabilityError` when:

1. A policy setting cannot be enforced, such as `network=NetworkAccess.NONE` on
   Kubernetes, `workspace=True` on Docker or `scratch_bytes` on Windows Job.
2. A capability in `required_capabilities` is reported `LIMITED` or `UNSUPPORTED`.
3. A required limit has no budget, for example `Capability.MEMORY_LIMIT` without
   `memory_bytes`.
4. Live probing disagrees with the request, for example a missing delegated cgroup
   controller or no CRIU on the engine.

Container and Pod backends also re-inspect what the engine created and verify cgroup
values inside the boundary before exec. A mismatch stops the execution and the boundary
is cleaned up.

## Example

`PodmanBackend` reports `NETWORK_ISOLATION` as `LIMITED`, so requiring it fails closed:

```python
import asyncio

from isolattice import (
    Capability,
    PodmanBackend,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
)


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    policy = SandboxPolicy(
        required_capabilities=(Capability.NETWORK_ISOLATION,),
    )
    async with Sandbox(backend=backend) as sandbox:
        for report in await sandbox.discover():
            evidence = report.capabilities[Capability.NETWORK_ISOLATION]
            print(report.name, evidence.state.value, "-", evidence.detail)
        try:
            instance = await sandbox.create(policy=policy)
        except SandboxCapabilityError as error:
            print("refused:", error)
            return
        await instance.close()


asyncio.run(main())
```

```text
podman limited - only an engine-created network namespace with networking disabled
refused: required capability network_isolation is limited: only an engine-created network namespace with networking disabled
```

The policy still runs with `network=NetworkAccess.NONE` if you drop the requirement:
the setting is enforced as far as the engine allows, and the evidence tells you how far.

## Choosing what to require

- Require every capability your threat model depends on. Leaving one out means you
  accept whatever the backend provides.
- Set the matching budget for each required limit (`memory_bytes`, `cpu_cores`,
  `process_limit`, `scratch_bytes`, `instance_budget`).
- Treat `SandboxCapabilityError` as a configuration result, not a transient error. Do
  not retry with a weaker policy automatically.

See the [guarantees table](guarantees.md) and the
[policy reference](../reference/policies.md).
