# Introduction

Isolattice follows one rule: **a portable API, with enforcement specific to each
backend.**

You describe what a workload needs in a `SandboxPolicy`. A backend describes
what it can enforce in a `BackendReport`. Isolattice compares the two before any
workload starts. Anything you required that the backend cannot enforce stops
creation with `SandboxCapabilityError`. Nothing is quietly downgraded.

## The pieces

| Object | Role |
| --- | --- |
| `Backend` | Enforces isolation with one technology, such as Podman or Windows Job Objects. |
| `Sandbox` | Owns one backend, plus optional artifact storage and event sink. |
| `SandboxPolicy` | The guarantees and limits an instance must have. |
| `SandboxInstance` | One policy applied to one backend session. It runs executions. |
| `ExecutionRequest` | One command: `argv`, `stdin`, `environment`, `cwd`, `timeout`. |
| `ExecutionHandle` | A running execution: stream output, wait, cancel, checkpoint. |
| `ExecutionResult` | Exit code, bounded output, duration and a `TerminationReport`. |

```python
async with Sandbox(backend=backend) as sandbox:
    async with await sandbox.create(policy=policy) as instance:
        result = await instance.run(request)
```

## What Isolattice is not

- **Not a defense against kernel exploits.** Each built-in backend is as strong
  as the technology underneath, such as the host kernel, the container engine
  or the Windows token. Containers, namespaces and AppContainers share the host
  or node kernel, and a Windows Job Object alone is not a security boundary.
- **Not an image builder or orchestrator.** It runs workloads in images and
  clusters you already have.
- **Not a sync API.** Execution and lifecycle operations are `async`. Pure
  configuration objects are ordinary immutable dataclasses.

Read [sandboxing fundamentals](sandboxing-fundamentals.md) next, or the
[project overview](../architecture/01-project-overview.md) for the full design.
