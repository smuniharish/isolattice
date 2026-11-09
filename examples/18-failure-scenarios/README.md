---
category: 18
status: runnable
---

# 18 Failure scenarios

Shows that an unenforceable policy fails closed and that a timed-out workload is
cleaned up with verification.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_failures.py`](https://github.com/smuniharish/isolattice/blob/main/examples/18-failure-scenarios/podman_failures.py) | `NetworkAccess.ALLOWLIST` rejected with `SandboxCapabilityError`; `SandboxTimeoutError` with a verified `TerminationReport` | Rootless Podman |

Other failure paths are exercised by
[07](../07-capabilities-and-backends/README.md) (rejected policies),
[08](../08-native-backends/README.md) (OOM kill, timeouts, rejected capabilities) and
[09](../09-container-integrations/README.md) (rejected workspace).

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit **rootless** Podman connection and an already-local image containing
  `/bin/sh`.

## Run

```shell
uv run python examples/18-failure-scenarios/podman_failures.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

```text
Unsupported network policy: rejected
Timeout cleanup: terminated
Owned container released: True
```

## Acceptance

- `Sandbox.create()` with `network=NetworkAccess.ALLOWLIST` must raise
  `SandboxCapabilityError`; no backend supports allowlists.
- `/bin/sh -c "sleep 60"` with a 12 s request timeout must raise
  `SandboxTimeoutError` whose `report` is not `None` and has
  `resources_released=True`. Anything else raises `RuntimeError`.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs the script and checks these lines.

## Cleanup

The rejected policy fails before any container is allocated. The timed-out container
is terminated, verified and removed before the error is raised.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- Failures never select a weaker backend or policy.
- A termination state of unknown is never success: when `cleanup_deadline` expires,
  `TerminationVerificationError` is raised and the unfinished cleanup stays owned and
  is reported through `LATE_CLEANUP_*` events.
- Error messages do not include workload output or environment values.

See the [errors API](https://isolattice.readthedocs.io/en/latest/api/errors/) and
[troubleshooting](https://isolattice.readthedocs.io/en/latest/operations/troubleshooting/).
