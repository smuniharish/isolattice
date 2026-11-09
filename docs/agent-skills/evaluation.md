# Evaluation

A skill is only useful when an agent picks it up for the right task and then
gets that task right. Isolattice tests both, following the Agent Skills
guidance on
[evaluating skills](https://agentskills.io/skill-creation/evaluating-skills).

## Checked on every change

`tests/skill` runs offline with the rest of the test suite. For both skills,
it checks:

- **Format.** The `skills-ref` reference validator accepts the frontmatter.
  `name` matches the directory, the description starts with "Use this skill
  when", and `metadata.version` equals the Isolattice release.
- **Progressive disclosure.** `SKILL.md` stays under 500 lines and references
  are one level deep. Every reference is linked from `SKILL.md`, and every
  relative link resolves.
- **Bundled files.** `SKILL.md` mentions every script and asset, so the agent
  can find it.
- **Scripts.**
    - `verify_setup.py` passes, prints valid JSON with `--json`, and requires
      the same dependency ranges as the package.
    - `check_store.py` passes a correct store and names the rule a broken
      store violates. It exits with a distinct code when the store factory
      can't be loaded.
- **Templates.** The policy, store, event-sink and runner templates pass their
  own tests.
- **Discovery.** The skills CLI finds both skills through `skills.sh.json`.

CI runs the setup check and the store contract again against the built wheel,
outside the development environment.

## Evaluated against real tasks

Each skill has an `evals/` folder:

| File | Contents |
| --- | --- |
| `evals.json` | Realistic tasks, each with input files, the expected result, and assertions a grader can verify |
| `trigger_queries.json` | 20 user requests: 10 that should load the skill and 10 near misses that shouldn't, split into training and validation sets |
| `files/` | The projects the tasks start from, such as a report runner that uses `subprocess` with `shell=True` |

The tasks cover what agents get wrong without the skill:

| Skill | Tasks |
| --- | --- |
| `isolattice` | Move a `shell=True` report runner onto rootless Podman with no network, a memory limit and a timeout; fix `SandboxCapabilityError` with `WindowsJobBackend` and choose AppContainer for file isolation; stop treating `TerminationVerificationError` as "stopped"; snapshot and restore a workspace through S3 |
| `isolattice-extend` | Turn a hand-written audit log into a non-blocking `EventSink`; write a PostgreSQL `ArtifactStore` with contract tests; refuse a runner that strips `--read-only`; design a wrapper package that passes extension points through |

The near-miss trigger queries keep the two skills apart: many near misses for
`isolattice-extend` are tasks for `isolattice`, and the reverse. A test checks
that no query is meant to load both.

To evaluate a change to a skill, run each task with and without the skill in a
fresh agent session. Grade the outputs against the assertions, and compare
pass rates, time and tokens. The
[validation guide](https://github.com/smuniharish/isolattice/blob/main/isolattice-skills/validation/README.md)
describes the workflow. No graded evaluation run has been recorded yet.

## Reviewed against the source

Automated checks can't tell whether guidance is correct. Each snippet and
claim in a skill is reviewed against the code, tests and documentation it
describes, and anything without an implementation, such as the macOS and
remote backends, is left out.
