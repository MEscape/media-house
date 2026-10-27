"""Media Pipeline orchestrates the other modules and owns no media result.

* It reaches every other module only through that module's public contract.
* Nothing else depends on it: it is a consumer at the top of the module graph.
* Its domain stays plain Python (no threads, no I/O); scheduling lives in application.
"""

from pathlib import Path

from tests.architecture.analysis import scan

SRC = Path(__file__).resolve().parents[2] / "src"
PIPELINE = "media_house.modules.media_pipeline"


def test_pipeline_imports_other_modules_only_through_contracts() -> None:
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PIPELINE)
        for i in info.imports
        if i.target.startswith("media_house.modules.")
        and not i.target.startswith(PIPELINE)
        and ".application.contracts" not in i.target
    ]

    assert offenders == []


def test_no_other_module_depends_on_the_pipeline() -> None:
    offenders = [
        name
        for name, info in scan(SRC).items()
        if not name.startswith(PIPELINE)
        and any(i.target.startswith(PIPELINE) for i in info.imports)
        and not name.startswith("media_house.bootstrap")
    ]

    assert offenders == []


def test_pipeline_owns_no_storage_or_process_access() -> None:
    forbidden = ("sqlite3", "subprocess", "shutil", "os")
    offenders = [
        (name, i.target)
        for name, info in scan(SRC).items()
        if name.startswith(PIPELINE)
        for i in info.imports
        if i.target.split(".")[0] in forbidden
    ]

    assert offenders == []
