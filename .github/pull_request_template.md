## Summary

<!-- What does this pull request change, and why? -->

## Related issues

<!-- For example: Closes #123. -->

## Type of change

- [ ] Bug fix
- [ ] New feature (backend, capability, extension-point implementation)
- [ ] Documentation
- [ ] Internal cleanup with no behavior change
- [ ] Other (describe above)

## Checklist

- [ ] I read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] New or changed behavior is covered by tests; enforcement claims are covered by live tests, not mocks.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run pyrefly check --python-platform linux`, `win32` and `darwin` pass.
- [ ] `uv run pytest` passes, and `uv run python scripts/validate_repository.py` passes.
- [ ] `uv run zensical build --strict` and `uv run python scripts/check_site_links.py` pass, and diagrams were
      re-rendered if a `diagrams/*.mmd` source changed.
- [ ] Docs, examples and `docs/development/features.yml` were updated together.
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]` for user-visible changes.

## Security impact

<!--
Does this change a guarantee, a capability state, a fail-closed path, cleanup or what is
logged? Describe the effect and the evidence, or write "None".
-->
