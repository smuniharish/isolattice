---
category: 6
status: runnable
---

# 06 Async and cancellation

Streams output from a running execution and cancels it once a readiness marker
appears.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_stream_cancel.py`](https://github.com/smuniharish/isolattice/blob/main/examples/06-async-and-cancellation/podman_stream_cancel.py) | `instance.start()`, `handle.output()` streaming, readiness split across chunks, `handle.cancel(force=True)` | Rootless Podman |

The workload runs `/bin/sh -c "echo READY; sleep 60"`. The script prints each
`OutputChunk`, stops reading at `READY\n` (even when it arrives in fragments), closes
the stream and cancels the execution.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit **rootless** Podman connection and an already-local image containing
  `/bin/sh`.

## Run

```shell
uv run python examples/06-async-and-cancellation/podman_stream_cancel.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

```text
stdout: READY
Termination: terminated
Owned container released: True
```

## Acceptance

- The script raises `RuntimeError("execution ended before its readiness marker")` if
  the stream ends without `READY\n` on stdout.
- `handle.cancel(force=True)` must return a `TerminationReport` with state
  `"terminated"` and `resources_released=True`. (`cancel()` returns `None` only when
  the boundary was not created yet.)
- [`tests/unit/test_examples.py`](https://github.com/smuniharish/isolattice/blob/main/tests/unit/test_examples.py)
  checks fragmented readiness, EOF and stream closing without an engine;
  [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs the script against a real engine.

## Cleanup

The stream is closed in a `finally` block. Cancellation verifies termination of the
owned container and removes it before returning. Leaving the context managers closes
the instance and sandbox.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- Cancelling an `asyncio` task does not by itself kill anything; termination happens
  through the backend boundary and is verified.
- Output queues are bounded (`output_queue_chunks`); a slow consumer applies back
  pressure instead of growing memory.

See [timeouts and cancellation](https://isolattice.readthedocs.io/en/latest/guides/timeout-and-cancellation/).
