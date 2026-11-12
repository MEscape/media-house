"""The style memory is a self-contained store of taste.

* It imports no other module at all (it is read through its own contracts).
* Its domain does no I/O: no file, process, database or clock access.
* Only the project that generates in the creator's style, the Control Center that lets him review
  it, and the composition root depend on it.
"""

from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
STYLE = "media_house.modules.style_memory"
DOMAIN = f"{STYLE}.domain"


def test_style_memory_imports_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(STYLE)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(STYLE)
    ]

    assert offenders == []


def test_style_domain_does_no_io() -> None:
    forbidden = {"sqlite3", "subprocess", "shutil", "os", "pathlib", "io", "tempfile", "json"}
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_only_the_project_the_control_center_and_the_composition_root_depend_on_it() -> None:
    allowed = (
        "media_house.modules.production_project",
        "media_house.modules.control_center",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(STYLE)
        and not name.startswith(allowed)
        and any(i.target.startswith(STYLE) for i in info.imports)
    ]

    assert offenders == []
