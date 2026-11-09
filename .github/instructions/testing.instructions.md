---
description: "Use when designing tests or CI: distinguish contract tests from real enforcement evidence."
applyTo: "tests/**/*.py,.github/workflows/*.yml"
---

# Testing and evidence

Follow the [testing strategy](../../docs/architecture/20-testing-strategy.md) and the
[development commands](../../docs/development/testing.md). Use pytest for
example-based tests and Hypothesis for properties. Run the smallest relevant checks
first, then the full unit suite. Negative cases must prove that bad inputs are
rejected.

Mocks test contracts, not OS security. Platform and dependency skips are not passes;
live suites run on the real engine or platform they claim (Podman, Docker API,
Kubernetes, Linux namespaces, Windows). Coverage is combined across unit and live
runs in [`ci.yml`](../workflows/ci.yml). Use approved disposable hosts for
adversarial runtime tests, bounded resources and independent teardown. Never
launch hazardous exhaustion fixtures on a shared workstation.
