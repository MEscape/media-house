"""Story Architecture plans from what other modules decided; it owns no analysis and no storage.

* It reaches every other module only through that module's public contract.
* Its domain imports no other module at all (the application maps upstream results into facts).
* It computes no media result: no process, file-system, database or numerical-library access.
* Only the pipeline (and the composition root) depends on it.
"""

from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
STORY = "media_house.modules.story_architecture"
DOMAIN = f"{STORY}.domain"


def test_story_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(STORY)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(STORY)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_story_domain_depends_on_no_other_module() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(DOMAIN)
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(STORY)
    ]

    assert offenders == []


def test_story_does_no_analysis_or_storage_of_its_own() -> None:
    forbidden = {"sqlite3", "subprocess", "shutil", "os", "numpy", "torch", "transformers", "cv2"}
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(STORY)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []


def test_only_the_pipeline_and_the_composition_root_depend_on_story() -> None:
    allowed = ("media_house.modules.media_pipeline", "media_house.bootstrap")
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(STORY)
        and not name.startswith(allowed)
        and any(i.target.startswith(STORY) for i in info.imports)
    ]

    assert offenders == []
