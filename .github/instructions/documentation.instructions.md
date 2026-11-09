---
description: "Use when maintaining documentation, skills or example READMEs: status, links and traceability."
applyTo: "docs/**/*.md,examples/**/*.md,.github/skills/**/SKILL.md,isolattice-skills/**/*.md"
---

# Documentation maintenance

Label every feature Implemented, Proposed or Not verified. State each backend's
threat model accurately: shared-kernel backends exclude kernel, engine, runtime or
cluster exploits, Windows Job Objects are not a security boundary, and no backend is
certified for hostile multi-tenant use. Code snippets use only names
exported by `isolattice`; runnable examples live in `examples/` and are run by a
live test. Example READMEs declare `runnable`, `index` or `proposed` status.

Canonical details live in the numbered architecture documents; guides link to them
rather than repeat them. Cite upstream claims and keep
[`features.yml`](../../docs/development/features.yml) in step with the code. Run
`uv run zensical build --strict`, `uv run python scripts/check_site_links.py`,
markdownlint and `uv run python scripts/validate_repository.py`. Every page must
appear in the `zensical.toml` navigation. The README is rendered on PyPI, so its
links are absolute URLs. Broken references fail; network-inconclusive external
checks stay visible. Make no deployment claim until it is configured and verified.
