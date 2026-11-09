# Contributor and coding-agent contract

Read the [architecture review](docs/development/architecture-review.md) and
[decisions](docs/architecture/22-architecture-decision-records.md) first.
Podman, Docker API, Kubernetes Pod, Linux namespace and Windows (AppContainer and
Job Object) backends are implemented. Containers, Pods, namespaces and AppContainers
share the host (or node) kernel, so kernel, engine, runtime or cluster exploits are
outside the threat model; the Windows Job Object backend is containment only, not a
security boundary. The native macOS backend, remote execution and other advanced
features remain proposed.
Review architecture and obtain the appropriate approval for new subsystems.

- Inspect existing files and preserve unrelated work.
- Use Python 3.12+, typed async-only execution/lifecycle contracts and explicit
  ownership. Configuration/immutable metadata may be synchronous.
- Extension points are `abc.ABC` subclasses, never `typing.Protocol`, and each is
  injectable so downstream packages can supply their own implementation.
- REQUIRED guarantees fail closed; PREFERRED alternatives need explicit
  acceptance. Never claim enforcement from an OS name, mock or skipped test.
- Verify authoritative containment identity, not direct-child exit or PID recursion.
  UNKNOWN never means successful termination.
- No silent fallback, swallowed cleanup error, unbounded stream or leaked secret.
- Reuse maintained libraries/OS primitives after dependency/security evaluation;
  keep every dependency at its latest stable release.
- Implemented features need API, docs, tests, real examples and security evidence.
- Type-check with pyrefly and build the docs with Zensical.
- Install Node.js tooling globally; do not add local npm manifests or node_modules.
- Use pytest and Hypothesis for example-based and property-based tests.
- Do not create empty packages, fake runnable code, or publish/deploy by implication.

Consult the matching contributor [skills](.github/skills) for task-specific
workflows; the user-facing skills live in [isolattice-skills](isolattice-skills/).
Use [development validation](docs/development/testing.md). Skills are guidance,
not a substitute for tests or explicit plugin trust.
