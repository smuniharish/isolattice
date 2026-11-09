# Windows backends

Isolattice has two Windows backends. `WindowsAppContainerBackend` runs workloads under
an AppContainer token inside nested Job Objects and is the one to use when you need
isolation. `WindowsJobBackend` only contains and limits the process tree in a Job
Object; it is **not a security boundary**. Both use only the standard library
(`ctypes`), are importable everywhere and report themselves unavailable off Windows.

!!! warning "Know the boundary"
    AppContainers share the host kernel; kernel exploits are out of scope. The Job
    Object backend is containment only, not a security boundary: workloads keep the
    caller's filesystem, network and registry access. Status: implemented; verified
    live on Windows 11.

| | `WindowsAppContainerBackend` | `WindowsJobBackend` |
| --- | --- | --- |
| Security boundary | AppContainer token (LPAC by default) | None; optional low-integrity restricted token |
| Filesystem, registry | Denied except granted or package-readable objects | Everything the user can reach |
| Network | Denied, including loopback (`network=NONE`) | Unrestricted (`network=ENABLED` required) |
| Root filesystem policy | `read_only_root=True` required | `read_only_root=False` required |
| Containment | Execution job nested in an instance job | Per-execution job |
| Budgets | Per execution and per instance | Per execution |
| Workspace, snapshots | Yes | No |

## AppContainer backend

Each instance gets its own AppContainer profile (`isolattice.<uuid>`), by default a
less privileged AppContainer (LPAC), and a private instance directory granted only to
that profile. Each execution runs in a Job Object nested in an instance Job Object
before its first instruction. No network capability is ever granted.

### Requirements

- Windows 10 1703 or later; no administrator rights or services.
- An `argv[0]` that is an absolute path readable by the AppContainer. Python from a
  python.org install under `C:\Program Files` works under LPAC without grants;
  interpreters inside a user profile (for example uv-managed CPython) are not readable
  and still failed with `STATUS_DLL_NOT_FOUND` when granted.

### Configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `read_only_paths` | `()` | Extra trees granted read/execute to the instance; must exist |
| `state_directory` | `None` | Parent of instance directories; `None` uses `TEMP` |
| `less_privileged` | `True` | Use LPAC; `False` uses a plain AppContainer |
| `startup_timeout` | `15.0` | Seconds allowed for the launcher |
| `helper_interpreter` | `None` | Base Python used for the launcher; `None` uses the base interpreter |

### Example

```python
import asyncio
import sys
from pathlib import Path

from isolattice import (
    ExecutionRequest,
    ResourceBudget,
    Sandbox,
    SandboxPolicy,
    WindowsAppContainerBackend,
)

PYTHON = str(Path(sys.base_prefix) / "python.exe")


async def main() -> None:
    backend = WindowsAppContainerBackend()
    policy = SandboxPolicy(
        memory_bytes=128 * 1024**2,
        process_limit=8,
        workspace=True,
        instance_budget=ResourceBudget(memory_bytes=256 * 1024**2),
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(argv=(PYTHON, "-I", "-c", "print('hello')"), cwd="/workspace")
            )
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

The environment contains only `SYSTEMROOT`, `LOCALAPPDATA` and explicit values;
Windows points `TEMP` and `TMP` at the profile. `cwd` `/workspace` addresses the
workspace directory. `SandboxInstance.snapshot(key)` and
`Sandbox.create(workspace_snapshot=key)` save and restore the workspace.

`prepare` rejects `network` other than `NONE`, `read_only_root=False` and
`scratch_bytes`. Discovery and preparation run a real probe through the whole chain.

### Capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `NETWORK_ISOLATION` | SUPPORTED | No network capabilities; outbound, inbound and loopback denied |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | SUPPORTED | Job committed memory, hard CPU rate, active process count |
| `INSTANCE_LIMITS` | SUPPORTED | Instance Job Object above every execution job |
| `SNAPSHOTS`, `WORKSPACE` | SUPPORTED | Cold tar snapshots of a private directory granted only to the instance |
| `FILESYSTEM_ISOLATION` | LIMITED | No user profile access, but package-readable system directories and `read_only_paths` stay readable |
| `PROCESS_ISOLATION` | LIMITED | Cannot open the user's processes; same session and desktop; brokered services reachable |
| `VERIFIED_TERMINATION` | LIMITED | Job accounting verified empty; processes brokered by other services are not contained |
| `ENVIRONMENT_ISOLATION` | LIMITED | Host environment not inherited; `SYSTEMROOT`, `LOCALAPPDATA`, `TEMP`, `TMP` set |
| `RESOURCE_LIMITS` | LIMITED | No disk or network budgets |
| `CHECKPOINTS` | UNSUPPORTED | Windows provides no process checkpoint facility |
| `DISK_LIMIT` | UNSUPPORTED | The instance directory has no quota |

### Limitations

- Workloads share the host kernel, session and desktop (window messages to other
  low-integrity windows, clipboard) and can reach services that accept AppContainer
  clients; processes those services create are outside the job.
- PowerShell does not start under LPAC; use `less_privileged=False` if required.
- Grants rewrite the DACL of every file in a `read_only_paths` tree, which is slow for
  large trees. Prefer small trees.
- Termination is forced (`TerminateJobObject`); Job Objects have no graceful stop.
- After a host crash, `KILL_ON_JOB_CLOSE` kills the workloads, but profiles
  (`isolattice.*`), `isolattice-ac-*` directories and grants remain and must be
  removed manually.

## Job Object backend

`WindowsJobBackend` contains each execution's process tree in its own Job Object,
enforces memory, CPU and process budgets and verifies tree termination. Workloads run
with the calling user's rights: filesystem, registry, network and system services stay
reachable. It has no workspaces and no snapshots.

!!! warning "Not a security boundary"
    A workload can read and write anything the user can, use the network, and escape
    the job through brokers such as WMI, the Task Scheduler, services or COM servers.
    Use `WindowsAppContainerBackend` when isolation matters.

### Job Object configuration

| Parameter | Default | Meaning |
| --- | --- | --- |
| `working_directory` | required | Absolute existing directory; request `cwd` paths map under it (`/` is the directory itself) |
| `restricted_token` | `False` | Start workloads under a restricted, low-integrity copy of the user's token |
| `startup_timeout` | `30.0` | Seconds allowed to start a workload |
| `helper_interpreter` | `None` | Base Python used for the launcher; `None` uses the base interpreter |

### Job Object example

```python
import asyncio

from isolattice import (
    ExecutionRequest,
    NetworkAccess,
    Sandbox,
    SandboxPolicy,
    WindowsJobBackend,
)


async def main() -> None:
    backend = WindowsJobBackend(working_directory=r"C:\isolattice\work")
    policy = SandboxPolicy(
        network=NetworkAccess.ENABLED,
        read_only_root=False,
        memory_bytes=128 * 1024**2,
        process_limit=4,
    )
    async with Sandbox(backend=backend) as sandbox:
        async with await sandbox.create(policy=policy) as instance:
            result = await instance.run(
                ExecutionRequest(argv=(r"C:\Windows\System32\cmd.exe", "/c", "echo hello"))
            )
            print(result.stdout.decode(), end="")


asyncio.run(main())
```

`argv[0]` must be an absolute Windows path with a drive; there is no search-path
resolution. Only `SYSTEMROOT` and explicit environment values are passed. For Python
workloads use the base interpreter (`Path(sys.base_prefix) / "python.exe"`); a
virtual-environment `python.exe` launches an extra process that counts against
`process_limit`.

`prepare` requires `network=NetworkAccess.ENABLED` and `read_only_root=False` to
acknowledge what the backend cannot restrict, and rejects `instance_budget`,
`scratch_bytes` and `workspace=True`.

### Restricted low-integrity tokens

With `restricted_token=True` each workload starts through an out-of-process launcher
under a copy of the user's token with every privilege except `SeChangeNotifyPrivilege`
removed, `BUILTIN\Administrators` deny-only and low mandatory integrity. It cannot
write medium-integrity files or registry keys or open the user's processes for write
access. It can still read everything the user can, write low-integrity locations, use
the network and ask system services to act for it. Discovery launches a probe and
reports the backend unavailable if the probe does not run at low integrity.

### Job Object capabilities

| Capability | Reported state | Reason |
| --- | --- | --- |
| `MEMORY_LIMIT`, `CPU_LIMIT`, `PROCESS_LIMIT` | SUPPORTED | Job committed memory, hard CPU rate, active processes (threads not counted) |
| `VERIFIED_TERMINATION` | LIMITED | No breakaway; zero active processes before release; brokered processes not contained |
| `ENVIRONMENT_ISOLATION` | LIMITED | Only `SYSTEMROOT` and explicit values |
| `RESOURCE_LIMITS` | LIMITED | Per-execution budgets only |
| `FILESYSTEM_ISOLATION`, `PROCESS_ISOLATION` | UNSUPPORTED, or LIMITED with `restricted_token=True` | Same user; low integrity only blocks writes to medium-integrity objects |
| `NETWORK_ISOLATION` | UNSUPPORTED | Job Objects cannot restrict networking |
| `SNAPSHOTS`, `WORKSPACE`, `CHECKPOINTS` | UNSUPPORTED | Not implemented |
| `INSTANCE_LIMITS` | UNSUPPORTED | No aggregate instance budget |
| `DISK_LIMIT` | UNSUPPORTED | Job Objects cannot cap disk usage |

### Job Object limitations

- Same-user processes outside the job can open, inject into or control contained
  processes, and the reverse.
- Committed-memory limits do not bound kernel or file-cache memory; allocations beyond
  the limit fail inside the workload instead of killing it.
- Termination is forced; if the owning Python process dies, `KILL_ON_JOB_CLOSE` ends
  the tree. If the host already places Python in a job that forbids nesting, assignment
  fails closed.

## Live testing

The Windows suites need no environment variables; they run automatically on Windows
and skip elsewhere:

- [test_windows_appcontainer.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_appcontainer.py):
  LPAC and plain AppContainers, denial of the profile, HKCU, loopback, host processes,
  scheduled tasks and WMI, workspaces and snapshots, budgets and crash cleanup.
  Interpreter-dependent tests skip when the base interpreter is not readable by
  restricted application packages.
- [test_windows_backend.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_windows_backend.py):
  I/O, descendant termination, denied breakaway, timeouts, budgets, owner crash and the
  restricted token.

```powershell
uv run pytest tests/integration/test_windows_appcontainer.py tests/integration/test_windows_backend.py
```

Results come from one Windows 11 host. Examples:
[AppContainer](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_appcontainer.py),
[restricted token](https://github.com/smuniharish/isolattice/blob/main/examples/08-native-backends/windows_restricted_token.py),
[Job limits](https://github.com/smuniharish/isolattice/blob/main/examples/04-resource-management/windows_job_limits.py).

## Related

- [Platform limitations](../security/platform-limitations.md)
- [Process containment](../architecture/10-process-containment.md)
- [Resource limits](../guides/resource-limits.md)
- [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
  and [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)
