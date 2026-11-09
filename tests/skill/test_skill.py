"""The Agent Skills are valid, self-consistent, evaluated, and their scripts and templates pass."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from skills_ref import read_properties, validate

import isolattice

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "isolattice-skills" / "skills"
SKILLS = sorted(path for path in SKILLS_ROOT.iterdir() if (path / "SKILL.md").is_file())
CORE = SKILLS_ROOT / "isolattice"
EXTEND = SKILLS_ROOT / "isolattice-extend"
LINK = re.compile(r"\]\(([^)#<>\s]+)(?:#[^)]*)?\)")
by_name = pytest.mark.parametrize("skill", SKILLS, ids=lambda path: path.name)


def run(
    *arguments: str, cwd: Path = ROOT, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-W", "error", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=False,
        cwd=cwd,
        env=None if environment is None else os.environ | environment,
    )


def pytest_run(target: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # A private basetemp keeps the nested run away from the user-wide pytest temp directory.
    return run(
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--basetemp={tmp_path}",
        f"--rootdir={target}",
        str(target),
        cwd=target,
    )


def test_both_skills_exist_and_skills_cli_discovers_them() -> None:
    assert [path.name for path in SKILLS] == ["isolattice", "isolattice-extend"]
    config = json.loads((ROOT / "skills.sh.json").read_text(encoding="utf-8"))
    assert [(ROOT / path).resolve() for path in config["paths"]] == [SKILLS_ROOT.resolve()]


@by_name
def test_the_skill_follows_the_agent_skills_specification(skill: Path) -> None:
    properties = read_properties(skill)

    assert validate(skill) == []
    assert properties.name == skill.name
    assert properties.license == "Apache-2.0"
    assert properties.metadata["version"] == isolattice.__version__
    assert properties.description.startswith("Use this skill when")
    assert "Do not use it" in properties.description
    assert len((skill / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 500


@by_name
def test_references_are_one_level_deep_linked_and_every_link_resolves(skill: Path) -> None:
    body = (skill / "SKILL.md").read_text(encoding="utf-8")
    references = sorted((skill / "references").iterdir())
    assert all(path.is_file() and path.suffix == ".md" for path in references)
    for path in references:
        assert f"](references/{path.name})" in body, f"SKILL.md never links {path.name}"
    for page in skill.rglob("*.md"):
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if "://" not in target:
                assert (page.parent / target).is_file(), f"{page.name} links to {target}"


@by_name
def test_every_bundled_script_and_asset_is_mentioned_by_the_skill(skill: Path) -> None:
    body = (skill / "SKILL.md").read_text(encoding="utf-8")
    bundled = [
        path
        for folder in ("scripts", "assets")
        if (skill / folder).is_dir()
        for path in (skill / folder).glob("*.py")
    ]
    assert bundled
    for path in bundled:
        assert f"{path.parent.name}/{path.name}" in body, f"SKILL.md never mentions {path.name}"


@by_name
def test_the_evals_are_well_formed(skill: Path) -> None:
    evals = json.loads((skill / "evals" / "evals.json").read_text(encoding="utf-8"))

    assert evals["skill_name"] == skill.name
    cases = evals["evals"]
    assert len(cases) >= 3
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["prompt"].strip()
        assert case["expected_output"].strip()
        assert len(case["assertions"]) >= 3
        for file in case.get("files", []):
            assert (skill / "evals" / file).is_file(), file


@by_name
def test_the_trigger_queries_are_balanced_and_split(skill: Path) -> None:
    data = json.loads((skill / "evals" / "trigger_queries.json").read_text(encoding="utf-8"))
    queries = data["queries"]

    assert data["skill_name"] == skill.name
    assert len(queries) >= 16
    assert len({query["query"] for query in queries}) == len(queries)
    for split in ("train", "validation"):
        labels = [query["should_trigger"] for query in queries if query["split"] == split]
        assert True in labels, f"{split} has no query that should trigger"
        assert False in labels, f"{split} has no near-miss query"
    train = sum(query["split"] == "train" for query in queries)
    assert 0.5 <= train / len(queries) <= 0.7
    positives = sum(query["should_trigger"] for query in queries)
    assert 0.4 <= positives / len(queries) <= 0.6


def test_no_query_triggers_both_skills() -> None:
    def triggers(skill: Path) -> set[str]:
        data = json.loads((skill / "evals" / "trigger_queries.json").read_text(encoding="utf-8"))
        return {query["query"].lower() for query in data["queries"] if query["should_trigger"]}

    assert not triggers(CORE) & triggers(EXTEND), "a query must not trigger both skills"


def test_the_setup_check_passes() -> None:
    completed = run(str(CORE / "scripts" / "verify_setup.py"))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "FAIL" not in completed.stdout
    assert completed.stdout.rstrip().endswith("All checks passed.")


def test_the_setup_check_reports_json_and_requires_the_s3_extra_on_request() -> None:
    completed = run(str(CORE / "scripts" / "verify_setup.py"), "--s3", "--json")
    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report["ok"] is True
    assert report["summary"] == "All checks passed."
    assert {check["status"] for check in report["checks"]} <= {"PASS", "INFO"}
    assert any("aiobotocore" in check["message"] for check in report["checks"])


def test_the_setup_check_requires_the_package_s3_range() -> None:
    source = (CORE / "scripts" / "verify_setup.py").read_text(encoding="utf-8")
    checked: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "S3":
                checked = ast.literal_eval(node.value)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = {}
    for requirement in project["optional-dependencies"]["s3"]:
        match = re.fullmatch(r"([\w-]+)>=([\d.]+),<([\d.]+)", requirement)
        assert match, requirement
        name, low, high = match.groups()
        declared[name] = (
            tuple(int(part) for part in low.split(".")),
            tuple(int(part) for part in high.split(".")),
        )

    assert checked == declared


def test_the_policy_template_passes(tmp_path: Path) -> None:
    completed = pytest_run(CORE / "assets", tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "5 passed" in completed.stdout


def test_the_extension_templates_pass_their_tests(tmp_path: Path) -> None:
    completed = pytest_run(EXTEND / "assets", tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    # 4 store, 3 event-sink and 3 runner checks.
    assert "10 passed" in completed.stdout


def test_the_store_check_passes_a_conforming_store(tmp_path: Path) -> None:
    completed = run(
        str(EXTEND / "scripts" / "check_store.py"),
        "store_template:from_environment",
        "--path",
        str(EXTEND / "assets"),
        "--json",
        environment={"ISOLATTICE_SQLITE_DATABASE": str(tmp_path / "store.sqlite3")},
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report == {"ok": True, "store": "SQLiteArtifactStore", "broken": None}


def test_the_store_check_names_a_broken_promise(tmp_path: Path) -> None:
    (tmp_path / "overwriting.py").write_text(
        "from pathlib import Path\n\n"
        "from isolattice import LocalArtifactStore\n\n\n"
        "class Overwriting(LocalArtifactStore):\n"
        "    async def publish(self, key, archive, manifest):\n"
        "        if not await self.exists(key):\n"
        "            await super().publish(key, archive, manifest)\n\n\n"
        "def build():\n"
        f"    return Overwriting(Path({str(tmp_path)!r}))\n",
        encoding="utf-8",
    )
    completed = run(
        str(EXTEND / "scripts" / "check_store.py"), "overwriting:build", "--path", str(tmp_path)
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert completed.stdout.rstrip() == (
        "FAIL  Overwriting: publishing an existing key did not fail"
    )


def test_the_store_check_rejects_an_unloadable_factory() -> None:
    completed = run(str(EXTEND / "scripts" / "check_store.py"), "no_such_module:Store")

    assert completed.returncode == 2
    assert "cannot load no_such_module:Store" in completed.stderr
