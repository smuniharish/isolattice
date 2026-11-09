# Contributing

This page covers setting up a development environment, the checks every change
must pass, the coding rules and what a pull request has to contain. The
repository-level
[CONTRIBUTING.md](https://github.com/smuniharish/isolattice/blob/main/CONTRIBUTING.md)
and [AGENTS.md](https://github.com/smuniharish/isolattice/blob/main/AGENTS.md)
apply to people and coding agents alike.

!!! warning "Evidence before claims"
    Built-in backends share the host or node kernel, and the Windows Job Object
    backend is not a security boundary. Do not describe a change as a security
    guarantee unless a live test on a real engine or operating system
    demonstrates it, and even then only for the tested configuration.

## Environment setup

Install [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer, then sync every
dependency group and extra from the lockfile:

```powershell
git clone https://github.com/smuniharish/isolattice.git
cd isolattice
uv sync --all-groups --all-extras
```

This installs the package in editable mode with the `test`, `lint`, `types` and
`docs` groups and the `s3` extra (aiobotocore). Pass `--python 3.14` (or another
version) to select a specific interpreter. Keep `uv.lock` in source control and
review advisories and licenses when upgrading dependencies; see the
[dependency evaluation](../architecture/19-dependency-evaluation.md).

### Node tooling

Two Node.js tools are used for documentation. Install them globally; the
repository has no `package.json`, lockfile or `node_modules`, and the repository
validator rejects them if they appear.

```powershell
npm install --global --ignore-scripts markdownlint-cli2@0.24.0
npm install --global --allow-scripts=puppeteer @mermaid-js/mermaid-cli@12.2.0
```

`markdownlint-cli2` lints Markdown. `@mermaid-js/mermaid-cli` renders the
diagram sources in `diagrams/` to PNG files in `docs/assets/diagrams/`; the
render script requires exactly version 12.2.0.

## Validation commands

Run these from the repository root before opening a pull request:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pyrefly check --python-platform linux
uv run pyrefly check --python-platform darwin
uv run pytest tests/unit
uv run python scripts/validate_repository.py
uv run zensical build --strict
uv run python scripts/check_site_links.py
node scripts/render-diagrams.mjs --check
markdownlint-cli2 "docs/**/*.md"
```

After editing a `diagrams/*.mmd` source, run `node scripts/render-diagrams.mjs`
to regenerate the PNG files, then `--check` to confirm they are current.

[Testing](testing.md) describes the skill suite, the live backend suites, their
environment variables and the coverage gate.

## Coding rules

- Target Python 3.12+ with complete type annotations. pyrefly must pass for the
  `win32`, `linux` and `darwin` platforms, because native backends contain
  platform-specific code paths.
- Execution and lifecycle APIs are async-only. Configuration and immutable
  metadata may be synchronous. Do not hold asyncio locks across unbounded I/O,
  and wrap blocking calls in owned, bounded adapters.
- Extension points are abstract base classes (`Backend`, `BackendSession`,
  `TerminationDriver`, `CommandRunner`, `ArtifactStore`, `EventSink`). Instances
  are passed explicitly; there is no hidden global registry or plugin discovery.
  See the [extension API](../api/extension.md).
- Required capabilities fail closed. Never fall back silently to a weaker
  backend or mechanism, never report enforcement from an operating-system name,
  a mock or a skipped test, and never treat an unknown termination state as
  success.
- Clean up by authoritative containment identity (container, cgroup, Job
  Object, Pod UID), not by direct-child exit or PID recursion. Keep original
  error causes and report cleanup failures instead of swallowing them.
- Keep streams and queues bounded. Never put workload output, environment values
  or secrets into events, logs or exception messages.
- Write Google-style docstrings; ruff enforces them with the `google`
  pydocstyle convention. Comment only code that needs clarification.
- Format and lint with ruff (line length 100).
- Test with pytest for examples and Hypothesis for properties.
- Prefer maintained libraries and operating-system primitives after a
  dependency and security evaluation. The core package has no runtime
  dependencies; optional integrations go in extras.

New subsystems need architecture review before implementation: describe the
responsibility, the dependencies and primitives used, the interfaces, the
security and failure cases, and the planned tests and examples. Record the
outcome in the
[architecture decision records](../architecture/22-architecture-decision-records.md).
The [architecture review](architecture-review.md) lists open risks and
proposed work.

## Pull request expectations

- Keep each pull request focused and preserve unrelated work.
- Deliver an implemented feature together with its public API, docstrings,
  documentation, unit tests, live tests where the feature enforces something,
  and a runnable example when it is user-facing.
- State which claims are documented, implemented, tested live, and not
  verified. Live-test evidence applies only to the configuration it ran on.
- Update the [feature coverage ledger](feature-coverage.md) and the
  [examples](examples.md) together with the code they describe.
- Do not add empty modules, placeholder runnable examples, or mocked tests
  presented as proof of isolation.
- Use the matching [contributor skill](agent-skills.md) for security-sensitive
  work.
- All validation commands above pass, and new code is covered by unit or live
  tests so the combined [coverage gate](testing.md#coverage-gate) never drops.

Report security issues privately as described in
[SECURITY.md](https://github.com/smuniharish/isolattice/blob/main/SECURITY.md).
Contributions are distributed under the
[Apache-2.0 license](https://github.com/smuniharish/isolattice/blob/main/LICENSE).
