---
name: python-framework-development
description: "Use when implementing approved typed Python APIs, models, packaging or framework extension interfaces."
---

# Python framework development

Consult [API](../../../docs/architecture/03-public-api-specification.md),
[models](../../../docs/architecture/04-domain-model.md) and
[packaging](../../../docs/architecture/21-packaging-and-distribution.md).

## Workflow

1. Confirm subsystem approval and actual exports.
2. Use Python 3.12+ annotations, immutable configuration and explicit ownership.
3. Model each extension point as an `abc.ABC` that implementers subclass explicitly,
   with a default implementation, a conformance check in `isolattice.testing` and a
   runnable example. Never use `typing.Protocol`.
4. Make every replaceable dependency an injectable constructor argument, so a
   downstream package can pass it through unchanged. Never pass raw engine flags that
   could weaken a required policy.
5. Reuse maintained dependencies; keep the core free of mandatory OS integrations and
   put optional ones behind extras such as `isolattice[s3]`.
6. Implement async lifecycle and execution only, with structured cause-preserving
   errors.
7. Add Google-style docstrings, contract tests, docs and runnable examples.
8. Run Ruff, pyrefly for `linux`, `win32` and `darwin`, focused tests, the docs build
   and clean artifact checks when applicable.

## Security and common mistakes

No fake implementation, global registry, unnecessary cast, blocking I/O in the
event loop or unverified security claim. Configuration validation is not
enforcement.

Done requires typed complete APIs and relevant validation; packaging claims need
clean wheel, sdist, `twine check --strict` and import evidence, not merely project
metadata.
