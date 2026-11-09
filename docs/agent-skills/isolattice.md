# isolattice skill

The `isolattice` skill teaches a coding agent to run commands, scripts and
generated code through Isolattice instead of `subprocess`, with a policy that
fails closed when a backend can't enforce it.

Source:
[`isolattice-skills/skills/isolattice`](https://github.com/smuniharish/isolattice/tree/main/isolattice-skills/skills/isolattice)

## When it loads

The agent loads it for tasks such as:

- choosing and configuring a backend: Podman, Docker API, Kubernetes, Linux
  namespaces, Windows Job Object or AppContainer;
- writing a `SandboxPolicy` with required capabilities and resource limits;
- streaming output, timeouts and cancellation;
- handling `TerminationVerificationError` and late cleanup;
- workspace snapshots, container snapshots and CRIU checkpoints;
- diagnosing `SandboxCapabilityError`.

It doesn't load for writing a custom store, sink, runner or backend; that is
the [isolattice-extend](isolattice-extend.md) skill. It doesn't load for plain
Docker or Kubernetes work that doesn't involve Isolattice.

## Workflow

1. Run `scripts/verify_setup.py` in the project's environment and fix every
   failing check.
2. Choose a backend for the host from `references/backends.md`. Never fall
   back to a weaker backend or to `subprocess` when a guarantee is missing.
3. Copy `assets/policy_template.py` into the project and adapt
   `workload_policy()` to the workload's real needs.
4. Copy `assets/test_policy.py` and run it. It must pass before the policy is
   wired in.
5. Write the integration from the core pattern, then run the project's tests
   and `verify_setup.py` again.

## Core pattern

The skill teaches one pattern: a strict policy whose required guarantees fail
closed, and `async with` for both the sandbox and the instance so cleanup runs
on errors and cancellation.

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

## Rules it enforces

- Built-in backends share the host or node kernel, and the Windows Job Object
  backend is not a security boundary; none defends against kernel exploits.
- A required capability the backend can't enforce raises
  `SandboxCapabilityError`. The agent fixes the policy or the backend, never
  the check.
- `TerminationVerificationError` means the process tree may still be running.
  It is reported, never logged as "stopped".
- Credentials are passed explicitly, never through the workload environment,
  events or logs.

## Bundled files

| File | Purpose |
| --- | --- |
| `scripts/verify_setup.py` | Checks Python, the installed package, the `s3` extra, engine clients on `PATH`, fail-closed policy defaults and the local artifact store. `--s3` requires the extra; `--json` prints one JSON object. |
| `assets/policy_template.py` | `workload_policy()`, a strict policy, and `unmet_requirements(report, policy)`. |
| `assets/test_policy.py` | pytest checks the policy must keep. |
| `references/API.md` | Signatures, streaming, snapshots, checkpoints and errors. |
| `references/backends.md` | Choosing a backend, host prerequisites and refusal rules. |
| `references/TROUBLESHOOTING.md` | Each error, its cause and the correct fix. |

See [Install in your agent](install.md) to install it and
[Evaluation](evaluation.md) for how it is tested.
