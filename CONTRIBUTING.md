# Contributing

The [contributing guide](docs/development/contributing.md) covers environment setup,
the validation commands, coding rules and pull request expectations. Read it together
with the [architecture review](docs/development/architecture-review.md) and the
[architecture decisions](docs/architecture/22-architecture-decision-records.md).

Container, Pod, Linux namespace and AppContainer backends share the host (or node)
kernel, so kernel, engine, runtime or cluster exploits are outside the threat model;
the Windows Job Object backend is containment only, not a security boundary. Do not
describe a backend as a security boundary unless real enforcement tests prove it.

- Discuss new subsystems, their contracts, dependencies, threat model and tests before
  writing the code.
- A pull request states which claims are documented, implemented, tested and not yet
  verified.
- Implemented APIs ship with tests, a runnable example and documentation. Do not add
  empty modules, fake examples, or mocked tests presented as proof of isolation.
- Use the relevant [Agent Skill](docs/development/agent-skills.md) for
  security-sensitive work.
- Preserve other contributors' changes and the original causes of errors.

Report security issues privately as described in [SECURITY.md](SECURITY.md), and follow
the [code of conduct](CODE_OF_CONDUCT.md). Contributions are distributed under the
[Apache-2.0 license](LICENSE).
