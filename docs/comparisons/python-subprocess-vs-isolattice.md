# Python subprocess versus Isolattice

[`subprocess`](https://docs.python.org/3/library/subprocess.html) starts a process
with your user's full access to files, network and other processes. Isolattice starts
a workload inside a backend boundary (container, Pod, namespaces, AppContainer or Job
Object), applies a policy and verifies cleanup of the whole process tree.

## When to use which

- **Use `subprocess`** for trusted tools you control: build steps, CLIs, scripts.
  It is in the standard library, has no setup and is the better choice when isolation
  is not the goal.
- **Use Isolattice** when you need resource limits, no network, a read-only root, an
  explicit environment or verified process-tree cleanup, with the policy checked before
  anything runs.
- **Use neither alone** for hostile code; Isolattice backends share the host
  kernel and do not defend against kernel exploits. See
  [VMs and microVMs](vm-and-microvm-vs-isolattice.md).

## Comparison

| | `subprocess` | Isolattice |
| --- | --- | --- |
| API | Synchronous (plus `Popen`) | Async only |
| Filesystem, network, environment | Inherited unless you change them | Backend-enforced per policy |
| Memory, CPU, process limits | None built in | Per execution, per backend |
| Descendant cleanup | Only the direct child is tracked | Whole boundary, verified |
| Output | Unbounded unless you bound it | Bounded with truncation flags |
| Setup | None | A backend: Podman, Linux cgroups, Windows, etc. |

## Example

```python
import subprocess

completed = subprocess.run(["/bin/sh", "-c", "echo hi"], capture_output=True, timeout=10)
print(completed.stdout)
```

```python
from isolattice import ExecutionRequest, SandboxInstance


async def run(instance: SandboxInstance) -> bytes:
    result = await instance.run(ExecutionRequest(argv=("/bin/sh", "-c", "echo hi"), timeout=10))
    return result.stdout
```

The instance comes from `Sandbox.create`; see the
[quickstart](../getting-started/quickstart.md). After a `subprocess` timeout,
background children of the shell can keep running. Isolattice terminates the boundary
and reports whether it was observed empty. See
[process escape prevention](../security/process-escape-prevention.md).
