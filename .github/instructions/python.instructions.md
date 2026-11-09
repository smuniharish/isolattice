---
description: "Use when editing Python framework or validation tooling: typing, ownership, async correctness and errors."
applyTo: "**/*.py"
---

# Python development

Target Python 3.12+. Use complete annotations, immutable leaf models and explicit
errors. Follow [domain contracts](../../docs/architecture/04-domain-model.md).
No sync execution/lifecycle API, hidden global registry or unnecessary casts.
Do not hold asyncio locks across unbounded I/O. Blocking SDK calls need owned,
bounded adapters; cancelled awaits do not stop threads.

Every extension point is an `abc.ABC` subclassed explicitly, never a
`typing.Protocol`, and every hard-coded choice a downstream package may need to
replace is an injectable constructor argument. Mark overrides with `@override` and
give public APIs Google-style docstrings.

Run `uv run ruff check .`, `uv run ruff format --check .`, pyrefly for the `linux`,
`win32` and `darwin` platforms, focused pytest tests and relevant Hypothesis
properties. Review new runtime subsystems before implementation. Preserve
cancellation and cleanup errors according to the
[exception model](../../docs/architecture/12-exception-model.md).
