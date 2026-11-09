# Contributor skills

Isolattice has two sets of [Agent Skills](https://agentskills.io/specification):

| Set | Location | Audience |
| --- | --- | --- |
| Contributor skills | `.github/skills/<name>/SKILL.md` | People and coding agents changing this repository |
| User skills | `isolattice-skills/skills/` (`isolattice`, `isolattice-extend`) | Developers using Isolattice in their own projects |

This page covers the contributor skills. The user skills are documented in
[Agent Skills](../agent-skills/index.md).

## Skills

Each skill holds a workflow, security constraints, links to the canonical
architecture documents and completion criteria. Consult the matching skill before
security-sensitive work.

| Skill | Use for |
| --- | --- |
| `isolattice-architecture` | Subsystem architecture, API boundaries, decision records, implementation gates |
| `python-framework-development` | Typed Python APIs, models, packaging, extension interfaces |
| `sandbox-backend-development` | Backend adapters, discovery, health, enforcement conformance |
| `async-runtime` | Async execution, bounded streaming, cancellation, shutdown ownership, event-loop races |
| `capability-model` | Capability discovery, evidence, required capabilities, backend matching |
| `process-containment` | Descendant containment, orphan prevention, boundary identity, PID reuse |
| `termination-engine` | Graceful and forced termination, retries, verification, timeouts, cleanup failure |
| `container-backend` | Docker, Podman, Podman Desktop and Kubernetes adapters |
| `linux-sandbox-security` | Namespaces, cgroup delegation, Landlock, seccomp, native launcher |
| `windows-sandbox-security` | Job Objects, AppContainer, restricted tokens, ACLs, suspended launch, brokers |
| `macos-sandbox-security` | Evaluating Seatbelt, App Sandbox and signed helpers (proposed backend) |
| `remote-backend` | Remote protocol, scheduling, authentication, leases, failover (proposed backend) |
| `snapshot-checkpoint` | Workspace and container snapshots, artifact storage, safe archives, CRIU |
| `security-review` | Reviewing guarantees, threat boundaries, secret handling, escape prevention, dependencies |
| `testing-and-validation` | Validator tests, backend conformance, live platform tests, CI evidence |
| `documentation-maintenance` | Documentation, API reference, comparisons, examples, skills, links, feature coverage |

## Related instructions

- [AGENTS.md](https://github.com/smuniharish/isolattice/blob/main/AGENTS.md):
  the contributor and coding-agent contract.
- [.github/copilot-instructions.md](https://github.com/smuniharish/isolattice/blob/main/.github/copilot-instructions.md):
  repository-wide instructions.
- Four scoped files in
  [.github/instructions](https://github.com/smuniharish/isolattice/blob/main/.github/instructions)
  apply by path: `python`, `security`, `testing` and `documentation`.

Skills are guidance. They do not replace tests, and an agent must not run a
skill's bundled scripts without explicit trust.

## Validation

`scripts/validate_repository.py` requires all 16 skills and checks each
`SKILL.md`: the `name` matches its directory and the standard naming rules
(lowercase, hyphen-separated, at most 64 characters), the `description` is 1 to
1,024 characters and says when to use the skill, and the body has a meaningful
workflow (at least 70 words). It also checks links and rejects known
contradictory security claims. Whether a specific agent client discovers and
activates the skills is not tested.

Keep one canonical copy of each skill in `.github/skills`. For a client that
reads another directory, configure the client or use a link to the canonical
copy instead of duplicating the skill.
