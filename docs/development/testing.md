# Testing

Isolattice has three kinds of test suites. Unit tests run everywhere and check
contracts with test doubles. The skill suite checks the published Agent Skills.
Live suites drive real engines and operating-system mechanisms; they are the only
evidence that a backend enforces anything, and only for the configuration they
ran on.

!!! warning "Tests are not qualification"
    Passing unit, property or live tests does not qualify any backend for hostile
    or untrusted code. A skipped live test is not a pass.

## Suites

| Suite | Location | Command | Needs |
| --- | --- | --- | --- |
| Unit and property | `tests/unit/` | `uv run pytest tests/unit` | Nothing |
| Skill | `tests/skill/` | `uv run pytest tests/skill` | Nothing |
| Live | `tests/integration/` | See [live suites](#live-suites) | A real engine, OS or service |

Running `uv run pytest` with no path collects all three; live tests without
configuration skip.

Pytest runs with `--strict-config --strict-markers` and turns every warning into
an error. Live suites use the markers `podman`, `docker`, `kubernetes` and `s3`;
the native suites are selected by platform instead of a marker.

### Unit and property tests

The unit suite covers runtime models, the async runtime, termination,
artifacts, fault injection, extension points, the S3 store against an in-memory client,
and the backend contracts (Podman, Docker, Kubernetes, Linux, Windows Job Object,
AppContainer) with recorded engine responses. It also tests the repository
tooling: `scripts/validate_repository.py`, `scripts/check_live_skips.py` and the
example inventory.

Hypothesis property tests live in
[tests/unit/test_properties.py](https://github.com/smuniharish/isolattice/blob/main/tests/unit/test_properties.py).
[tests/conftest.py](https://github.com/smuniharish/isolattice/blob/main/tests/conftest.py)
registers three profiles, selected with the `HYPOTHESIS_PROFILE` environment
variable:

| Profile | Settings |
| --- | --- |
| `default` | Hypothesis default example count, no deadline, `too_slow` health check suppressed |
| `ci` | 300 examples, derandomized, failure blobs printed |
| `thorough` | 2,000 examples, failure blobs printed |

```powershell
$env:HYPOTHESIS_PROFILE = "thorough"
uv run pytest tests/unit/test_properties.py
```

### Skill suite

[tests/skill/test_skill.py](https://github.com/smuniharish/isolattice/blob/main/tests/skill/test_skill.py)
validates the user-facing skills in `isolattice-skills/skills/` (`isolattice` and
`isolattice-extend`): frontmatter through `skills-ref`, links, the bundled
scripts and templates, and their evaluation fixtures. See the
[Agent Skills](../agent-skills/index.md) pages.

### Platform matrix

CI runs the unit, property and skill suites, with the live Windows Job Object and
AppContainer tests, on these platforms:

| Platform | Environments | Python |
| --- | --- | --- |
| Ubuntu, x64 and arm64 | 22.04, 24.04, 26.04 hosted runners | 3.12 to 3.15 |
| Windows, x64 and arm64 | Server 2022, Server 2025, Windows 11 arm64 hosted runners | 3.12 to 3.15 |
| macOS, arm64 and Intel | 15 and 26 hosted runners | 3.12 to 3.15 |
| Other Linux distributions | Debian, Fedora, AlmaLinux, Rocky Linux, Amazon Linux, openSUSE Leap and Tumbleweed, Arch Linux, Alpine (musl), Void Linux (musl) containers as an unprivileged user | 3.12 and 3.14 |
| BSD | FreeBSD, OpenBSD and NetBSD virtual machines | the system Python package |

macOS and the BSDs run the core runtime, artifact stores and backend contracts.
They have no native backend; use a container or Kubernetes backend there. The
[tested platforms](../reference/tested-platforms.md) page lists every CI job,
including the live backend jobs.

## Live suites

Each live fixture in `tests/conftest.py` reads an explicit environment profile.
If no variable of a profile is set, its tests skip. If only some are set, they
fail. Flag variables accept only `0` or `1`. Tests create resources with
unique names and labels and remove only what they created.

Use disposable engines, clusters and buckets. Resource tests run bounded memory,
CPU and process pressure inside the sandbox.

### Podman

| Variable | Purpose |
| --- | --- |
| `ISOLATTICE_TEST_PODMAN_CONNECTION`, `ISOLATTICE_TEST_PODMAN_IMAGE` | Default profile: a rootless connection and an already-local image |
| `ISOLATTICE_TEST_PODMAN_MACHINE` | Optional. Podman machine name; the suite checks recorded host PIDs from inside that machine |
| `ISOLATTICE_TEST_PODMAN_RESOURCE_CONNECTION`, `ISOLATTICE_TEST_PODMAN_RESOURCE_IMAGE` | Resource profile: a connection with delegated cgroup v2 memory, CPU and pids controllers |
| `ISOLATTICE_TEST_PODMAN_RESOURCE_ROOTFUL` | Optional, `0` (default) or `1` when the resource connection is rootful |
| `ISOLATTICE_TEST_PODMAN_ROOTFUL_CONNECTION`, `ISOLATTICE_TEST_PODMAN_ROOTFUL_IMAGE` | Rootful profile: pod instance budgets, CRIU checkpoints and container snapshots |

```powershell
$env:ISOLATTICE_TEST_PODMAN_CONNECTION = "ROOTLESS_CONNECTION"
$env:ISOLATTICE_TEST_PODMAN_IMAGE = "LOCAL_IMAGE"
$env:ISOLATTICE_TEST_PODMAN_RESOURCE_CONNECTION = "DELEGATED_CONNECTION"
$env:ISOLATTICE_TEST_PODMAN_RESOURCE_IMAGE = "LOCAL_IMAGE"
$env:ISOLATTICE_TEST_PODMAN_ROOTFUL_CONNECTION = "ROOTFUL_CONNECTION"
$env:ISOLATTICE_TEST_PODMAN_ROOTFUL_IMAGE = "LOCAL_IMAGE"
uv run pytest -m podman
```

Use a trusted image containing `/bin/sh` and BusyBox utilities (for example
Alpine). The `podman` marker covers `test_podman_backend.py`,
`test_podman_resources.py`, `test_podman_instance.py` and
`test_podman_container_snapshot.py`. Configure all three profiles to run every
case, including every Podman example script. On a WSL-based Podman machine,
[scripts/podman-machine/Install-IsolatticeRootless.ps1](https://github.com/smuniharish/isolattice/blob/main/scripts/podman-machine/Install-IsolatticeRootless.ps1)
installs a rootless service with delegated controllers and registers a
`<machine>-isolattice` connection to use as the resource profile.

### Docker API

| Variable | Purpose |
| --- | --- |
| `ISOLATTICE_TEST_DOCKER_HOST`, `ISOLATTICE_TEST_DOCKER_IMAGE` | Required. Docker API endpoint and an already-local image |
| `ISOLATTICE_TEST_DOCKER_EXECUTABLE` | Optional. Docker CLI path |
| `ISOLATTICE_TEST_DOCKER_ROOTFUL` | Optional, `0` (default) or `1` for a rootful endpoint |

```powershell
uv run pytest -m docker
```

The maintainer runs this suite against Podman's Docker-compatible API.

### Kubernetes

| Variable | Purpose |
| --- | --- |
| `ISOLATTICE_TEST_KUBERNETES_KUBECONFIG`, `_CONTEXT`, `_NAMESPACE`, `_IMAGE` | Required. Explicit kubeconfig, context, an existing dedicated namespace and an image present on the node |
| `ISOLATTICE_TEST_KUBERNETES_EXECUTABLE` | Optional. `kubectl` path |

```powershell
uv run pytest -m kubernetes
```

See the [Kubernetes guide](../guides/kubernetes-integration.md) for a local kind
cluster.

### S3 artifact store

| Variable | Purpose |
| --- | --- |
| `ISOLATTICE_TEST_S3_ENDPOINT`, `_BUCKET`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | Required. S3-compatible endpoint, an existing bucket and its credentials |

```powershell
uv run pytest -m s3
```

[tests/integration/test_s3_store.py](https://github.com/smuniharish/isolattice/blob/main/tests/integration/test_s3_store.py)
runs the `check_artifact_store` conformance check, a multipart round trip, and a
wrong-credentials check that must fail closed without leaking the secret. Its
tamper-detection test round-trips a Podman workspace snapshot, so it also needs
the default Podman profile. Every test writes below a unique key prefix and
deletes it afterwards. The maintainer verified the suite against RustFS 1.0.4
running in rootless Podman.

### Linux namespaces

The suite runs only on Linux and needs unprivileged user namespaces, Landlock and
a delegated cgroup v2 subtree in `ISOLATTICE_TEST_LINUX_CGROUP_ROOT`. On WSL 2 or
another systemd host,
[scripts/linux/isolattice-delegated-run.sh](https://github.com/smuniharish/isolattice/blob/main/scripts/linux/isolattice-delegated-run.sh)
starts a command in a delegated user scope and exports
`ISOLATTICE_LINUX_CGROUP_ROOT`:

```sh
scripts/linux/isolattice-delegated-run.sh sh -c \
  'ISOLATTICE_TEST_LINUX_CGROUP_ROOT=$ISOLATTICE_LINUX_CGROUP_ROOT uv run pytest tests/integration/test_linux_backend.py'
```

### Windows

`test_windows_backend.py` (Job Object) and `test_windows_appcontainer.py` run
automatically on Windows, with no variables:

```powershell
uv run pytest tests/integration/test_windows_backend.py tests/integration/test_windows_appcontainer.py
```

Workloads use the base interpreter (`sys.base_prefix\python.exe`), because a venv
redirector adds an extra process. AppContainer interpreter tests skip when that
interpreter is not readable by restricted application packages, for example when
it is installed under the user profile. The suite asserts that no
`isolattice.*` AppContainer profile is left behind.

## Checking live skips

A live run that silently skips proves nothing.
[scripts/check_live_skips.py](https://github.com/smuniharish/isolattice/blob/main/scripts/check_live_skips.py)
reads a JUnit report and fails when no test executed or when any test skipped
that is not explicitly allowed with `--allow TEST_NAME` (parametrized IDs
included) or `--allow-reason TEXT` (skips whose message contains `TEXT`, for a
documented host limitation that affects several tests):

```powershell
uv run pytest -m podman --junitxml=live.xml
uv run python scripts/check_live_skips.py live.xml
```

CI runs every live job this way and also checks for leftover owned resources.

## Coverage gate

`pyproject.toml` measures branch coverage of the `isolattice` package with
`fail_under = 87`. `[tool.coverage.paths]` maps source checkouts and installed
`site-packages` copies on Linux and Windows to one tree, so data from unit and
live runs on different machines can be combined:

```powershell
uv run pytest tests/unit --cov --cov-report=term-missing
```

A unit-only run on one platform stays below the gate, because native helpers,
engine adapters and platform branches run only in live suites. The gate applies
to the combined report. Unit, Windows, Podman and S3, Docker Engine, kind and
Linux runs together reach 87.88%: the runtime, termination, storage and models
are at 98–100%, and the misses are backend branches for engine faults (a
daemon dropping mid-call, malformed inspection output, failed `kill` or
`rm`). Every new branch needs a unit or live test that reaches it, and the
floor only rises.
