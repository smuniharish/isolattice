<p align="center">
  <img src="https://raw.githubusercontent.com/smuniharish/isolattice/main/docs/assets/logo.svg" alt="Isolattice logo" width="96">
</p>

<h1 align="center">Isolattice</h1>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12%20%7C%203.13%20%7C%203.14%20%7C%203.15-blue.svg" alt="Python 3.12 | 3.13 | 3.14 | 3.15">
  <img src="https://img.shields.io/badge/status-beta-yellow.svg" alt="Status: beta">
  <img src="https://img.shields.io/badge/typing-typed-blue.svg" alt="Typed">
  <img src="https://img.shields.io/badge/dependencies-none-brightgreen.svg" alt="No runtime dependencies">
  <a href="https://github.com/smuniharish/isolattice/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
</p>

**Run commands and generated code from async Python, under policies that are
enforced or refused, never quietly weakened.**

Isolattice is an asynchronous sandbox execution framework. You state the
guarantees a workload needs: no network, a read-only root, 256 MiB of memory,
at most 32 processes, a 30-second timeout. Isolattice asks the backend what it
can actually enforce on this host, refuses the request if a required guarantee
is missing, runs the workload, and verifies that its whole process tree is
gone before reporting it stopped. The same typed API drives Podman, the Docker
API, Kubernetes, Linux namespaces, Windows AppContainer and Windows Job
Objects.

<p align="center">
  <img src="https://raw.githubusercontent.com/smuniharish/isolattice/main/docs/assets/diagrams/architecture-overview.png" alt="Isolattice architecture" width="720">
</p>

> [!IMPORTANT]
> Containers, Pods, Linux namespaces and AppContainers share the host (or node)
> kernel, so kernel, engine, runtime or cluster exploits are outside the threat
> model. Windows Job Objects provide containment and resource control only;
> they are not a security boundary. See the [guarantees](https://isolattice.readthedocs.io/en/latest/security/guarantees/).

## Why Isolattice

An application has to run code it didn't write: a user's report script, a
tool call from an LLM agent, a build step, a grader for submitted exercises.
The usual options each leave a gap:

- **`subprocess` and `asyncio.create_subprocess_exec` isolate nothing.** The
  child has your files, your network and your credentials. Killing the child
  leaves its grandchildren running, and a timeout doesn't bound memory or
  output.
- **Calling `docker run` or `podman run` yourself is easy to get subtly
  wrong.** A missing flag silently grants network access, a resource limit is
  ignored on a host without delegated cgroups, and a cancelled request leaks a
  container nobody owns.
- **Every platform enforces isolation differently.** Linux has namespaces,
  cgroups, seccomp and Landlock; Windows has Job Objects and AppContainers;
  Kubernetes has Pods. Code written for one doesn't move to another, and their
  guarantees aren't equal.
- **Wrappers that promise "secure execution" rarely say what they don't
  enforce.** When a limit can't be applied, the run goes ahead anyway.

How Isolattice addresses each one:

| Problem | What Isolattice does |
| --- | --- |
| No isolation from `subprocess` | Runs each workload inside a backend boundary: a container, a namespace set, a Pod or an AppContainer, with no network and a read-only root by default |
| Engine flags are easy to get wrong | Builds every engine call itself from a typed `SandboxPolicy`, owns every resource it creates, and cleans up by identity on success, error and cancellation |
| Platforms differ | One async API; each backend reports what it enforces on this host as supported, limited or unsupported, with evidence |
| Limits silently ignored | Required capabilities and requested budgets fail closed with `SandboxCapabilityError` before anything runs |
| Orphaned processes | Termination is verified against the boundary, not the direct child; an unverified stop raises `TerminationVerificationError` instead of reporting success |

Isolattice doesn't replace a container engine, an orchestrator or a virtual
machine. It drives them through one policy model, and is explicit about the
guarantees each one does and doesn't give you.

## Highlights

- **Async-only lifecycle.** Create, run, stream, cancel, stop and close are
  `async`; a sandbox and its instances are async context managers that clean
  up on errors and cancellation.
- **Fail-closed policies.** Network access, root writability, memory, CPU,
  process count, scratch space, instance-wide budgets and timeouts are
  enforced or refused. Nothing is downgraded quietly.
- **Capability discovery with evidence.** `Sandbox.discover()` reports each
  capability's state on the current host and why.
- **Verified termination.** Graceful stop, forced kill, retries and a cleanup
  deadline; cleanup that outlives the deadline stays owned and is reported.
- **Bounded output.** Streamed stdout and stderr with per-stream limits and
  truncation flags.
- **Snapshots and checkpoints.** Workspace snapshots on every native backend
  and Podman, whole-container snapshots, and CRIU checkpoints on rootful
  Podman, stored with SHA-256 manifests verified on every read.
- **Extensible through ABCs.** Bring your own `ArtifactStore`, `EventSink`,
  `CommandRunner` or `Backend`. A local store is built in and an S3 store is an
  optional extra.
- **Small and typed.** The core has no runtime dependencies, ships type hints
  and supports Python 3.12 to 3.15.

## Backends

| Backend | Boundary | Live-tested on |
| --- | --- | --- |
| `PodmanBackend` | One restricted container per execution; rootless by default | CI on Ubuntu: rootless, rootful runc with CRIU, and a remote engine over `ssh://`. Locally: Podman machine on Windows 11 |
| `DockerBackend` | One restricted container per execution through the Docker Engine API | CI: Docker Engine on Ubuntu. Locally: Podman's Docker-compatible API |
| `KubernetesBackend` | One owned Pod per execution in an explicit namespace; no network isolation | Single-node kind cluster, in CI and locally |
| `LinuxNamespaceBackend` | User, PID, mount, network, IPC and UTS namespaces, delegated cgroup v2, seccomp and Landlock | CI on Ubuntu 24.04 and WSL 2, each with a delegated systemd scope |
| `WindowsAppContainerBackend` | Less-privileged AppContainer inside nested Job Objects; no network | CI on Windows Server, and Windows 11 |
| `WindowsJobBackend` | Job Object containment and budgets. **Not a security boundary:** files, network and registry stay reachable | CI on Windows Server, and Windows 11 |

**macOS** is a tested host: CI runs the full core suite on macOS 15 and 26 (Apple
silicon and Intel). Workloads on a Mac run through a container backend, against
a Podman machine or a remote engine. A native macOS sandbox backend (Seatbelt)
is proposed and not implemented.

**Remote engines** are supported: a Podman `ssh://` connection, a remote Docker
host or a kubeconfig context. The SSH path is live-tested in CI. A dedicated
Isolattice remote service, with leases and failover, is proposed.

The [tested platforms](https://isolattice.readthedocs.io/en/latest/reference/tested-platforms/)
page lists every CI job and what it verifies. The
[platform limitations](https://isolattice.readthedocs.io/en/latest/security/platform-limitations/)
page lists what each backend doesn't enforce.

## Installation

```bash
pip install isolattice           # core, no dependencies
pip install "isolattice[s3]"     # adds S3ArtifactStore (aiobotocore)
```

Container backends need their engine client: `podman`, a Docker API endpoint,
or `kubectl` with a kubeconfig. The Windows backends need nothing else. The
Linux backend needs a delegated cgroup v2 subtree.

## Quickstart

Run a command on a rootless Podman connection, with no network, a read-only
root, and memory and process limits that must be enforced:

```python
import asyncio

from isolattice import Capability, ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    policy = SandboxPolicy(
        memory_bytes=128 * 1024 * 1024,
        process_limit=32,
        required_capabilities=(Capability.MEMORY_LIMIT, Capability.PROCESS_LIMIT),
    )
    request = ExecutionRequest(
        argv=(
            "/bin/sh",
            "-c",
            "echo hello from $(hostname); wget -q -T 2 -O- http://example.com || echo network: blocked",
        ),
        timeout=10,
    )
    async with Sandbox(backend=backend) as sandbox, await sandbox.create(policy=policy) as instance:
        result = await instance.run(request)
    print(result.stdout.decode(), end="")
    print("exit code:", result.exit_code)
    print("termination:", result.termination.state)


asyncio.run(main())
```

On a connection with delegated cgroup controllers:

```text
hello from 33a5a6dd24ba
network: blocked
exit code: 0
termination: terminated
```

On a connection without them, nothing runs:

```text
isolattice.errors.SandboxCapabilityError: resource limits require delegated controllers: memory, pids
```

Isolattice never pulls images; the image must already be present on the
connection. A Podman workload sees only the environment you pass, with no `PATH`,
to name executables by absolute path. The
[quickstart](https://isolattice.readthedocs.io/en/latest/getting-started/quickstart/)
covers streaming output, cancellation and the other backends.

### Your own storage, events and tooling

Every extension point is an abstract base class, passed in explicitly.
Nothing is discovered or loaded implicitly:

```python
from isolattice import LoggingEventSink, S3ArtifactStore, Sandbox

store = S3ArtifactStore(bucket="sandbox-artifacts", prefix="team-a/")
sandbox = Sandbox(backend=backend, artifact_store=store, event_sink=LoggingEventSink())
```

- **`ArtifactStore`** keeps workspace snapshots, container snapshots and
  checkpoints. Isolattice computes and verifies the integrity manifest, so a
  store never has to vouch for its own data.
  `isolattice.testing.check_artifact_store` checks a custom store against the
  contract.
- **`EventSink`** receives typed lifecycle events, never workload output or
  secrets.
- **`CommandRunner`** controls how `podman`, `docker` and `kubectl` are
  started, for auditing or tracing.
- **`Backend`**, **`BackendSession`** and **`TerminationDriver`** add a new
  isolation technology.

A package built on Isolattice can pass each of these through to its own users.
See [custom integrations](https://isolattice.readthedocs.io/en/latest/guides/custom-integrations/)
and [custom backends](https://isolattice.readthedocs.io/en/latest/guides/custom-backend/).

## Downloads

<p>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2Bsum%2528count%2529%2Bv%2BFROM%2Bpypi.pypi_downloads_per_day%2BWHERE%2Bproject%253D%2527isolattice%2527%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=downloads&amp;color=blue&amp;cacheSeconds=3600" alt="Total downloads"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2Bsum%2528count%2529%2Bv%2BFROM%2Bpypi.pypi_downloads_per_day%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bdate%2B%253E%253D%2BtoStartOfWeek%2528today%2528%2529%252C%2B1%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=this+week&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this week"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2Bsum%2528count%2529%2Bv%2BFROM%2Bpypi.pypi_downloads_per_day%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bdate%2B%253E%253D%2BtoStartOfMonth%2528today%2528%2529%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=this+month&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this month"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2Bsum%2528count%2529%2Bv%2BFROM%2Bpypi.pypi_downloads_per_day%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bdate%2B%253E%253D%2BtoStartOfYear%2528today%2528%2529%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=this+year&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads this year"></a>
</p>
<p>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528toString%2528y%2529%252C%2B%2527%253A%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2BtoYear%2528date%2529%2By%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day%2BWHERE%2Bproject%253D%2527isolattice%2527%2BGROUP%2BBY%2By%2BORDER%2BBY%2By%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=by+year&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by year"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527A%2527%2BAND%2B%2527A%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+A&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country A"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527B%2527%2BAND%2B%2527B%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+B&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country B"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527C%2527%2BAND%2B%2527C%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+C&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country C"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527D%2527%2BAND%2B%2527D%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+D&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country D"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527E%2527%2BAND%2B%2527F%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+E-F&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country E-F"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527G%2527%2BAND%2B%2527G%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+G&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country G"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527H%2527%2BAND%2B%2527H%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+H&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country H"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527I%2527%2BAND%2B%2527I%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+I&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country I"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527J%2527%2BAND%2B%2527J%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+J&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country J"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527K%2527%2BAND%2B%2527K%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+K&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country K"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527L%2527%2BAND%2B%2527M%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+L-M&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country L-M"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527N%2527%2BAND%2B%2527O%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+N-O&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country N-O"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527P%2527%2BAND%2B%2527Q%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+P-Q&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country P-Q"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527R%2527%2BAND%2B%2527R%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+R&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country R"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527S%2527%2BAND%2B%2527S%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+S&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country S"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2Bsubstring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527T%2527%2BAND%2B%2527T%2527%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+T&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country T"></a>
  <a href="https://clickpy.clickhouse.com/dashboard/isolattice"><img class="off-glb" src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fuser%3Ddemo%26query%3DSELECT%2BarrayStringConcat%2528groupArray%2528concat%2528country_code%252C%2B%2527%2B%2527%252C%2BtoString%2528c%2529%2529%2529%252C%2B%2527%2B%257C%2B%2527%2529%2Bv%2BFROM%2B%2528SELECT%2Bcountry_code%252C%2Bsum%2528count%2529%2Bc%2BFROM%2Bpypi.pypi_downloads_per_day_by_version_by_country%2BWHERE%2Bproject%253D%2527isolattice%2527%2BAND%2B%2528substring%2528country_code%252C%2B1%252C%2B1%2529%2BBETWEEN%2B%2527U%2527%2BAND%2B%2527Z%2527%2BOR%2BNOT%2Bmatch%2528country_code%252C%2B%2527%255E%255BA-Z%255D%2527%2529%2529%2BGROUP%2BBY%2Bcountry_code%2BORDER%2BBY%2Bc%2BDESC%252C%2Bcountry_code%2BASC%2529%2BFORMAT%2BJSON&amp;query=%24.data%5B0%5D.v&amp;label=countries+U-Z&amp;color=blue&amp;cacheSeconds=3600" alt="Downloads by country U-Z"></a>
</p>

These badges use the [ClickHouse public PyPI dataset](https://clickpy.clickhouse.com/).
The dataset refreshes daily, so counts may be about a day behind. Shields caches
badge images for up to an hour, and GitHub may also cache them. Weeks start on
Monday (UTC). Explore interactive charts on the
[ClickPy dashboard](https://clickpy.clickhouse.com/dashboard/isolattice).

## Examples

Runnable programs in
[`examples/`](https://github.com/smuniharish/isolattice/tree/main/examples),
each exercised by a live test on a real host:

- [First sandbox on Podman](https://github.com/smuniharish/isolattice/blob/main/examples/01-getting-started/podman_hello.py)
- [Resource limits and instance budgets](https://github.com/smuniharish/isolattice/tree/main/examples/04-resource-management)
- [Streaming and cancellation](https://github.com/smuniharish/isolattice/blob/main/examples/06-async-and-cancellation/podman_stream_cancel.py)
- [Linux namespaces and Windows AppContainer](https://github.com/smuniharish/isolattice/tree/main/examples/08-native-backends)
- [Docker API](https://github.com/smuniharish/isolattice/blob/main/examples/09-container-integrations/docker_isolated_run.py)
  and [Kubernetes](https://github.com/smuniharish/isolattice/blob/main/examples/10-kubernetes/kubernetes_isolated_run.py)
- [Workspace and container snapshots](https://github.com/smuniharish/isolattice/tree/main/examples/12-snapshots)
- [CRIU checkpoints](https://github.com/smuniharish/isolattice/blob/main/examples/13-checkpoints/podman_checkpoint.py)
- [Failure handling](https://github.com/smuniharish/isolattice/blob/main/examples/18-failure-scenarios/podman_failures.py)

## Documentation

Full documentation: **[isolattice.readthedocs.io](https://isolattice.readthedocs.io/en/latest/)**

- [Getting started](https://isolattice.readthedocs.io/en/latest/getting-started/)
- [Concepts](https://isolattice.readthedocs.io/en/latest/concepts/introduction/)
- [Backends](https://isolattice.readthedocs.io/en/latest/backends/podman/)
- [Security model and guarantees](https://isolattice.readthedocs.io/en/latest/security/guarantees/)
- [Architecture](https://isolattice.readthedocs.io/en/latest/architecture/02-architecture-overview/)
- [API reference](https://isolattice.readthedocs.io/en/latest/api/)

Two [Agent Skills](https://isolattice.readthedocs.io/en/latest/agent-skills/)
teach coding agents such as Claude Code, Codex, Cursor and GitHub Copilot to
use Isolattice correctly. `isolattice` moves command execution onto a backend
with a fail-closed policy, and `isolattice-extend` writes stores, event sinks,
runners and backends:

```bash
npx skills add smuniharish/isolattice
```

Changes are listed in the
[changelog](https://github.com/smuniharish/isolattice/blob/main/CHANGELOG.md).

## Contributing

Contributions are welcome. See
[CONTRIBUTING.md](https://github.com/smuniharish/isolattice/blob/main/CONTRIBUTING.md)
for the development setup and checks, and
[SECURITY.md](https://github.com/smuniharish/isolattice/blob/main/SECURITY.md)
to report a vulnerability.

## License

Apache License 2.0. See
[LICENSE](https://github.com/smuniharish/isolattice/blob/main/LICENSE).
