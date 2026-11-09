import shutil
from pathlib import Path

import pytest

from scripts.validate_repository import (
    RepositoryValidator,
    ValidationError,
    example_errors,
    nav_targets,
    parse_page,
    skill_errors,
    yaml_mapping,
)

ROOT = Path(__file__).resolve().parents[2]


def test_actual_repository_passes() -> None:
    assert RepositoryValidator(ROOT).run() == []


@pytest.mark.parametrize(
    "source",
    [
        "---\nname: missing-close\n",
        "---\nname: [unterminated\n---\n",
        "---\n- not-a-mapping\n---\n",
        "---\nname: first\nname: second\n---\n",
    ],
)
def test_invalid_frontmatter_rejected(source: str) -> None:
    with pytest.raises(ValidationError):
        parse_page(source, "test")


def test_reference_links_and_anchors_use_markdown_parser() -> None:
    page = parse_page("# A title\n\n[ref][target]\n\n[target]: other.md#heading", "test")
    assert page.links == ("other.md#heading",)
    assert "a-title" in page.ids


def test_code_links_are_not_treated_as_document_links() -> None:
    page = parse_page("```text\n[not a link](missing.md)\n```", "test")
    assert page.links == ()


@pytest.mark.parametrize("name", ["Bad", "-bad", "bad-", "bad--name", "a" * 65])
def test_invalid_skill_name_rejected(name: str) -> None:
    page = parse_page(
        f"---\nname: {name}\ndescription: Use when testing\n---\n" + "word " * 90, "test"
    )
    assert skill_errors(name, page)


@pytest.mark.parametrize("description", ["", "x" * 1025, "42", "Unclear task"])
def test_invalid_skill_description_rejected(description: str) -> None:
    page = parse_page(
        f"---\nname: valid-skill\ndescription: '{description}'\n---\n" + "word " * 90, "test"
    )
    assert skill_errors("valid-skill", page)


def category_readme(category: str = "1", status: str = "index", extra: str = "") -> str:
    return (
        f"---\ncategory: {category}\nstatus: {status}\n---\n# Category\n\n{extra}\n\n"
        "## Prerequisites\n\n## Acceptance\n\n## Cleanup\n\n## Security\n"
    )


RUNNABLE = category_readme(
    status="runnable", extra="Runs `podman_demo.py`.\n\n## Run\n\n## Expected output"
)


@pytest.mark.parametrize("status", ["specification", "implemented", "unknown"])
def test_unknown_example_status_rejected(status: str) -> None:
    assert example_errors(parse_page(category_readme(status=status), "test"), 1, ())


@pytest.mark.parametrize("category", ["true", "'1'", "2"])
def test_invalid_example_category_rejected(category: str) -> None:
    assert example_errors(parse_page(category_readme(category=category), "test"), 1, ())


def test_complete_index_and_runnable_pages_pass() -> None:
    assert example_errors(parse_page(category_readme(), "test"), 1, ()) == []
    assert example_errors(parse_page(RUNNABLE, "test"), 1, ("podman_demo.py",)) == []
    proposed = category_readme(status="proposed", extra="**Proposed.** Not implemented.")
    assert example_errors(parse_page(proposed, "test"), 1, ()) == []


def test_index_page_may_reference_the_existing_runtime() -> None:
    page = parse_page(category_readme(extra="```python\nfrom isolattice import Sandbox\n```"), "t")
    assert example_errors(page, 1, ()) == []


@pytest.mark.parametrize(
    ("source", "scripts", "message"),
    [
        (RUNNABLE, (), "needs at least one script"),
        (RUNNABLE, ("podman_demo.py", "podman_other.py"), "not documented in README"),
        (RUNNABLE.replace("## Expected output", ""), ("podman_demo.py",), "Expected output"),
        (category_readme(), ("podman_demo.py",), "must be runnable"),
        (category_readme(status="proposed"), (), "**Proposed.**"),
    ],
)
def test_status_contradicting_the_scripts_rejected(
    source: str, scripts: tuple[str, ...], message: str
) -> None:
    errors = example_errors(parse_page(source, "test"), 1, scripts)
    assert any(message in error for error in errors)


def test_missing_example_section_rejected() -> None:
    page = parse_page(category_readme().replace("## Cleanup", "## Something else"), "test")
    assert example_errors(page, 1, ())


@pytest.mark.parametrize(
    "text",
    [
        "Build the site with MkDocs.",
        "Run mypy before pushing.",
        "This option is deprecated.",
        "This is a breaking change.",
    ],
)
def test_stale_wording_rejected(tmp_path: Path, text: str) -> None:
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    assert any("stale wording" in error for error in validator.errors)


def test_mkdocstrings_is_not_stale_wording(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("API pages use mkdocstrings.", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    assert validator.errors == []


def test_user_skill_location_is_accepted_and_others_rejected(tmp_path: Path) -> None:
    body = "---\nname: {name}\ndescription: Use when testing\n---\n" + "word " * 90
    for base, name in (("isolattice-skills/skills", "good-skill"), ("docs/skills", "bad-skill")):
        directory = tmp_path / base / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(body.format(name=name), encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    assert [error for error in validator.errors if "skills must use" in error] == [
        f"{Path('docs/skills/bad-skill/SKILL.md')}: skills must use .github/skills/<name>/SKILL.md "
        "or isolattice-skills/skills/<name>/SKILL.md"
    ]


def test_links_reject_missing_paths_and_anchors(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# Home\n\n[bad](missing.md)\n\n[anchor](other.md#absent)", encoding="utf-8"
    )
    (tmp_path / "other.md").write_text("# Present", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.links()
    assert any("missing.md" in error for error in validator.errors)
    assert any("missing Markdown anchor" in error for error in validator.errors)


def test_links_reject_repository_escape(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[escape](../outside.md)", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.links()
    assert any("out-of-repository" in error for error in validator.errors)


def test_external_links_are_not_contacted(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("[external](https://example.invalid/)", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.links()
    assert validator.errors == []


def test_snippet_reference_must_exist(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text('--8<-- "missing.yml"', encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    assert any("snippet" in error for error in validator.errors)


def test_known_contradictory_claim_rejected(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("UNKNOWN means successful termination.", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    assert any("contradictory security claim" in error for error in validator.errors)


def test_missing_architecture_inventory_rejected(tmp_path: Path) -> None:
    validator = RepositoryValidator(tmp_path)
    validator.inventory()
    assert any("01-project-overview.md" in error for error in validator.errors)
    assert any("SKILL.md" in error for error in validator.errors)


def test_runtime_package_is_required(tmp_path: Path) -> None:
    validator = RepositoryValidator(tmp_path)
    validator.inventory()
    assert any("src" in error and "__init__.py" in error for error in validator.errors)


@pytest.mark.parametrize(
    "name", ["package.json", "package-lock.json", "node_modules", "mkdocs.yml"]
)
def test_repository_local_node_artifacts_and_mkdocs_rejected(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    if name == "node_modules":
        path.mkdir()
    else:
        path.write_text("{}", encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.inventory()
    expected = (
        "zensical.toml" if name == "mkdocs.yml" else "Node tooling must be installed globally"
    )
    assert any(expected in error and name in error for error in validator.errors)


@pytest.mark.parametrize(
    "ledger",
    [
        "schema: 2\nstatus: architecture-only\ncategories: []",
        "schema: 1\nstatus: production\ncategories: []",
        "schema: 1\nstatus: architecture-only\ncategories: []",
        "schema: 1\nstatus: architecture-only\ncategories:\n  - id: fake\n    category: 1\n"
        "    status: implemented\n    tests: []",
        "schema: 1\nstatus: runtime\ncategories:\n  - id: fake\n    category: 1\n"
        "    status: partial\n    features: [missing]\n    implemented: [missing]\n    tests: []",
    ],
)
def test_invalid_or_false_implemented_ledger_rejected(tmp_path: Path, ledger: str) -> None:
    directory = tmp_path / "docs" / "development"
    directory.mkdir(parents=True)
    (directory / "features.yml").write_text(ledger, encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.coverage()
    assert validator.errors


def test_duplicate_ledger_identity_rejected(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    shutil.copytree(
        ROOT / "tests", tmp_path / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    source = (ROOT / "docs/development/features.yml").read_text(encoding="utf-8")
    source = source.replace("id: execution", "id: getting-started")
    directory = tmp_path / "docs" / "development"
    (directory / "features.yml").write_text(source, encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.coverage()
    assert any("IDs must be nonempty and unique" in error for error in validator.errors)


def test_partial_feature_requires_real_test_and_example_evidence(tmp_path: Path) -> None:
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    shutil.copytree(
        ROOT / "tests", tmp_path / "tests", ignore=shutil.ignore_patterns("__pycache__")
    )
    directory = tmp_path / "docs" / "development"
    ledger = (directory / "features.yml").read_text(encoding="utf-8")
    ledger = ledger.replace(
        "tests/unit/test_async_runtime.py", "tests/unit/missing_runtime_test.py", 1
    )
    (directory / "features.yml").write_text(ledger, encoding="utf-8")
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.coverage()
    assert any("missing tests source" in error for error in validator.errors)


def test_navigation_parser_rejects_invalid_shape() -> None:
    with pytest.raises(ValidationError):
        nav_targets(42)


def test_navigation_parser_flattens_labels() -> None:
    assert nav_targets([{"Home": "index.md"}, {"Section": [{"Page": "page.md"}]}]) == [
        "index.md",
        "page.md",
    ]


def test_yaml_mapping_rejects_nonstring_keys() -> None:
    with pytest.raises(ValidationError):
        yaml_mapping("1: bad", "test")


def test_outdated_skill_reference_rejected(tmp_path: Path) -> None:
    directory = tmp_path / ".github" / "skills" / "valid-skill"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\nname: valid-skill\ndescription: Use when testing\n---\n"
        + "word " * 90
        + "\n[old](../../../docs/obsolete.md)",
        encoding="utf-8",
    )
    validator = RepositoryValidator(tmp_path)
    validator.documents()
    validator.links()
    assert any("obsolete.md" in error for error in validator.errors)


def test_unpinned_workflow_action_rejected(tmp_path: Path) -> None:
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "test.yml").write_text(
        "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@main\n",
        encoding="utf-8",
    )
    validator = RepositoryValidator(tmp_path)
    validator.configuration()
    assert any("pinned to commit" in error for error in validator.errors)


def test_navigation_omission_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "docs"
    directory.mkdir()
    (directory / "index.md").write_text("# Home", encoding="utf-8")
    (directory / "missing.md").write_text("# Not in nav", encoding="utf-8")
    (tmp_path / "zensical.toml").write_text(
        '[project]\nnav = [{ "Home" = "index.md" }]\n\n'
        "[project.validation]\ninvalid_links = true\ninvalid_link_anchors = true\n",
        encoding="utf-8",
    )
    validator = RepositoryValidator(tmp_path)
    validator.configuration()
    assert [error for error in validator.errors if "zensical.toml" in error] == [
        "zensical.toml: navigation mismatch: ['missing.md']"
    ]


def test_lenient_link_validation_rejected(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "zensical.toml").write_text(
        "[project]\nnav = []\n\n[project.validation]\ninvalid_links = false\n", encoding="utf-8"
    )
    validator = RepositoryValidator(tmp_path)
    validator.configuration()
    assert any("invalid links and anchors" in error for error in validator.errors)


def test_mypy_or_mkdocs_dependency_rejected(tmp_path: Path) -> None:
    source = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        source.replace("docs = [\n", 'docs = [\n    "mkdocs-material>=9",\n', 1), encoding="utf-8"
    )
    validator = RepositoryValidator(tmp_path)
    validator.configuration()
    assert any("mypy or MkDocs" in error for error in validator.errors)


def test_readthedocs_source_mismatch_rejected(tmp_path: Path) -> None:
    source = (ROOT / ".readthedocs.yaml").read_text(encoding="utf-8")
    (tmp_path / ".readthedocs.yaml").write_text(
        source.replace("zensical build --strict", "zensical build"), encoding="utf-8"
    )
    validator = RepositoryValidator(tmp_path)
    validator.configuration()
    assert any("strict Zensical build" in error for error in validator.errors)
