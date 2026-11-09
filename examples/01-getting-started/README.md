---
category: 1
status: runnable
---

# 01 Getting started

The smallest complete Isolattice program: discover an explicitly configured Podman
engine, create one sandbox instance and run one shell command in an owned container.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_hello.py`](https://github.com/smuniharish/isolattice/blob/main/examples/01-getting-started/podman_hello.py) | Discovery, `Sandbox.create`, `SandboxInstance.run`, exit code and verified owned cleanup | Rootless Podman |

The script constructs a `PodmanBackend` and passes it to `Sandbox(backend=...)`.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- Podman with an explicit **rootless** connection, listed by
  `podman system connection list` (for example `podman-machine-default`).
- An image that is already present on that engine and contains `/bin/sh`. The script
  never pulls images:

  ```shell
  podman --connection podman-machine-default pull docker.io/library/alpine:3.22
  ```

## Run

```shell
uv run python examples/01-getting-started/podman_hello.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

Both `--connection` and `--image` are required.

## Expected output

```text
Backend: podman
Qualification: Containers share the host kernel; kernel or engine exploits are out of scope.
Hello from Isolattice
Exit code: 0
Owned container released: True
```

## Acceptance

The script raises `RuntimeError` unless all of the following hold:

- `Sandbox.discover()` reports the backend as available;
- the command `("/bin/sh", "-c", "printf 'Hello from Isolattice\n'")` exits with 0
  and stdout is exactly `Hello from Isolattice\n`;
- `result.termination.state` is `"terminated"` and
  `result.termination.resources_released` is `True`.

The live suite
[`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
runs the script as a separate process (`ISOLATTICE_TEST_PODMAN_CONNECTION`,
`ISOLATTICE_TEST_PODMAN_IMAGE`) and checks the output lines above.

## Cleanup

Each execution runs in its own labelled container. Isolattice verifies termination of
that container and removes it before `run()` returns; leaving the `async with` blocks
closes the instance and the sandbox. Only containers created by this script are touched.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope, and
  the engine and image are trusted.
- Podman clears the image environment: the workload sees only
  `ExecutionRequest.environment` and has no `PATH`. Always use an absolute `argv[0]`
  such as `/bin/sh`.
- The default policy disables networking and uses a read-only root filesystem.

See the [quickstart](https://isolattice.readthedocs.io/en/latest/getting-started/quickstart/)
and the [Podman backend](https://isolattice.readthedocs.io/en/latest/backends/podman/) pages.
