"""Video Improvement is an independent subsystem with one explicit, optional seam.

* It reaches Media Inspection only through that module's public contract, in its application
  layer (and ``module.py`` for wiring); Media Inspection knows nothing about it.
* It reaches the Media Library only through its public contract, and no other processing module.
* Array maths, processing libraries and tools stay in infrastructure; the domain and the
  application layer are plain Python.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
VIDEO = "media_house.modules.video_improvement"
INSPECTION_CONTRACT = "media_house.modules.media_inspection.application.contracts"
LIBRARY_CONTRACT = "media_house.modules.media_library.application.contracts"
OTHERS = (
    "media_house.modules.audio_improvement",
    "media_house.modules.audio_intelligence",
    "media_house.modules.image_adjustment",
    "media_house.modules.workspace",
)


def imports_of(
    modules: dict[str, ModuleInfo], package: str, target_prefix: str
) -> list[tuple[str, str]]:
    return [
        (name, i.target)
        for name, info in modules.items()
        if name.startswith(package)
        for i in info.imports
        if i.target.startswith(target_prefix)
    ]


def only_contract(found: list[tuple[str, str]], contract: str) -> bool:
    return all(t == contract or t.startswith(f"{contract}.") for _, t in found)


def test_video_improvement_depends_on_no_other_processing_module() -> None:
    modules = scan(SRC)

    for other in OTHERS:
        assert imports_of(modules, VIDEO, other) == []


def test_it_reaches_inspection_and_the_library_only_through_their_contracts() -> None:
    modules = scan(SRC)

    inspection = imports_of(modules, VIDEO, "media_house.modules.media_inspection")
    library = imports_of(modules, VIDEO, "media_house.modules.media_library")
    assert inspection, "the inspection seam should exist"
    assert only_contract(inspection, INSPECTION_CONTRACT), inspection
    assert only_contract(library, LIBRARY_CONTRACT), library


def test_only_application_code_and_wiring_use_the_inspection_seam() -> None:
    modules = scan(SRC)

    layers = {
        modules[name].location.layer
        for name, _ in imports_of(modules, VIDEO, "media_house.modules.media_inspection")
    }

    assert layers <= {"application", "module"}, layers


def test_the_seam_is_confined_to_a_few_named_files() -> None:
    modules = scan(SRC)

    users = {name for name, _ in imports_of(modules, VIDEO, "media_house.modules.media_inspection")}

    assert users <= {
        f"{VIDEO}.application.prior_inspection",
        f"{VIDEO}.application.improve_video",
        f"{VIDEO}.application.calibration",
        f"{VIDEO}.module",
    }, users


def test_inspection_and_the_audio_modules_do_not_depend_on_video_improvement() -> None:
    modules = scan(SRC)

    for package in (
        "media_house.modules.media_inspection",
        "media_house.modules.audio_improvement",
        "media_house.modules.audio_intelligence",
        "media_house.modules.media_library",
    ):
        assert imports_of(modules, package, VIDEO) == []


def test_array_maths_and_tools_stay_in_infrastructure() -> None:
    leaks = [
        f"{name} imports {i.target}"
        for name, info in scan(SRC).items()
        if name.startswith(VIDEO) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in {"numpy", "scipy", "subprocess", "cv2", "PIL", "torch"}
    ]

    assert leaks == []
