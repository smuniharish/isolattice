# Examples

The `examples/` directory holds two things: one README per feature category, and
runnable scripts for features that are implemented. Every runnable
script uses a built-in backend with benign workloads; none demonstrates safe
execution of hostile or untrusted code.

## Layout

```text
examples/
  README.md                     index of scripts and categories
  templates/example.md          template for a new category README
  01-getting-started/
    README.md                   category README (front matter + sections)
    podman_hello.py             runnable script
  ...
  19-platform-specific/
    README.md
```

There is exactly one directory per category, numbered `01` to `19`
(`NN-category-name`). Each directory has a `README.md` that starts with front
matter and contains four sections:

```markdown
---
category: 1
status: runnable
---

# 01 Getting started

## Prerequisites

## Acceptance

## Cleanup

## Security
```

`category` must be an integer equal to the directory number. `status` is one of:

- `runnable`: the directory has scripts. The README names every script and also has
  `## Run` and `## Expected output` sections.
- `index`: the directory has no scripts of its own and points to scripts in other
  categories (security, process lifecycle, production workflows, platform specific).
- `proposed`: the feature is not implemented (remote execution, framework
  integrations). The directory has no scripts and the README says **Proposed.**

Runnable scripts sit next to the README and are named after the backend they
use: `podman_`, `docker_`, `kubernetes_`, `linux_` or `windows_`. They take explicit
command-line arguments, check their own expected results and cleanup, print a
short report and raise on failure.

## Runnable scripts

| Script | Backend | Arguments |
| --- | --- | --- |
| `01-getting-started/podman_hello.py` | Podman (rootless) | `--connection`, `--image` |
| `02-execution/podman_io.py` | Podman (rootless) | `--connection`, `--image` |
| `04-resource-management/podman_limits.py` | Podman with delegated cgroup v2 controllers | `--connection`, `--image`, optional `--allow-rootful` |
| `04-resource-management/podman_instance_budget.py` | Podman (rootful) | `--connection`, `--image`, `--allow-rootful` |
| `04-resource-management/windows_job_limits.py` | Windows Job Object | optional `--python` |
| `06-async-and-cancellation/podman_stream_cancel.py` | Podman (rootless) | `--connection`, `--image` |
| `07-capabilities-and-backends/podman_capabilities.py` | Podman (rootless) | `--connection`, `--image` |
| `07-capabilities-and-backends/windows_job_capabilities.py` | Windows Job Object | none |
| `08-native-backends/linux_namespaces.py` | Linux namespaces | optional `--cgroup-root` (default `ISOLATTICE_LINUX_CGROUP_ROOT`) |
| `08-native-backends/windows_appcontainer.py` | Windows AppContainer | none |
| `08-native-backends/windows_restricted_token.py` | Windows Job Object with restricted token | optional `--python` |
| `09-container-integrations/docker_isolated_run.py` | Docker API | `--host`, `--image`, optional `--executable`, `--allow-rootful` |
| `10-kubernetes/kubernetes_isolated_run.py` | Kubernetes | `--kubeconfig`, `--context`, `--namespace`, `--image`, optional `--executable` |
| `12-snapshots/podman_workspace_snapshot.py` | Podman (rootless) | `--connection`, `--image`, optional `--allow-rootful` |
| `12-snapshots/podman_container_snapshot.py` | Podman (rootless) | `--connection`, `--image`, optional `--allow-rootful` |
| `13-checkpoints/podman_checkpoint.py` | Podman (rootful, CRIU) | `--connection`, `--image`, `--allow-rootful` |
| `14-customization/podman_custom_extensions.py` | Podman (rootless) | `--connection`, `--image`, optional `--allow-rootful` |
| `15-observability/podman_event_sink.py` | Podman (rootless) | `--connection`, `--image`, optional `--allow-rootful` |
| `18-failure-scenarios/podman_failures.py` | Podman (rootless) | `--connection`, `--image` |

Run a script from the repository root:

```powershell
uv run python examples/01-getting-started/podman_hello.py --connection "CONNECTION" --image "IMAGE"
uv run python examples/07-capabilities-and-backends/windows_job_capabilities.py
```

Podman images must already be local and contain `/bin/sh`. Pass
`--allow-rootful` only for an explicitly chosen rootful engine; a missing
capability is an error, never a weaker fallback. The
[examples index](https://github.com/smuniharish/isolattice/blob/main/examples/README.md)
describes what each script verifies.

## How examples are validated

Three layers check examples, and they check different things.

### Repository validator

`uv run python scripts/validate_repository.py`
([source](https://github.com/smuniharish/isolattice/blob/main/scripts/validate_repository.py))
requires one `README.md` for every category 1 to 19, each with integer
`category` front matter that matches the directory number, a `status` that agrees
with the scripts in the directory, and the required sections. It also resolves
every relative link in the READMEs.

It checks the [feature coverage ledger](feature-coverage.md) against the
examples:

- every ledger entry points to an existing category README with the same
  category number;
- every script in a `partial` entry's `runnable_examples` exists under
  `examples/`, is a `.py` file, imports `isolattice`, and is named by one of
  the entry's test sources;
- every test source exists under `tests/`, imports `isolattice` and defines at
  least one `test_` function.

[tests/unit/test_repository_validation.py](https://github.com/smuniharish/isolattice/blob/main/tests/unit/test_repository_validation.py)
runs the validator on the repository and on broken fixtures: wrong or missing
front matter, a non-integer or mismatched category, a missing section, broken
links and anchors, a status that contradicts the scripts present, and a `partial` entry without real
test and example sources.

### Unit tests

[tests/unit/test_examples.py](https://github.com/smuniharish/isolattice/blob/main/tests/unit/test_examples.py)
requires every script under `examples/` to carry a live-tested backend prefix.
It also runs the streaming example against an in-memory sandbox to check its
readiness and end-of-stream handling.

### Live tests

Each backend's live suite runs its scripts as separate CLI processes with
`PYTHONPATH=src`, asserts exit code 0, empty stderr (Podman) and expected output
lines, and includes an inventory test that fails when a matching script has no
live test case:

| Scripts | Live suite | Inventory test |
| --- | --- | --- |
| `podman_*.py` | `test_podman_backend.py` (default, resource and rootful profiles) | `test_every_runnable_example_has_a_live_test_case` |
| `docker_*.py` | `test_docker_backend.py` | `test_every_docker_example_has_a_live_test_case` |
| `kubernetes_*.py` | `test_kubernetes_backend.py` | `test_every_kubernetes_example_has_a_live_test_case` |
| `linux_*.py` | `test_linux_backend.py` | `test_every_linux_example_has_a_live_test_case` |
| `windows_*.py` | `test_windows_backend.py` | `test_every_windows_example_has_a_live_test_case` |

A script therefore cannot be added without a live test that runs it. See
[testing](testing.md#live-suites) for the profiles each suite needs. A skipped
live run does not count as a passing example.

## Adding an example

1. Implement and test the feature first; examples document existing behavior.
2. Add the script to the matching category directory with a backend prefix.
3. Add a case to that backend's live example list with the expected output.
4. Update the category README and the
   [examples index](https://github.com/smuniharish/isolattice/blob/main/examples/README.md).
5. Add the script to `runnable_examples` in the ledger entry, make sure one of
   its `tests` names it, and run the validator.
