---
category: 2
status: runnable
---

# 02 Execution

Shows how an `ExecutionRequest` reaches the workload and how output is captured and
bounded.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_io.py`](https://github.com/smuniharish/isolattice/blob/main/examples/02-execution/podman_io.py) | stdin, explicit environment, `cwd`, two concurrent executions, stdout/stderr limits and truncation | Rootless Podman |

The policy sets `execution_timeout=60`, `stdout_limit=8` and `stderr_limit=4`, and
runs two requests concurrently with `asyncio.gather` on one instance.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit **rootless** Podman connection.
- An already-local image containing `/bin/sh` (for example
  `docker.io/library/alpine:3.22`, pulled with `podman --connection NAME pull`).

## Run

```shell
uv run python examples/02-execution/podman_io.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

```text
stdin/environment/cwd: verified
Bounded capture: abcdefgh
Concurrent boundaries: distinct
Owned container released: True
```

## Acceptance

The script raises `RuntimeError` unless:

- the first request reads `payload` from stdin, sees `GREETING=hello` and runs in
  `/tmp`, then writes `received` to stdout and `warn` to stderr with exit code 0;
- the second request prints 16 bytes, stdout is truncated to `abcdefgh` and
  `stdout_truncated` is `True`;
- the two executions report different `termination.boundary_id` values;
- both report `termination.resources_released`.

The live suite
[`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
runs the script as a separate process and checks these lines.

## Cleanup

Every execution has its own container, which is verified terminated and removed before
its result is returned. Closing the instance and sandbox releases everything else they
own.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- The workload receives only the variables in `ExecutionRequest.environment`; the host
  and image environments are not inherited, and `PATH` is unset.
- Arguments are passed as an argv tuple. Do not build shell strings from untrusted input.
- Workload output is sensitive data. Output limits keep captured memory bounded;
  bytes beyond the limit are discarded and flagged.

See the [execution model](https://isolattice.readthedocs.io/en/latest/concepts/execution-model/)
and [policy reference](https://isolattice.readthedocs.io/en/latest/reference/policies/).
