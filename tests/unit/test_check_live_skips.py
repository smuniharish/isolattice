import xml.etree.ElementTree as ElementTree
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from scripts.check_live_skips import executed, main, unexpected_skips

REPORT = """<testsuites><testsuite>
<testcase classname="tests.integration.test_a" name="test_runs"/>
<testcase classname="tests.integration.test_a" name="test_rootless_only">
<skipped message="the resource connection is rootful"/></testcase>
<testcase classname="tests.integration.test_b" name="test_examples[podman-x.py]">
<skipped message="missing profile"/></testcase>
</testsuite></testsuites>"""


def test_reports_only_unexpected_skips() -> None:
    report = ElementTree.fromstring(REPORT)  # noqa: S314 - fixed test literal
    assert executed(report) == 1
    assert unexpected_skips(report, ["test_rootless_only"]) == [
        "tests.integration.test_b::test_examples[podman-x.py]"
    ]
    assert unexpected_skips(report, ["test_rootless_only", "test_examples"]) == []


def test_main_fails_on_unexpected_skip_or_empty_run(tmp_path: Path) -> None:
    path = tmp_path / "report.xml"
    path.write_text(REPORT, encoding="utf-8")
    assert main([str(path), "--allow", "test_rootless_only"]) == 1
    assert main([str(path), "--allow", "test_rootless_only", "--allow", "test_examples"]) == 0
    path.write_text(
        '<testsuite><testcase name="test_a"><skipped/></testcase></testsuite>', encoding="utf-8"
    )
    assert main([str(path), "--allow", "test_a"]) == 1


@given(
    st.lists(
        st.tuples(st.from_regex(r"test_[a-z]{1,8}", fullmatch=True), st.booleans()), max_size=12
    )
)
def test_allowing_every_skipped_name_leaves_nothing(cases: list[tuple[str, bool]]) -> None:
    suite = ElementTree.Element("testsuite")
    for name, skipped in cases:
        case = ElementTree.SubElement(suite, "testcase", name=f"{name}[param]")
        if skipped:
            ElementTree.SubElement(case, "skipped")
    skipped_names = {name for name, skipped in cases if skipped}
    assert unexpected_skips(suite, skipped_names) == []
    assert len(unexpected_skips(suite, ())) == len([1 for _, skipped in cases if skipped])
    assert executed(suite) == len([1 for _, skipped in cases if not skipped])
