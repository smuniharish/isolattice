# Agent Skills

Isolattice publishes two [Agent Skills](https://agentskills.io). They give a
coding agent the instructions, references, scripts, templates and tests it
needs to use Isolattice correctly. They work in Claude Code, Codex, Cursor,
GitHub Copilot and any other agent that reads the open Agent Skills format.

<div class="il-agentic" markdown>

## Agentic installation

Install the skills so your coding agent knows how to:

- move `subprocess` calls and generated-code runners onto an Isolattice
  backend;
- write a `SandboxPolicy` whose required guarantees fail closed;
- handle timeouts, cancellation and unverified termination correctly;
- extend Isolattice with your own artifact store, event sink, command runner
  or backend.

=== "Ask your coding agent"

    Give your coding agent the repository and let it install the skills it
    needs.

    ```text title="Agent instruction"
    Install the Agent Skills from https://github.com/smuniharish/isolattice
    (isolattice-skills/skills/isolattice and
    isolattice-skills/skills/isolattice-extend) into this project, run the
    isolattice skill's scripts/verify_setup.py, then move this application's
    command execution onto isolattice, following the skill's workflow and
    rules.
    ```

=== "skills CLI"

    The [skills CLI](https://github.com/vercel-labs/skills) finds both skills
    in the repository and installs them for the agents in your project. It
    needs Node.js 22.20 or later.

    ```bash
    npx skills add smuniharish/isolattice
    ```

    To install a single skill, add `--skill isolattice` or
    `--skill isolattice-extend`. Add `-g` to install it for every project.

=== "Manual installation"

    Copy the skill directories into your agent's skills directory, for
    example:

    ```bash
    git clone --depth 1 https://github.com/smuniharish/isolattice.git
    cp -r isolattice/isolattice-skills/skills/isolattice .agents/skills/isolattice
    cp -r isolattice/isolattice-skills/skills/isolattice-extend .agents/skills/isolattice-extend
    ```

    [Install in your agent](install.md) lists the directory each agent reads.

</div>

## The skills

<div class="grid cards" markdown>

-   :material-play-circle-outline:{ .lg .middle } __[isolattice](isolattice.md)__

    ---

    Runs commands, scripts and generated code through Isolattice. Covers
    choosing a backend, writing a policy, streaming output, timeouts,
    cancellation, verified termination, snapshots and checkpoints, and
    troubleshooting capability errors.

    Includes an offline setup check and a policy template with its tests.

-   :material-puzzle-outline:{ .lg .middle } __[isolattice-extend](isolattice-extend.md)__

    ---

    Extends Isolattice. Use it to write an `ArtifactStore`, `EventSink`,
    `CommandRunner` or `Backend`, or to re-expose these extension points from
    your own package.

    Includes templates with their tests and an artifact-store contract checker.

</div>

Each skill tells the agent when to use the other one, so you can install both
and the agent picks the right one. If your application only runs workloads,
`isolattice` alone is enough.

## Use them

Once installed, describe the task. The agent loads a skill when the task
matches it:

| You ask | Skill |
| --- | --- |
| "Run the uploaded report scripts with no network and 512 MiB of memory." | `isolattice` |
| "Why do I get `SandboxCapabilityError` with `WindowsJobBackend`?" | `isolattice` |
| "Cancel a run from our API without hiding a failed cleanup." | `isolattice` |
| "Snapshot the workspace after each step and restore it later." | `isolattice` |
| "Store Isolattice snapshots in PostgreSQL and prove the store is correct." | `isolattice-extend` |
| "Send every lifecycle event to our audit log without blocking." | `isolattice-extend` |
| "Let users of our wrapper package bring their own store and sink." | `isolattice-extend` |

You can also invoke a skill by name: `/isolattice` or `/isolattice-extend` in
Claude Code, Cursor and GitHub Copilot, and `$isolattice` or
`$isolattice-extend` in Codex.

Both skills teach the agent the rules that keep an integration correct:

- never claim a backend is safe for hostile code;
- never weaken a required policy, or fall back to `subprocess`, to make a run
  succeed;
- treat an unknown termination state as a failure, not a success;
- never put workload output, environment values or credentials in events or
  logs.

## Next steps

<div class="grid cards" markdown>

-   :material-download:{ .lg .middle } __[Install in your agent](install.md)__

    ---

    Where each agent looks for skills, and how to verify and update an
    installation.

-   :material-check-decagram-outline:{ .lg .middle } __[Evaluation](evaluation.md)__

    ---

    How the skills are tested and evaluated against real tasks.

</div>
