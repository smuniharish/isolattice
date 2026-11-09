---
name: testing-and-validation
description: "Use when adding validator tests, backend conformance, platform security tests, CI or release evidence."
---

# Testing and validation

Read [test strategy](../../../docs/architecture/20-testing-strategy.md) and
[commands](../../../docs/development/testing.md).

## Workflow

1. Select exact requirement assertions, not proxy thresholds.
2. Add negative, race and error cases, and state the evidence level.
3. Use pytest for examples and Hypothesis for properties; the `ci` profile runs more
   examples (`HYPOTHESIS_PROFILE=ci`).
4. Run Ruff, pyrefly on three platforms, the repository validator and the docs and
   Markdown checks.
5. Record skipped, inconclusive and failed tests separately; a skip is not a pass.
6. Run every new example script from a live test, against the real engine.
7. Run adversarial workloads only on approved disposable environments.
8. Validate cleanup independently by containment identity.
9. Update feature coverage and example status with the actual evidence, and keep the
   combined unit and live coverage gate in `ci.yml` passing.

Prefer mature tools. Never count mocks as OS security. A docs build is not
deployment, package or runtime qualification.

Done requires passing applicable checks, truthful limitations and coverage
links; failing or inconclusive security evidence cannot qualify a backend.
