# Validating the isolattice skills

The `isolattice` and `isolattice-extend` skills are validated in three ways:
automated checks on every change, evaluations against real tasks, and a
review of every claim against its source.

## Automated checks

Run from the repository root:

```bash
uv run pytest tests/skill
uvx --from skills-ref agentskills validate isolattice-skills/skills/isolattice
uvx --from skills-ref agentskills validate isolattice-skills/skills/isolattice-extend
```

`tests/skill` runs offline in continuous integration. For both skills it
checks that:

- the frontmatter passes `skills-ref`. `name` matches the directory, the
  description starts with "Use this skill when", `license` is `Apache-2.0`
  and `metadata.version` equals the package version;
- `SKILL.md` has fewer than 500 lines and links every reference;
  `references/` is one level deep and every relative link resolves;
- `SKILL.md` mentions every bundled script and asset;
- `evals/evals.json` and `evals/trigger_queries.json` are well formed. The
  trigger queries are balanced, split into training and validation sets, and
  no query triggers both skills;
- the skills CLI discovers both skills through `skills.sh.json`.

It also runs the bundled code:

- `isolattice/scripts/verify_setup.py` passes with warnings treated as
  errors, prints valid JSON with `--json`, and checks the same `s3` extra range
  as `pyproject.toml`;
- `isolattice/assets/test_policy.py` passes;
- the `isolattice-extend` store, event-sink and runner templates pass their
  tests;
- `isolattice-extend/scripts/check_store.py` handles three cases:
    - it passes a correct store;
    - it names the promise a broken store violates;
    - it exits with code 2 for a factory it can't load.

CI also runs `verify_setup.py` and `check_store.py` against the built wheel.

## Evaluations

Each skill has an `evals/` folder, in the format described in
[Evaluating skills](https://agentskills.io/skill-creation/evaluating-skills):

- `evals.json`: realistic tasks with input files, an expected result and
  assertions a grader can verify.
- `trigger_queries.json`: 10 requests that should load the skill and 10 near
  misses that shouldn't, split 12/8 into training and validation.

To evaluate a change:

1. Copy each task's input files into fresh workspaces, one with the skill and
   one without.
2. Run each task in a new agent session. The session without the skill can
   read the installed package but not the skill, the docs or the
   repository's source.
3. Grade every assertion as passed or failed, with the evidence. Record time
   and tool calls.
4. Compare the two variants. Change the skill where the agent went wrong with
   it, or where it did no better than without it, then evaluate again.

Keep workspaces and benchmark files out of the repository.

## Source review

Automated checks can't tell whether guidance is correct. For every change,
check each snippet and claim against its source:

| Claim area | Source of truth |
| --- | --- |
| Public imports, signatures and the package version | [`src/isolattice/__init__.py`](../../src/isolattice/__init__.py), [`pyproject.toml`](../../pyproject.toml) and the [API reference](../../docs/api/index.md) |
| Policies, requests and results | [`src/isolattice/models.py`](../../src/isolattice/models.py) |
| Sandbox, instance and execution lifecycle | [`src/isolattice/runtime.py`](../../src/isolattice/runtime.py) |
| Backend capabilities and refusals | [`src/isolattice/backends/`](../../src/isolattice/backends/) and the [backend pages](../../docs/backends/) |
| Extension contracts | [`src/isolattice/backend.py`](../../src/isolattice/backend.py), [`storage.py`](../../src/isolattice/storage.py), [`events.py`](../../src/isolattice/events.py), [`commands.py`](../../src/isolattice/commands.py) and [`testing.py`](../../src/isolattice/testing.py) |
| Security limits | [security guarantees](../../docs/security/guarantees.md) |
| Working patterns | [`examples/`](../../examples/) and [`tests/`](../../tests/) |

If a behavior has no implementation, test or documentation, leave it out of
the skill rather than infer it.

## Routing matrix

Check that the descriptions send each task to the right skill:

| Task | Skill | Route | Avoids |
| --- | --- | --- | --- |
| "Run generated code without network access." | `isolattice` | A strict `SandboxPolicy` with required capabilities and a prepared backend. | Building shell strings from untrusted input. |
| "Why does my backend refuse this policy?" | `isolattice` | `discover()`, `unmet_requirements()` and the troubleshooting reference. | Removing a required capability to silence the error. |
| "Snapshot the workspace and restore it later." | `isolattice` | `Sandbox(artifact_store=...)`, `workspace=True`, `snapshot()` and `create(workspace_snapshot=...)`. | Claiming snapshots on backends that refuse them. |
| "close() raised TerminationVerificationError." | `isolattice` | Treat it as unverified cleanup and alert an operator. | Reporting the run as stopped. |
| "Store snapshots in PostgreSQL." | `isolattice-extend` | The store template, its tests and `check_store.py`. | Skipping `max_bytes` or overwrite checks. |
| "Export lifecycle events to our telemetry." | `isolattice-extend` | The buffered event-sink template. | Blocking I/O inside `emit`. |
| "Strip an engine flag that breaks our image." | `isolattice-extend` | Explain why a runner never changes argv; change the policy instead. | Weakening enforcement silently. |
| "Write a Dockerfile for our web app." | Neither | Not this package's purpose. | Inventing capabilities. |
