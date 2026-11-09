# Native sandboxing versus Isolattice

Native sandbox tools apply operating-system primitives directly: on Linux,
[bubblewrap](https://github.com/containers/bubblewrap),
[nsjail](https://github.com/google/nsjail), Firejail or `systemd-run` with cgroup
properties; on Windows, AppContainer and Job Objects through the Win32 API. Isolattice's
native backends use the same kinds of primitives, wrapped in a typed async API with
capability discovery and verified termination.

## When to use which

- **Use a native tool directly** when you need its full configuration surface (custom
  mounts, seccomp policies, network setups, user mappings), a non-Python caller, or a
  tool with a longer security track record. These tools are mature and well reviewed.
- **Use Isolattice's native backends** when a Python application wants one async API
  across Linux, Windows and container backends, with fail-closed required capabilities,
  limits read back from the kernel and verified process-tree cleanup.

## Comparison

| | Native tools (bubblewrap, nsjail, Win32) | Isolattice native backends |
| --- | --- | --- |
| Linux primitives | Namespaces, seccomp, cgroups as configured | User, PID, mount, network, IPC, UTS namespaces; delegated cgroup v2; Landlock and seccomp where available |
| Windows primitives | Anything the API allows | AppContainer (LPAC by default) plus Job Objects, or Job Object only |
| Configuration | Very flexible | Fixed profile, policy-driven |
| Discovery | Manual | `sandbox.discover()` with per-capability evidence |
| Cleanup | Tool-specific | Boundary termination with a `TerminationReport` |
| Maturity | Mature | Implemented; shares the host kernel, so kernel exploits are out of scope; Job Object only is not a security boundary |

## Example

```python
from isolattice import LinuxNamespaceBackend, SandboxPolicy, WindowsAppContainerBackend

linux = LinuxNamespaceBackend(cgroup_root="/sys/fs/cgroup/user.slice/isolattice")
windows = WindowsAppContainerBackend()
policy = SandboxPolicy(memory_bytes=256 * 1024 * 1024)
```

`cgroup_root` must be a delegated cgroup v2 directory you own. The Windows Job backend
is not a security boundary; prefer AppContainer. See the
[Linux](../backends/linux.md) and [Windows](../backends/windows.md) backend pages and
[platform limitations](../security/platform-limitations.md). A macOS backend is
proposed and does not exist yet.
