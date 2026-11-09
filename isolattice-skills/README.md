# isolattice Agent Skills

This directory distributes two Agent Skills that teach coding agents to use
the `isolattice` package correctly. They are not part of the Python package
and add no runtime behavior.

| Skill | Use it to |
| --- | --- |
| [`isolattice`](skills/isolattice/SKILL.md) | Run commands and generated code through isolattice: choose a backend, write a `SandboxPolicy`, stream output, handle timeouts, cancellation and verified termination, take snapshots and checkpoints, and troubleshoot refusals |
| [`isolattice-extend`](skills/isolattice-extend/SKILL.md) | Extend isolattice: write an `ArtifactStore`, `EventSink`, `CommandRunner` or a new `Backend`, and re-expose those extension points from a downstream package |

Install both with the [skills CLI](https://github.com/vercel-labs/skills),
which finds them through [`skills.sh.json`](../skills.sh.json):

```bash
npx skills add smuniharish/isolattice                           # both
npx skills add smuniharish/isolattice --skill isolattice        # one
```

Other ways to install are in the
[documentation](https://isolattice.readthedocs.io/en/latest/agent-skills/install/).

## Layout

Each skill follows the [Agent Skills specification](https://agentskills.io/specification):

```text
skills/<skill>/
├── SKILL.md      # frontmatter, workflow, rules, gotchas, links to every file
├── references/   # focused documents, one level deep, read only when a task needs them
├── scripts/      # offline checks with --help, --json and meaningful exit codes
├── assets/       # templates the agent copies into the project, each with its tests
└── evals/        # evals.json, trigger_queries.json and the input files they use
```

| Skill | Scripts | Assets |
| --- | --- | --- |
| `isolattice` | `verify_setup.py`: offline environment, policy and local-store check (`--s3` requires the S3 extra) | `policy_template.py`, `test_policy.py` |
| `isolattice-extend` | `check_store.py`: runs `check_artifact_store` against any store factory | `store_template.py`, `test_store_contract.py`, `event_sink_template.py`, `test_event_sink.py`, `runner_template.py`, `test_runner.py` |

Every Agent Skills host reads the same directories, so there is no separate
copy for Claude Code, Codex, Cursor or GitHub Copilot.

## Format

- `name` matches the directory. The `description` starts with "Use this skill
  when", names near misses and hands over to the other skill where they meet.
- `license`, `compatibility` and `metadata.version` are set. The version
  equals the `isolattice` release the skill describes.
- `SKILL.md` stays short and links every reference with the task that needs
  it. Scripts and assets are referenced by paths relative to the skill
  directory.

## Maintaining the skills

When the public API or documented behavior changes:

1. Update the affected skill, and `metadata.version` in both skills with each
   release.
2. Check every changed snippet and claim against the code, tests or
   documentation. Leave out anything that is not implemented.
3. Add or update an eval when the change affects what an agent should do.
4. Run the checks in [`validation/README.md`](validation/README.md).

The skills are covered by the repository's Apache License 2.0; see
[LICENSE](../LICENSE).
