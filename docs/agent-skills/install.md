# Install in your agent

Each skill is one directory with `SKILL.md` at its root: `isolattice` and
`isolattice-extend`. Every agent that supports the
[Agent Skills format](https://agentskills.io/specification) reads the same
directories, so there is no separate copy per agent.

## With the skills CLI

The [skills CLI](https://github.com/vercel-labs/skills) finds both skills in
the repository, detects the agents in your project, and installs the skills
into each agent you choose. It needs Node.js 22.20 or later.

```bash
npx skills add smuniharish/isolattice                            # choose interactively
npx skills add smuniharish/isolattice --skill isolattice         # only the core skill
npx skills add smuniharish/isolattice --skill isolattice-extend  # only the extension skill
```

| Option | Effect |
| --- | --- |
| `-g` | Install for every project instead of the current one |
| `-a claude-code` | Install for one agent; see the CLI's [agent list](https://github.com/vercel-labs/skills/blob/main/docs/agents.md) |

## Manually

Copy each complete skill directory, including `SKILL.md`, `references/`,
`scripts/` and `assets/`, into one of the locations for your agent. The
[`isolattice`](https://github.com/smuniharish/isolattice/tree/main/isolattice-skills/skills/isolattice)
and
[`isolattice-extend`](https://github.com/smuniharish/isolattice/tree/main/isolattice-skills/skills/isolattice-extend)
directories are in the Isolattice repository. Their `evals/` folders only test
the skills, so you can leave them out.

=== "Claude Code"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.claude/skills/<skill>/` |
    | All local projects | `~/.claude/skills/<skill>/` |

    Restart Claude Code. It selects a skill when the task matches, or invoke
    it with `/isolattice` or `/isolattice-extend`. See
    [Claude Code Skills](https://code.claude.com/docs/en/skills).

=== "Codex"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` |

    Codex detects new skills automatically; restart it if a skill doesn't
    appear. Invoke one with `$isolattice` or `$isolattice-extend`, or list
    skills with `/skills`.

=== "Cursor"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/` or `.cursor/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` or `~/.cursor/skills/<skill>/` |

    Restart Cursor. In Agent chat, type `/` and select the skill, or let
    Cursor activate it from its description. See
    [Cursor Agent Skills](https://cursor.com/docs/skills).

=== "GitHub Copilot"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/`, `.github/skills/<skill>/` or `.claude/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` or `~/.copilot/skills/<skill>/` |

    In Copilot CLI, start a new session or run `/skills reload`, then check
    with `/skills info isolattice`. Invoke a skill with `/isolattice` or
    `/isolattice-extend`. See
    [Adding agent skills for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

    The repository's own `.github/skills` directory holds contributor skills
    for developing Isolattice itself; see
    [Contributor skills](../development/agent-skills.md).

=== "Other agents"

    Copy the skill folders into the skills directory your agent documents.
    The skills use only standard `SKILL.md` frontmatter, so they need no
    agent-specific adapter.

## Verify

1. Each installed directory is named after its skill (`isolattice` or
   `isolattice-extend`) and contains `SKILL.md`.
2. Your agent lists the skills, if it has a skill list.
3. From your project's Python environment, the setup check passes:

    ```bash
    python <skills directory>/isolattice/scripts/verify_setup.py
    ```

    ```text
    PASS  Python 3.14.10 (3.12 or newer required)
    PASS  isolattice 0.1.1 (0.1 expected by this skill)
    INFO  aiobotocore 3.10.0 (>=3.10.0,<4 for the s3 extra)
    INFO  engine clients on PATH: podman, kubectl; platform win32
    PASS  policies default to no network and a read-only root, and fail closed
    PASS  LocalArtifactStore passes check_artifact_store
    All checks passed.
    ```

    Add `--s3` to fail when the `isolattice[s3]` extra is missing, or `--json`
    to print one JSON object. The check runs offline and starts no workload.

4. If you installed `isolattice-extend`, its store template passes the
   artifact-store contract:

    ```bash
    cd <skills directory>/isolattice-extend
    ISOLATTICE_SQLITE_DATABASE=/tmp/isolattice-skill.db \
        python scripts/check_store.py store_template:from_environment --path assets
    ```

    ```text
    PASS  SQLiteArtifactStore keeps the ArtifactStore contract.
    ```

## Update

Run `npx skills update`, or replace the installed folders with the latest
directories from the repository, then restart or reload your agent. Each
skill's `metadata.version` matches the Isolattice release it describes.

## Optional upstream skills

Installing a Python package or an Isolattice extra does not install an agent
skill. Review the upstream source and your agent's permissions before installing
third-party instructions.

### Amazon S3

For applications using `isolattice[s3]` with Amazon S3, AWS's
[Agent Toolkit skill catalog](https://github.com/aws/agent-toolkit-for-aws/tree/main/skills)
offers `aws-storage`, `securing-s3-buckets`, and `troubleshooting-s3-files`.
With the same Node.js and agent prerequisites as the skills CLI above, select
a skill explicitly, for example:

```bash
npx skills add aws/agent-toolkit-for-aws/skills --skill securing-s3-buckets
```

Ask your agent to review an S3 bucket policy or help diagnose object access.
The skills do not configure Isolattice, supply credentials, or establish that a
third-party S3-compatible service supports the same AWS features. Configure the
store separately using the [storage API](../api/storage.md). Use read-only
or appropriately scoped access when investigating a live account.

[aiobotocore-bot](https://github.com/aio-libs/aiobotocore/tree/main/plugins/aiobotocore-bot)
is a separate Claude Code plugin for **contributing to aiobotocore**, not for
using Isolattice's S3 store. Its upstream instructions describe `review-pr`,
`port-tests`, `check-override-drift`, and related maintenance skills.

### Contributor tools

- The [Skills CLI](https://github.com/vercel-labs/skills) offers `find-skills`.
  Install it with `npx skills add vercel-labs/skills --skill find-skills` to
  help discover other skills; `skills-ref` is a format validator, not a catalog.
- Pyrefly's
  [add-shape-types-to-torch-model](https://github.com/facebook/pyrefly/tree/main/tensor-shapes/skills/add-shape-types-to-torch-model)
  is for PyTorch projects using Pyrefly's tensor shape annotations. Copy the
  complete directory into your agent's skills location as above. It is not
  needed for Isolattice's type checks.
- uv, Ruff and Hypothesis have repository-contributor skills; these are not
  part of Isolattice or those Python distributions. No installable end-user
  skill was verified for the other declared Python tools or for the
  `podman`, `docker`, and `kubectl` clients reviewed.
