---
hide:
  - navigation
---

# Isolattice

<div class="il-hero" markdown>

**Run workloads under security policies that are enforced, or refused.**

Isolattice is an async Python framework for running commands inside sandboxes.
You state which guarantees a workload needs: no network, a read-only root,
memory and process limits, verified termination. Isolattice finds a backend that
can enforce each of them, or it refuses to start.

[Get started](getting-started/index.md){ .md-button .md-button--primary }
[Why Isolattice](#why-isolattice){ .md-button }

</div>

!!! warning "Know the boundary"

    Containers, namespaces and AppContainers share the host or node kernel, so
    kernel, engine, runtime and cluster exploits are outside the threat model.
    The Windows Job Object backend is containment only, not a security boundary.
    No backend is certified for hostile multi-tenant code. Read
    [platform limitations](security/platform-limitations.md) before you rely on a
    guarantee.

## Why Isolattice

Running a command is easy. Knowing what it could reach, and that it is really
gone afterwards, is not.

| Problem | What Isolattice does |
| --- | --- |
| `subprocess` gives a process, not a boundary. Children escape, keep running and keep your files, network and credentials. | Each execution gets its own containment boundary: a container, a Linux namespace set with a cgroup, or a Windows Job Object. |
| Security options are silently ignored when a platform cannot enforce them. | Every backend reports each capability as supported, limited or unsupported, with evidence. A required capability that is not supported stops creation with `SandboxCapabilityError`. |
| A dead parent process does not mean the workload has stopped. | Termination is verified against the boundary's identity, not a PID. An outcome that cannot be verified is reported as unknown, never as success. |
| Every sandbox technology has its own API. | One typed, async API across Podman, the Docker API, Kubernetes, Linux namespaces and Windows. |
| Snapshots, artifacts and telemetry are tied to one vendor. | Storage, events and engine invocation are abstract base classes you implement or replace. |

## Highlights

- **Fail-closed policies.** Required guarantees are enforced or rejected, never
  downgraded with a warning.
- **Verified termination.** Graceful stop, forced stop and re-observation until
  the boundary is empty, within a deadline you set.
- **Bounded streaming.** Output is streamed with backpressure and capped, with
  truncation flags on the result.
- **Resource limits.** Memory, CPU and process limits per execution, and
  budgets across a whole instance where the backend supports them.
- **Workspaces, snapshots and checkpoints.** Persistent workspaces, verified
  workspace and container snapshots, and CRIU checkpoints on rootful Podman.
- **Your infrastructure.** Local or S3 artifact storage, your own event sink,
  your own command runner, or a backend of your own.
- **No required dependencies.** The core uses only the standard library.

## Backends

| Backend | Boundary | Status |
| --- | --- | --- |
| [Podman](backends/podman.md) | One container per execution, rootless by default | Implemented; verified live in CI (rootless, rootful with CRIU, remote over `ssh://`) and on Podman 6.1.4 locally |
| [Docker API](backends/docker.md) | One container per execution through the Docker Engine API | Implemented; verified live on Docker Engine 29.9.3 and Podman's Docker-compatible API |
| [Kubernetes](backends/kubernetes.md) | One Pod per execution | Implemented; verified live on kind |
| [Linux namespaces](backends/linux.md) | User, PID, mount, network, IPC and UTS namespaces with a delegated cgroup | Implemented; verified live in CI on Ubuntu 24.04 and in WSL2 Ubuntu 24.04 |
| [Windows](backends/windows.md) | AppContainer token with a Job Object, or a Job Object alone | Implemented; verified live in CI on Windows Server and on Windows 11 |
| [macOS](backends/macos.md) | Tested host using the container backends | Core suite tested in CI; native Seatbelt backend proposed |
| [Remote](backends/remote.md) | Remote engines through Podman, Docker or Kubernetes connections | Remote engines supported, with Podman over `ssh://` tested in CI; Isolattice remote service proposed |

See [tested platforms](reference/tested-platforms.md) for every CI job.

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

## A first look

```python
import asyncio

from isolattice import ExecutionRequest, PodmanBackend, Sandbox, SandboxPolicy


async def main() -> None:
    backend = PodmanBackend(
        connection="podman-machine-default", image="docker.io/library/alpine:3.22"
    )
    policy = SandboxPolicy()
    async with Sandbox(backend=backend) as sandbox, await sandbox.create(policy=policy) as instance:
        result = await instance.run(ExecutionRequest(argv=("/bin/echo", "hello")))
    print(result.stdout.decode(), result.termination.state)


asyncio.run(main())
```

Continue with the [quickstart](getting-started/quickstart.md), learn the
[concepts](concepts/introduction.md), or read the
[architecture](architecture/01-project-overview.md).
