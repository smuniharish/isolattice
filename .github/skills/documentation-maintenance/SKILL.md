---
name: documentation-maintenance
description: "Use for Zensical docs, API reference, comparisons, examples, skills, links or feature traceability changes."
---

# Documentation maintenance

Read [examples standards](../../../docs/development/examples.md),
[skills](../../../docs/development/agent-skills.md) and
[feature coverage](../../../docs/development/feature-coverage.md).

## Workflow

1. Verify the actual implementation status in the source and its canonical contract.
2. Label unimplemented features Proposed and source external technical claims.
3. Update the `zensical.toml` navigation, `features.yml`, comparisons and example
   README status (`runnable`, `index` or `proposed`) together.
4. Document defaults, types, ranges, compatibility and security effects.
5. Keep the README PyPI-safe: absolute URLs and rendered diagram images.
6. Run `uv run zensical build --strict`, `scripts/check_site_links.py`, markdownlint,
   `node scripts/render-diagrams.mjs --check`, the repository validator and its tests.
7. Report external-network inconclusive results and deployment status honestly.

Use the locked Zensical and mkdocstrings tooling from the `docs` group. API pages are
generated from docstrings; never point an autodoc target at a missing name. No
superficial placeholders, duplicated skill content or fake runnable snippets.

Done means meaningful, consistent pages, passing required checks and explicit
not-verified or deployment limitations.
