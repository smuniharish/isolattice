---
category: 13
status: runnable
---

# 13 Checkpoints

Checkpoints a running process with CRIU and resumes it in a new instance from the
saved execution position.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_checkpoint.py`](https://github.com/smuniharish/isolattice/blob/main/examples/13-checkpoints/podman_checkpoint.py) | `handle.checkpoint(key)` of a running counter, `instance.restore(key)` in a new instance | **Rootful** Podman with CRIU |

The workload counts from 0 to 99 every 0.1 s. After it prints 5 the script
checkpoints it (the source keeps running), cancels the source, then restores the
checkpoint into a new instance and waits for it to finish.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicitly selected **rootful** Podman connection (for example
  `podman-machine-default-root`) with CRIU available to the engine. Rootless engines
  report `CHECKPOINTS` as unsupported.
- An already-local image containing `/bin/sh`.

## Run

```shell
uv run python examples/13-checkpoints/podman_checkpoint.py --connection podman-machine-default-root --image docker.io/library/alpine:3.22 --allow-rootful
```

## Expected output

```text
Checkpointed after the counter reached 5; the source keeps running
Restored execution continued from <n> to 99
Restored exit code: 0
Owned containers released: True
```

`<n>` is at least 6 and depends on how long the CRIU dump took.

## Acceptance

- The policy requires `Capability.CHECKPOINTS` with `execution_timeout=120`; creating
  the instance probes CRIU live.
- The restored stdout must be a contiguous run of integers ending at 99 and starting
  above 5, showing that execution resumed rather than restarted.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs the script (`ISOLATTICE_TEST_PODMAN_ROOTFUL_CONNECTION`,
  `ISOLATTICE_TEST_PODMAN_ROOTFUL_IMAGE`).

## Cleanup

The source execution is cancelled and its container verified removed; the restored
execution's container is released when it exits. The checkpoint artifact lives in a
temporary `LocalArtifactStore` that is deleted on exit.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
  Rootful engines need
  `allow_rootful=True` and run the engine with host root.
- The capability is reported `LIMITED`: restored output comes from size-bounded
  engine logs, and checkpoints cannot be combined with workspaces, instance budgets
  or a writable root.
- Checkpoint archives contain workload memory, including any secrets. Treat them as
  confidential, trusted restore inputs. Restore validates the archive's container
  spec (unprivileged user, no capabilities, read-only root, private namespaces,
  seccomp, no devices) before use.

See [checkpoints](https://isolattice.readthedocs.io/en/latest/guides/checkpoints/).
