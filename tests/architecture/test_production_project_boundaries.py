"""The production project is the one owner of a brief-to-cut project.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module and does no I/O (files, processes, databases, clocks).
* It stores no media and no analysis: the library, the inspections, transcripts and video
  analyses stay with their owners; only its own project documents live in its database.
* Only the pipeline, the Control Center and the composition root depend on it.
"""

from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
PROJECT = "media_house.modules.production_project"
DOMAIN = f"{PROJECT}.domain"
APPLICATION = f"{PROJECT}.application"


def test_production_project_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PROJECT)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(PROJECT)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_the_domain_depends_on_no_other_module_and_does_no_io() -> None:
    forbidden = {
        "sqlite3",
        "subprocess",
        "shutil",
        "os",
        "pathlib",
        "tempfile",
        "socket",
        "zipfile",
    }
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if (i.target.startswith("media_house.modules.") and not i.target.startswith(PROJECT))
        or i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_the_application_reads_no_media_and_runs_no_process() -> None:
    forbidden = {"sqlite3", "subprocess", "shutil", "os", "numpy", "torch", "PIL", "zipfile"}
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(APPLICATION)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_only_the_pipeline_the_control_center_and_the_composition_root_depend_on_it() -> None:
    allowed = (
        "media_house.modules.media_pipeline",
        "media_house.modules.control_center",
        "media_house.bootstrap",
    )
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(PROJECT)
        and not name.startswith(allowed)
        and any(i.target.startswith(PROJECT) for i in info.imports)
    ]

    assert offenders == []
