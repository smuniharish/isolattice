"""Validate documentation, source inventory, and evidence-backed feature claims."""

from __future__ import annotations

import argparse
import ast
import re
import sys
import tomllib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import markdown
import yaml
from yaml.nodes import MappingNode, ScalarNode

ARCHITECTURE = (
    "project-overview",
    "architecture-overview",
    "public-api-specification",
    "domain-model",
    "policy-model",
    "capability-model",
    "backend-contracts",
    "backend-resolution",
    "lifecycle-state-machine",
    "process-containment",
    "termination-engine",
    "exception-model",
    "snapshot-architecture",
    "checkpoint-architecture",
    "remote-backend-protocol",
    "container-backend-architecture",
    "security-threat-model",
    "observability",
    "dependency-evaluation",
    "testing-strategy",
    "packaging-and-distribution",
    "architecture-decision-records",
)
COMPARISONS = (
    "python-subprocess",
    "asyncio-subprocess",
    "multiprocessing",
    "docker",
    "podman",
    "kubernetes",
    "native-sandboxing",
    "vm-and-microvm",
    "remote-execution",
)
SKILLS = (
    "isolattice-architecture",
    "python-framework-development",
    "sandbox-backend-development",
    "linux-sandbox-security",
    "macos-sandbox-security",
    "windows-sandbox-security",
    "process-containment",
    "termination-engine",
    "async-runtime",
    "capability-model",
    "snapshot-checkpoint",
    "remote-backend",
    "container-backend",
    "security-review",
    "testing-and-validation",
    "documentation-maintenance",
)
USER_SKILLS = ("isolattice", "isolattice-extend")
SKILL_ROOTS = (Path(".github/skills"), Path("isolattice-skills/skills"))
EXAMPLE_SECTIONS = ("Prerequisites", "Acceptance", "Cleanup", "Security")
RUNNABLE_SECTIONS = ("Run", "Expected output")
EXAMPLE_STATUSES = ("runnable", "index", "proposed")
STALE_WORDING = (
    (re.compile(r"\bmkdocs\b", re.IGNORECASE), "MkDocs; the site is built with Zensical"),
    (re.compile(r"\bmypy\b", re.IGNORECASE), "mypy; the type checker is pyrefly"),
    (re.compile(r"\bdeprecat", re.IGNORECASE), "deprecation wording"),
    (re.compile(r"\bbreaking[ -]changes?\b", re.IGNORECASE), "breaking-change wording"),
)
FORBIDDEN_CLAIMS = (
    "unknown means successful termination",
    "process groups guarantee containment",
    "mock tests prove security enforcement",
)
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SNIPPET = re.compile(r'^\s*--8<-- "([^"]+)"\s*$', re.MULTILINE)


class ValidationError(ValueError):
    """A malformed repository document or metadata record."""


class PageHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if value is None:
                continue
            if key == "id":
                self.ids.add(value)
            if (tag == "a" and key == "href") or (tag == "img" and key == "src"):
                self.links.append(value)


@dataclass(frozen=True)
class Page:
    metadata: dict[str, object]
    body: str
    links: tuple[str, ...]
    ids: frozenset[str]


def mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{context}: expected a mapping with string keys")
    return dict(value.items())


def strings(value: object, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValidationError(f"{context}: expected a nonempty list")
    if not all(isinstance(item, str) and item for item in value):
        raise ValidationError(f"{context}: entries must be nonempty strings")
    return [item for item in value if isinstance(item, str)]


def yaml_mapping(source: str, context: str, *, base: bool = False) -> dict[str, object]:
    try:
        node = yaml.compose(source, Loader=yaml.BaseLoader if base else yaml.SafeLoader)
        if isinstance(node, MappingNode):
            keys = [key.value for key, _ in node.value if isinstance(key, ScalarNode)]
            if len(keys) != len(set(keys)):
                raise ValidationError(f"{context}: duplicate YAML keys")
        loader = yaml.BaseLoader if base else yaml.SafeLoader
        value: object = yaml.load(source, Loader=loader)  # noqa: S506 - both loaders are safe
    except yaml.YAMLError as error:
        raise ValidationError(f"{context}: invalid YAML: {error}") from error
    return mapping(value, context)


def parse_page(source: str, context: str) -> Page:
    metadata: dict[str, object] = {}
    body = source
    lines = source.splitlines()
    if lines and lines[0] == "---":
        try:
            end = lines.index("---", 1)
        except ValueError as error:
            raise ValidationError(f"{context}: unterminated frontmatter") from error
        metadata = yaml_mapping("\n".join(lines[1:end]), context)
        body = "\n".join(lines[end + 1 :])
    parser = PageHTML()
    parser.feed(markdown.markdown(body, extensions=["extra", "toc"]))
    return Page(metadata, body, tuple(parser.links), frozenset(parser.ids))


def skill_errors(name: str, page: Page) -> list[str]:
    errors: list[str] = []
    if page.metadata.get("name") != name or len(name) > 64 or NAME.fullmatch(name) is None:
        errors.append("skill name must match directory and standard naming rules")
    description = page.metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        errors.append("skill description must be a nonempty string of at most 1024 characters")
    elif "use" not in description.lower():
        errors.append("skill description must explain activation conditions")
    if len(page.body.split()) < 70:
        errors.append("skill body must contain a meaningful workflow and completion criteria")
    return errors


def example_errors(page: Page, expected_category: int, scripts: tuple[str, ...]) -> list[str]:
    """Check one category README against the scripts that exist in its directory."""
    errors: list[str] = []
    if type(page.metadata.get("category")) is not int:
        errors.append("example category must be an integer")
    elif page.metadata["category"] != expected_category:
        errors.append("example category must match directory number")
    status = page.metadata.get("status")
    sections: tuple[str, ...] = EXAMPLE_SECTIONS
    if status not in EXAMPLE_STATUSES:
        errors.append(f"example status must be one of {', '.join(EXAMPLE_STATUSES)}")
    elif status == "runnable":
        sections += RUNNABLE_SECTIONS
        if not scripts:
            errors.append("a runnable category needs at least one script")
        errors.extend(
            f"script not documented in README: {script}"
            for script in scripts
            if script not in page.body
        )
    elif scripts:
        errors.append(f"a category with scripts must be runnable, not {status}")
    elif status == "proposed" and "**Proposed.**" not in page.body:
        errors.append("a proposed category must say **Proposed.** plainly")
    errors.extend(
        f"missing example section: {section}"
        for section in sections
        if f"## {section}" not in page.body
    )
    return errors


def source_markdown(root: Path) -> list[Path]:
    files = list(root.glob("*.md"))
    for directory in ("docs", "examples", ".github", "isolattice-skills"):
        files.extend((root / directory).rglob("*.md"))
    return sorted(set(files))


def nav_targets(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [target for item in value for target in nav_targets(item)]
    if isinstance(value, dict):
        return [target for item in value.values() for target in nav_targets(item)]
    raise ValidationError("navigation: expected string, list or mapping")


@dataclass
class RepositoryValidator:
    root: Path
    errors: list[str] = field(default_factory=list)
    pages: dict[Path, Page] = field(default_factory=dict)

    def error(self, path: Path, message: str) -> None:
        self.errors.append(f"{path.relative_to(self.root)}: {message}")

    def require(self, relative: str) -> None:
        path = self.root / relative
        if not path.is_file():
            self.error(path, "required file missing")

    def inventory(self) -> None:
        for number, name in enumerate(ARCHITECTURE, 1):
            self.require(f"docs/architecture/{number:02d}-{name}.md")
        for name in COMPARISONS:
            self.require(f"docs/comparisons/{name}-vs-isolattice.md")
        for name in SKILLS:
            self.require(f".github/skills/{name}/SKILL.md")
        for name in USER_SKILLS:
            self.require(f"isolattice-skills/skills/{name}/SKILL.md")
        for name in ("python", "testing", "security", "documentation"):
            self.require(f".github/instructions/{name}.instructions.md")
        for name in (
            "README.md",
            "LICENSE",
            "AGENTS.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "CHANGELOG.md",
            ".github/copilot-instructions.md",
            ".github/CODEOWNERS",
            ".github/dependabot.yml",
            ".github/pull_request_template.md",
            "zensical.toml",
            ".readthedocs.yaml",
            "pyproject.toml",
            "uv.lock",
            "skills.sh.json",
            "src/isolattice/__init__.py",
            "src/isolattice/py.typed",
            "examples/README.md",
            "examples/templates/example.md",
            "docs/development/features.yml",
            "docs/development/architecture-review.md",
            "scripts/check_site_links.py",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
        ):
            self.require(name)
        for name in ("package.json", "package-lock.json", "node_modules", "mkdocs.yml"):
            path = self.root / name
            if path.exists():
                message = (
                    "the documentation site is configured only in zensical.toml"
                    if name == "mkdocs.yml"
                    else "Node tooling must be installed globally, not in this repository"
                )
                self.error(path, message)

    def documents(self) -> None:
        for path in source_markdown(self.root):
            if not path.resolve().is_relative_to(self.root):
                self.error(path, "source document escapes repository")
                continue
            try:
                page = parse_page(path.read_text(encoding="utf-8"), str(path))
            except ValidationError as error:
                self.error(path, str(error))
                continue
            self.pages[path.resolve()] = page
            if path.name == "SKILL.md":
                if not any(path.parent.parent == self.root / base for base in SKILL_ROOTS):
                    self.error(
                        path,
                        "skills must use .github/skills/<name>/SKILL.md "
                        "or isolattice-skills/skills/<name>/SKILL.md",
                    )
                for message in skill_errors(path.parent.name, page):
                    self.error(path, message)
            for claim in FORBIDDEN_CLAIMS:
                if claim in page.body.lower():
                    self.error(path, f"contradictory security claim: {claim}")
            for pattern, topic in STALE_WORDING:
                if pattern.search(page.body) or pattern.search(str(page.metadata)):
                    self.error(path, f"stale wording: {topic}")
            for reference in SNIPPET.findall(page.body):
                target = (self.root / reference).resolve()
                if not target.is_relative_to(self.root) or not target.is_file():
                    self.error(path, f"missing or out-of-repository snippet: {reference}")

    def links(self) -> None:
        for path, page in self.pages.items():
            for link in page.links:
                try:
                    parsed = urlsplit(link)
                except ValueError:
                    self.error(path, f"invalid link: {link}")
                    continue
                if parsed.scheme or parsed.netloc:
                    continue
                target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                if not target.is_relative_to(self.root) or not target.exists():
                    self.error(path, f"missing or out-of-repository link: {link}")
                    continue
                if parsed.fragment and target.suffix == ".md":
                    target_page = self.pages.get(target)
                    if target_page is not None and unquote(parsed.fragment) not in target_page.ids:
                        self.error(path, f"missing Markdown anchor: {link}")

    def examples(self) -> None:
        directories = sorted((self.root / "examples").glob("[0-9][0-9]-*"))
        numbers: list[int] = []
        for directory in directories:
            number = int(directory.name[:2])
            numbers.append(number)
            path = directory / "README.md"
            self.require(str(path.relative_to(self.root)))
            page = self.pages.get(path.resolve())
            if page is not None:
                scripts = tuple(sorted(script.name for script in directory.glob("*.py")))
                for message in example_errors(page, number, scripts):
                    self.error(path, message)
        if sorted(numbers) != list(range(1, 20)):
            self.error(self.root / "examples", "one README per category 1-19 required")

    def configuration(self) -> None:
        path = self.root / "zensical.toml"
        if path.is_file():
            try:
                config = tomllib.loads(path.read_text(encoding="utf-8"))
                project = mapping(config.get("project"), "zensical project")
                targets = nav_targets(project.get("nav"))
                expected = {
                    str(file.relative_to(self.root / "docs")).replace("\\", "/")
                    for file in (self.root / "docs").rglob("*.md")
                }
                if len(targets) != len(set(targets)):
                    self.error(path, "duplicate navigation targets")
                if set(targets) != expected:
                    self.error(path, f"navigation mismatch: {sorted(set(targets) ^ expected)}")
                validation = mapping(project.get("validation"), "zensical validation")
                if not all(
                    validation.get(key) is True for key in ("invalid_links", "invalid_link_anchors")
                ):
                    self.error(path, "strict builds must reject invalid links and anchors")
            except (tomllib.TOMLDecodeError, ValidationError) as error:
                self.error(path, str(error))
        path = self.root / "pyproject.toml"
        if path.is_file():
            try:
                config = tomllib.loads(path.read_text(encoding="utf-8"))
                project = mapping(config.get("project"), str(path))
                build = mapping(config.get("build-system"), str(path))
                groups = mapping(config.get("dependency-groups"), str(path))
                testing = strings(groups.get("test"), "test dependencies")
                types = strings(groups.get("types"), "type-checking dependencies")
                extras = mapping(project.get("optional-dependencies", {}), "extras")
                if project.get("requires-python") != ">=3.12":
                    self.error(path, "package requires Python >=3.12")
                if build.get("build-backend") != "uv_build":
                    self.error(path, "package requires the uv_build backend")
                if not all(
                    any(re.match(rf"{dependency}(?:[=<>!~]|$)", value) for value in testing)
                    for dependency in ("pytest", "hypothesis")
                ):
                    self.error(path, "test dependencies must include pytest and Hypothesis")
                if not any(re.match(r"pyrefly(?:[=<>!~]|$)", value) for value in types):
                    self.error(path, "type-checking dependencies must include pyrefly")
                declared = [
                    value
                    for values in (*groups.values(), *extras.values())
                    if isinstance(values, list)
                    for value in values
                    if isinstance(value, str)
                ]
                if any(re.match(r"(?:mypy|mkdocs)(?:[-=<>!~\[]|$)", value) for value in declared):
                    self.error(path, "dependencies must not include mypy or MkDocs packages")
                if project.get("license") != "Apache-2.0":
                    self.error(path, "metadata must preserve Apache-2.0")
            except (tomllib.TOMLDecodeError, ValidationError) as error:
                self.error(path, str(error))
        for path in (self.root / ".github/instructions").glob("*.instructions.md"):
            page = self.pages.get(path.resolve())
            if page is not None:
                for key in ("description", "applyTo"):
                    if not isinstance(page.metadata.get(key), str) or not page.metadata[key]:
                        self.error(path, f"instructions require nonempty {key}")
        path = self.root / ".readthedocs.yaml"
        if path.is_file():
            try:
                config = yaml_mapping(path.read_text(encoding="utf-8"), str(path))
                build = mapping(config.get("build"), "Read the Docs build")
                jobs = mapping(build.get("jobs"), "Read the Docs jobs")
                install = strings(jobs.get("install"), "Read the Docs install")
                html = strings(
                    mapping(jobs.get("build"), "Read the Docs build jobs").get("html"),
                    "Read the Docs HTML build",
                )
                if "mkdocs" in config or "sphinx" in config:
                    self.error(path, "Read the Docs must build with Zensical only")
                if not any("zensical build --strict" in command for command in html):
                    self.error(path, "Read the Docs must run a strict Zensical build")
                if not any("--locked" in command and "docs" in command for command in install):
                    self.error(path, "Read the Docs must install the locked docs group")
            except ValidationError as error:
                self.error(path, str(error))
        for path in (self.root / ".github/workflows").glob("*.yml"):
            try:
                config = yaml_mapping(path.read_text(encoding="utf-8"), str(path), base=True)
                jobs = mapping(config.get("jobs"), "workflow jobs")
                for value in jobs.values():
                    job = mapping(value, "workflow job")
                    steps = job.get("steps")
                    if not isinstance(steps, list):
                        raise ValidationError("workflow job steps must be a list")
                    for value in steps:
                        step = mapping(value, "workflow step")
                        action = step.get("uses")
                        if action is not None and (
                            not isinstance(action, str)
                            or re.fullmatch(r"[\w.-]+/[\w.-]+@[a-f0-9]{40}", action) is None
                        ):
                            self.error(path, "external actions must be pinned to commit revisions")
            except ValidationError as error:
                self.error(path, str(error))

    def coverage(self) -> None:
        path = self.root / "docs/development/features.yml"
        if not path.is_file():
            return
        try:
            ledger = yaml_mapping(path.read_text(encoding="utf-8"), str(path))
            if ledger.get("schema") != 1 or ledger.get("status") != "runtime":
                raise ValidationError("ledger must declare schema 1 and runtime status")
            entries = ledger.get("categories")
            if not isinstance(entries, list):
                raise ValidationError("ledger categories must be a list")
            ids: set[str] = set()
            categories: list[int] = []
            for value in entries:
                item = mapping(value, "feature")
                identifier = item.get("id")
                if not isinstance(identifier, str) or identifier in ids or not identifier:
                    raise ValidationError("feature IDs must be nonempty and unique")
                ids.add(identifier)
                category = item.get("category")
                if type(category) is not int:
                    raise ValidationError(f"{identifier}: category must be an integer")
                categories.append(category)
                status = item.get("status")
                if status == "proposed":
                    if item.get("tests") != []:
                        raise ValidationError(
                            f"{identifier}: proposed categories cannot claim tests"
                        )
                elif status == "partial":
                    implemented = strings(item.get("implemented"), f"{identifier}: implemented")
                    features = strings(item.get("features"), f"{identifier}: features")
                    if not set(implemented).issubset(features):
                        raise ValidationError(f"{identifier}: implemented features must be listed")
                    test_sources: list[str] = []
                    for key, directory in (("tests", "tests"), ("runnable_examples", "examples")):
                        for reference in strings(item.get(key), f"{identifier}: {key}"):
                            target = (self.root / reference).resolve()
                            allowed = (self.root / directory).resolve()
                            if (
                                not target.is_relative_to(allowed)
                                or not target.is_file()
                                or target.suffix != ".py"
                            ):
                                raise ValidationError(
                                    f"{identifier}: missing {key} source {reference}"
                                )
                            try:
                                text = target.read_text(encoding="utf-8")
                                source = ast.parse(text)
                            except (OSError, SyntaxError) as error:
                                raise ValidationError(
                                    f"{identifier}: invalid {key} source"
                                ) from error
                            imports_runtime = any(
                                (
                                    isinstance(node, ast.ImportFrom)
                                    and node.module is not None
                                    and (
                                        node.module == "isolattice"
                                        or node.module.startswith("isolattice.")
                                    )
                                )
                                or (
                                    isinstance(node, ast.Import)
                                    and any(
                                        alias.name == "isolattice"
                                        or alias.name.startswith("isolattice.")
                                        for alias in node.names
                                    )
                                )
                                for node in ast.walk(source)
                            )
                            if not imports_runtime:
                                raise ValidationError(
                                    f"{identifier}: {key} source must import the actual runtime"
                                )
                            if key == "tests":
                                if not any(
                                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                                    and node.name.startswith("test_")
                                    for node in ast.walk(source)
                                ):
                                    raise ValidationError(f"{identifier}: test source has no tests")
                                test_sources.append(text)
                            elif not any(target.name in text for text in test_sources):
                                raise ValidationError(
                                    f"{identifier}: runnable example must be exercised by a test"
                                )
                else:
                    raise ValidationError(f"{identifier}: unsupported feature status")
                for key in ("features", "planned_tests", "architecture"):
                    strings(item.get(key), f"{identifier}: {key}")
                references = [item.get(key) for key in ("api", "examples", "security")]
                references.extend(strings(item["architecture"], f"{identifier}: architecture"))
                for raw_reference in references:
                    if not isinstance(raw_reference, str):
                        raise ValidationError(f"{identifier}: references must be paths")
                    target = (self.root / raw_reference).resolve()
                    if not target.is_relative_to(self.root) or not target.is_file():
                        raise ValidationError(f"{identifier}: missing reference {raw_reference}")
                example_path = item["examples"]
                if not isinstance(example_path, str):
                    raise ValidationError(f"{identifier}: examples must be a path")
                example_page = self.pages.get((self.root / example_path).resolve())
                if example_page is None or example_page.metadata.get("category") != category:
                    raise ValidationError(f"{identifier}: example/ledger category mismatch")
            if sorted(categories) != list(range(1, 20)):
                raise ValidationError("ledger must cover categories 1-19 exactly once")
        except ValidationError as error:
            self.error(path, str(error))

    def run(self) -> list[str]:
        self.inventory()
        self.documents()
        self.links()
        self.examples()
        self.configuration()
        self.coverage()
        return self.errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error(f"repository root does not exist: {root}")
    errors = RepositoryValidator(root).run()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Repository validation passed: docs, links, skills, examples and coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
