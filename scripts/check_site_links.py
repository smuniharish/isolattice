"""Fail when the built docs site has a broken internal link or anchor.

``zensical build --strict`` validates links written in Markdown pages, but not
links inside API reference pages rendered from docstrings. This scans every
built HTML page instead.

Run after a build:

    uv run zensical build --strict
    uv run python scripts/check_site_links.py
"""

from __future__ import annotations

import argparse
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if identifier := values.get("id"):
            self.ids.add(identifier)
        if tag == "a" and (href := values.get("href")):
            self.links.append(href)


def _parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


def _target(site: Path, page: Path, path: str) -> Path | None:
    resolved = (page.parent / unquote(path)).resolve() if path else page
    if resolved.is_dir():
        resolved /= "index.html"
    return resolved if resolved.is_file() and resolved.is_relative_to(site) else None


def broken_links(site: Path) -> list[str]:
    site = site.resolve()
    pages = {path: _parse(path) for path in site.rglob("*.html")}
    problems = []
    for page, parsed in sorted(pages.items()):
        for href in parsed.links:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or href.startswith(("/", "mailto:")):
                continue
            target = _target(site, page, parts.path)
            where = page.relative_to(site).as_posix()
            if target is None:
                problems.append(f"{where}: missing page {href}")
            elif (
                parts.fragment
                and target in pages
                and unquote(parts.fragment) not in pages[target].ids
            ):
                problems.append(f"{where}: missing anchor {href}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("site", nargs="?", default="site", type=Path, help="built site directory")
    arguments = parser.parse_args(argv)
    if not arguments.site.is_dir():
        print(f"{arguments.site} does not exist; build the docs first.", file=sys.stderr)
        return 2
    problems = broken_links(arguments.site)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(problems)} broken internal links.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
