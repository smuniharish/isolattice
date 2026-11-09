---
category: 12
status: runnable
---

# 12 Snapshots

Cold filesystem snapshots stored as sealed artifacts and restored into a new instance.

| Script | What it shows | Engine |
| --- | --- | --- |
| [`podman_workspace_snapshot.py`](https://github.com/smuniharish/isolattice/blob/main/examples/12-snapshots/podman_workspace_snapshot.py) | Persistent `/workspace` across executions, `instance.snapshot(key)`, `Sandbox.create(workspace_snapshot=key)` | Rootless Podman |
| [`podman_container_snapshot.py`](https://github.com/smuniharish/isolattice/blob/main/examples/12-snapshots/podman_container_snapshot.py) | `handle.snapshot_container(key)` of a running workload's root filesystem, `Sandbox.create(container_snapshot=key)` | Rootless Podman |

Both scripts store artifacts in a `LocalArtifactStore` under a temporary directory.
Workspace snapshots also work with `LinuxNamespaceBackend` and
`WindowsAppContainerBackend` (see [08](../08-native-backends/README.md)).

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- An explicit Podman connection (rootless by default; `--allow-rootful` for an
  explicitly chosen rootful engine) and an already-local image containing `/bin/sh`.

## Run

```shell
uv run python examples/12-snapshots/podman_workspace_snapshot.py --connection podman-machine-default --image docker.io/library/alpine:3.22
uv run python examples/12-snapshots/podman_container_snapshot.py --connection podman-machine-default --image docker.io/library/alpine:3.22
```

## Expected output

`podman_workspace_snapshot.py`:

```text
Second execution reads: state from run 1
Snapshot sha256: <first 16 hex digits>...
New instance reads: state from run 1
Owned volumes removed: True
```

`podman_container_snapshot.py`:

```text
Snapshot of the running workload: <size> bytes
Snapshot sha256: <first 16 hex digits>...
New instance reads: model weights v1
Restored root is: read-only
Owned containers and imported image removed: True
```

## Acceptance

- Workspace: with `workspace=True` and `WORKSPACE` and `SNAPSHOTS` required, a file
  written to `/workspace` in one execution must be readable in the next, and must be
  restored byte for byte into a new instance created from the snapshot key.
- Container: with `read_only_root=False` and `SNAPSHOTS` required, the workload writes
  `/var/tmp/app/state.txt` and prints `ready`; `snapshot_container()` pauses it,
  exports the root filesystem as a `rootfs-tar` artifact and resumes it. A new
  instance with the default read-only root must read the file and fail to write.
- Isolattice computes the `ArtifactManifest` (size and SHA-256) and re-verifies it
  after every fetch. Container snapshot imports reject devices and special files,
  unsafe names, dangling hard links and oversized archives.
- [`tests/integration/test_podman_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_backend.py)
  runs both scripts;
  [`test_podman_container_snapshot.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_podman_container_snapshot.py)
  covers more container snapshot cases.

## Cleanup

Closing an instance removes its owned workspace volume and containers; closing the
restored instance also removes the image imported from the container snapshot. The
temporary artifact directory is deleted when the script exits.

## Security

- Containers share the host kernel; kernel or engine exploits are out of scope.
- Snapshots are cold filesystem state, never process memory. Container snapshots
  exclude the workspace volume and the `/tmp` scratch.
- A SHA-256 manifest detects corruption; it does not authenticate who produced the
  artifact. Keep artifact stores trusted and access-controlled.
- Workspace volumes and writable layers have no disk quota, so requiring `DISK_LIMIT`
  with either is rejected.
- Do not snapshot secrets you would not store in the artifact store.

See [snapshots](https://isolattice.readthedocs.io/en/latest/guides/snapshots/) and the
[storage API](https://isolattice.readthedocs.io/en/latest/api/storage/).
