---
category: 8
status: runnable
---

# 08 Native backends

Native OS backends that run workloads without a container engine.

| Script | Backend | Platform |
| --- | --- | --- |
| [`linux_namespaces.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/linux_namespaces.py) | `LinuxNamespaceBackend` | Linux with a delegated cgroup v2 subtree |
| [`windows_appcontainer.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_appcontainer.py) | `WindowsAppContainerBackend` | Windows 10 1703 or later |
| [`windows_restricted_token.py`](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_restricted_token.py) | `WindowsJobBackend(restricted_token=True)` | Windows |

More `WindowsJobBackend` scripts are in
[04 Resource management](../04-resource-management/README.md) and
[07 Capabilities](../07-capabilities-and-backends/README.md). A macOS backend is
proposed and does not exist.

## Prerequisites

- Python 3.12+ and a synced checkout (`uv sync`).
- `linux_namespaces.py`: Linux with unprivileged user namespaces, Landlock,
  libseccomp and a process-free cgroup v2 subtree delegated to the calling user with
  the `cpu`, `memory` and `pids` controllers. The wrapper
  [`scripts/linux/isolattice-delegated-run.sh`](https://github.com/smuniharish/isolattice/blob/main/scripts/linux/isolattice-delegated-run.sh)
  creates one in a systemd user scope and exports `ISOLATTICE_LINUX_CGROUP_ROOT`.
  The workload uses the host's `/bin/sh` and `python3` through the read-only system
  paths. Live-tested in WSL2.
- `windows_appcontainer.py`: the workload runs `sys.base_prefix\python.exe`, which
  must be readable by restricted application packages. A python.org install under
  Program Files works; interpreters under the user profile (including uv-managed
  Pythons) fail to load and the script exits with a message.
- `windows_restricted_token.py`: Windows; `--python` selects the workload interpreter
  (default `sys.base_prefix\python.exe`).

## Run

```shell
scripts/linux/isolattice-delegated-run.sh uv run python examples/08-native-backends/linux_namespaces.py
uv run python examples/08-native-backends/windows_appcontainer.py
uv run python examples/08-native-backends/windows_restricted_token.py
```

`linux_namespaces.py` also accepts `--cgroup-root PATH` instead of the environment
variable.

## Expected output

`linux_namespaces.py`:

```text
Backend: linux-namespaces (available: True)
Identity: uid 65534, no capabilities, no_new_privs, seccomp filter
Filesystem: host home hidden, root read-only
Network: blocked
Memory budget: allocation beyond 64 MiB was OOM-killed
Timeout cleanup: terminated
Workspace snapshot: restored into a new instance
Required checkpoints: rejected
```

`windows_appcontainer.py`:

```text
Backend: windows-appcontainer (available: True)
filesystem_isolation: limited
network_isolation: supported
User profile: denied
Registry writes: denied
Network: denied
Host environment: not inherited
Memory budget: allocation beyond 128 MiB failed
Timeout cleanup: terminated
Workspace snapshot: restored into a new instance
Required full filesystem isolation: rejected
```

`windows_restricted_token.py`:

```text
Backend: windows-job (available: True)
filesystem_isolation: limited
process_isolation: ...
Integrity level: low (0x1000)
Read working directory: allowed
Write working directory: denied
Create HKCU registry key: denied
Open this process for termination: denied
Timeout cleanup: terminated
Descendant process terminated: True
```

## Acceptance

- `linux_namespaces.py` requires filesystem, network and process isolation, verified
  termination, memory, process and instance limits and snapshots. It checks uid
  65534, `CapEff` 0, `NoNewPrivs` 1, seccomp mode 2, hidden home, read-only `/etc`
  and blocked network; a 256 MiB allocation must exit 137 under a 64 MiB limit; a
  `sleep` tree must be cleaned up on timeout; a `/workspace` file must survive
  `snapshot()` and `create(workspace_snapshot=...)`; requiring `CHECKPOINTS` must
  raise `SandboxCapabilityError`.
- `windows_appcontainer.py` requires network isolation, memory, process and instance
  limits and snapshots. It checks denied profile listing, HKCU key creation and
  outbound connections, an empty inherited environment, a failed 512 MiB allocation,
  timeout cleanup of a child tree, a workspace snapshot round trip, and rejection of
  required `FILESYSTEM_ISOLATION`.
- `windows_restricted_token.py` checks integrity `0x1000`, allowed reads, denied
  writes to the working directory and HKCU, denied `PROCESS_TERMINATE` on the parent,
  and that a grandchild has exited after timeout.

Live suites:
[`test_linux_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_linux_backend.py)
(`ISOLATTICE_TEST_LINUX_CGROUP_ROOT`),
[`test_windows_appcontainer.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_appcontainer.py)
and
[`test_windows_backend.py`](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_backend.py)
(run automatically on Windows).

## Cleanup

- Linux: each execution has a delegated cgroup leaf; termination uses `cgroup.kill`
  and waits for `populated 0`. The systemd scope removes the subtree when the wrapper
  exits.
- AppContainer: nested Job Objects (instance and execution) deny breakaway and verify
  termination. Closing the instance deletes its AppContainer profile, private
  directory and grants. After a caller crash the job still kills the workload but the
  profile and directory remain.
- Restricted token: Job Object termination; the temporary working directory is
  deleted on exit.
- Snapshots are written to a `LocalArtifactStore` in a temporary directory that is
  deleted on exit.

## Security

Linux namespaces and AppContainers share the host kernel, so kernel exploits are out
of scope. The Windows Job Object backend is containment only, not a security boundary.

- Linux: shares the host kernel; supplementary groups are retained; only
  `NetworkAccess.NONE`; workspaces have no quota; checkpoints are unsupported.
- AppContainer: LPAC by default with no network capability. It shares the
  interactive session and desktop, brokered system services stay reachable, and there
  are no disk quotas or checkpoints. `read_only_paths` grants on large trees are slow.
- Restricted token: low integrity and deny-only Administrators block writes to
  medium-integrity files, keys and processes, but reads, the network and services
  such as WMI stay available. This is hardening, not isolation.

See the [Linux](https://isolattice.readthedocs.io/en/latest/backends/linux/),
[Windows](https://isolattice.readthedocs.io/en/latest/backends/windows/) and
[macOS](https://isolattice.readthedocs.io/en/latest/backends/macos/) backend pages.
