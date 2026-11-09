---
name: isolattice
description: >-
  Use this skill when running commands, scripts or generated code through the
  isolattice Python package: choosing and configuring a backend (Podman,
  Docker API, Kubernetes, Linux namespaces, Windows Job Object or
  AppContainer), writing a SandboxPolicy with required capabilities and
  limits, streaming output, handling timeouts, cancellation and
  TerminationVerificationError, taking workspace or container snapshots and
  CRIU checkpoints, or troubleshooting a SandboxCapabilityError. Do not use it
  to write a custom ArtifactStore, EventSink, CommandRunner or Backend; the
  isolattice-extend skill covers those. Do not use it for plain Docker,
  Podman or Kubernetes work that does not involve isolattice.
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer with isolattice 0.1 installed (pip install isolattice;
  pip install "isolattice[s3]" for S3 storage). Running workloads needs one
  prepared backend host; the bundled script and template run offline.
metadata:
  version: "0.1.0"
  documentation: "https://isolattice.readthedocs.io/en/latest/"
---

# Running workloads with isolattice

isolattice runs argv commands inside a backend-enforced boundary behind one
async API. The application states the guarantees it needs; the backend
reports what it can enforce on this host, and a required guarantee it cannot
enforce raises `SandboxCapabilityError` before anything starts.

Containers, Pods, Linux namespaces and AppContainers share the host (or node)
kernel, so kernel, engine, runtime or cluster exploits are out of scope, and
the Windows Job Object backend is containment only, not a security boundary.
Never tell a user that isolattice protects against kernel exploits.

## Workflow

1. Run `python scripts/verify_setup.py` in the project's environment. Fix every
   FAIL before writing code. It also lists which engine clients are on `PATH`.
2. Pick the backend from the host, using
   [references/backends.md](references/backends.md). Never fall back to a
   weaker backend silently; ask the user when the preferred host is missing.
3. Copy `assets/policy_template.py` into the project and adapt
   `workload_policy()`. Put every guarantee the application relies on in
   `required_capabilities`, and set the matching explicit setting (budget,
   `scratch_bytes`, `workspace`).
4. Copy `assets/test_policy.py` and run it. It must pass before you wire the
   policy into the application.
5. Call `await sandbox.discover()` once on the real host and log the report.
   Use `unmet_requirements()` from the template to explain a refusal.
6. Write the execution code with the patterns below. Read
   [references/API.md](references/API.md) for signatures, streaming,
   snapshots and checkpoints.
7. Run the application's own tests on the real backend, then run
   `scripts/verify_setup.py` again. Do not finish until both pass.

## Core pattern

```python
from isolattice import (
    Capability,
    ExecutionRequest,
    PodmanBackend,
    Sandbox,
    SandboxPolicy,
)

policy = SandboxPolicy(
    memory_bytes=256 * 1024 * 1024,
    process_limit=64,
    required_capabilities=(Capability.NETWORK_ISOLATION, Capability.MEMORY_LIMIT),
)
backend = PodmanBackend(connection="podman-machine-default", image="python:3.14-slim")
async with (
    Sandbox(backend=backend) as sandbox,
    await sandbox.create(policy=policy) as instance,
):
    result = await instance.run(ExecutionRequest(argv=("python", "-c", "print(6 * 7)")))
```

## Rules

- Pass argv tuples. Never build a shell string from untrusted input; when a
  shell is needed, pass the untrusted value through `stdin` or `environment`.
- Always use `async with` for `Sandbox` and `SandboxInstance`, so cleanup runs
  on errors and cancellation.
- Keep `network=NetworkAccess.NONE` unless the user asks for network access.
- Keep output bounded: check `stdout_truncated` and `stderr_truncated` instead
  of raising the limits without need.
- Treat `TerminationVerificationError` as "cleanup not verified", never as
  success. Log it, keep the instance owned, and alert an operator.
- Never put secrets in argv. Pass them through `environment` or `stdin`, and
  never log `ExecutionResult.stdout` when it can contain them.

## Gotchas

- A required capability that needs a setting fails at construction: for
  example `Capability.INSTANCE_LIMITS` without `instance_budget` raises
  `ValueError`.
- Rootless Podman enforces limits only when the connection has delegated
  cgroup controllers. CRIU checkpoints and pod instance budgets need a rootful
  connection and `allow_rootful=True`.
- `WindowsJobBackend` contains and limits a process tree but is not a security
  boundary: files, network, registry and system services stay reachable. Use
  `WindowsAppContainerBackend` for isolation on Windows.
- `WindowsJobBackend` refuses the defaults on purpose: it needs
  `network=NetworkAccess.ENABLED` and `read_only_root=False`, which acknowledge
  that it cannot restrict either. `KubernetesBackend` needs
  `network=NetworkAccess.ENABLED` because Pods always join the cluster network.
- `DockerBackend` was verified against Podman's Docker-compatible API and Docker
  Engine in `docker:dind`, not on a native Docker host.
- Snapshots and checkpoints need `Sandbox(artifact_store=...)`.
- Read [references/TROUBLESHOOTING.md](references/TROUBLESHOOTING.md) for each
  `SandboxCapabilityError` message and its fix.

## Files

- `scripts/verify_setup.py`: checks Python, the installed package, the `s3`
  extra (`--s3`) and a local artifact store, offline. `--json` prints one
  object. Exit codes are 0 (all checks pass) and 1 (a check failed).
- `assets/policy_template.py`: a strict workload policy and
  `unmet_requirements(report, policy)` to explain a refusal.
- `assets/test_policy.py`: pytest checks the policy must keep.
- [references/API.md](references/API.md): signatures, streaming,
  cancellation, snapshots, checkpoints and events.
- [references/backends.md](references/backends.md): choosing a backend, host
  preparation and what each one enforces.
- [references/TROUBLESHOOTING.md](references/TROUBLESHOOTING.md): error
  messages and fixes.
