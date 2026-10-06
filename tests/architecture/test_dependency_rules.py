"""Executable architecture: the real source tree must obey the dependency rules."""

from pathlib import Path

from tests.architecture.analysis import check_all, find_cycles, scan

SRC = Path(__file__).resolve().parents[2] / "src"


def test_source_tree_obeys_the_dependency_rules() -> None:
    violations = check_all(scan(SRC))
    assert not violations, "\n" + "\n".join(violations)


def test_there_are_no_import_cycles() -> None:
    cycles = find_cycles(scan(SRC))
    assert not cycles, f"import cycles: {cycles}"


def test_the_scan_actually_sees_the_codebase() -> None:
    modules = scan(SRC)
    assert len(modules) > 50
    assert "media_house.modules.workspace.domain.workspace" in modules
    assert modules["media_house.modules.workspace.domain.workspace"].location.layer == "domain"
