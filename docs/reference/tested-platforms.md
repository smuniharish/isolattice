# Tested platforms

Every push and pull request runs the
[CI workflow](https://github.com/smuniharish/isolattice/blob/main/.github/workflows/ci.yml).
GitHub starts all of its jobs at once and runs them in parallel, up to the account's
concurrency limit. This page lists what each job verifies. A platform appears here
only when a job checks it on every run.

## Core suite

The core suite covers the unit, property-based (Hypothesis), contract and skill
tests: policy validation, capability discovery, the lifecycle state machine, the
termination engine, bounded output, the artifact stores, snapshot manifests and
every backend's command and API contract. Warnings fail the run.

| Platform | Images | Python |
| --- | --- | --- |
| Ubuntu, x64 | 22.04, 24.04, 26.04 | 3.12, 3.13, 3.14, 3.15 |
| Ubuntu, arm64 | 22.04, 24.04, 26.04 | 3.12, 3.13, 3.14, 3.15 |
| Windows, x64 | Server 2022, Server 2025 | 3.12, 3.13, 3.14, 3.15 |
| Windows, arm64 | Windows 11 | 3.12, 3.13, 3.14, 3.15 |
| macOS, Apple silicon | 15, 26 | 3.12, 3.13, 3.14, 3.15 |
| macOS, Intel | 15, 26 | 3.12, 3.13, 3.14, 3.15 |
| glibc distributions | Debian stable, Fedora, AlmaLinux, Rocky Linux, Amazon Linux, openSUSE Leap, openSUSE Tumbleweed, Arch Linux | 3.12, 3.14 |
| musl distributions | Alpine, Void Linux | 3.12, 3.14 |
| BSD | FreeBSD, OpenBSD, NetBSD virtual machines | The system Python package |

The distribution containers run as an unprivileged user, so permission checks behave
as they do for real callers. The BSD jobs install the built wheel, not the source
tree. On Windows images the Job Object and AppContainer tests also run, because they
need no extra setup.

## Live backends

Live jobs provision a real engine, cluster or kernel feature and run the backend's
integration tests and example scripts against it. Each job fails if a configured
live test skips or if nothing runs. It also fails if a resource that Isolattice
labelled as its own is left behind.

| Job | Runner | What runs |
| --- | --- | --- |
| Live Podman | Ubuntu, Python 3.12 and 3.14 | Rootless engine, rootful engine with runc, delegated memory, CPU and pids limits, pod instance budgets, CRIU checkpoints and restore, whole-container snapshots, and the S3 artifact store against a disposable object store |
| Live Podman over SSH | Ubuntu, Python 3.14 | The Podman suite through an `ssh://` connection with a dedicated key and pinned host key, as with a remote engine |
| Live Docker Engine | Ubuntu | `DockerBackend` against the runner's Docker Engine |
| Live Kubernetes | Ubuntu | `KubernetesBackend` against a single-node kind cluster |
| Live Linux namespaces | Ubuntu 24.04 | `LinuxNamespaceBackend` in a delegated systemd scope: namespaces, cgroup v2 limits, seccomp and Landlock |
| Live Windows | Windows Server 2025, Python 3.12 and 3.14 | `WindowsJobBackend`, the ACL grants, and every `WindowsAppContainerBackend` case, including the check for leftover AppContainer profiles |

The SSH job covers the default Podman profile. Checkpoints, resource limits and S3
run in the main Podman job.

## Packaging and documentation

| Job | What runs |
| --- | --- |
| Lint | Ruff, the Ruff formatter, and Pyrefly for Linux, Windows and macOS, plus repository checks |
| Lowest dependencies | The full suite on Python 3.12 with the lowest supported version of each dependency |
| Package | Builds the sdist and wheel, runs `twine check --strict`, and smoke-tests the wheel with and without the S3 extra |
| Documentation | A strict Zensical build, a link check and a diagram check |
| Markdown | `markdownlint-cli2` |
| Coverage | Combines branch coverage from the core and live jobs |

## Verified locally

Maintainers also run the live suites on Windows 11 with Podman Desktop (a Podman
machine reached over SSH, rootless and rootful), Podman's Docker-compatible API,
a kind cluster and WSL 2 with a delegated systemd scope.

## Not covered

- **Native macOS sandboxing.** There is no macOS backend, so macOS jobs run only the
  core suite. Hosted macOS runners can't start a Podman machine because they don't
  support nested virtualization. On a Mac, use a container backend with a local
  Podman machine or a remote engine.
- **Restricted low-integrity tokens on hosted Windows.** Hosted runners either
  disable User Account Control or can't initialize a low-integrity process. The
  backend reports the token as unavailable and refuses the policy; it doesn't fall
  back.
- **The Isolattice remote service.** The
  [remote backend protocol](../architecture/15-remote-backend-protocol.md) is
  proposed. Remote engines through Podman, Docker or Kubernetes connections are
  supported and tested.

## Related

- [Testing](../development/testing.md)
- [Platform limitations](../security/platform-limitations.md)
- [Capabilities](capabilities.md)
