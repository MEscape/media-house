"""Media Inspection establishes technical truth and nothing else.

* It depends on the Media Library's public contract and on no processing module.
* The processing modules reach it only through its public contract, in their application layer
  (and ``module.py`` for wiring), and never the other way round.
* The domain stays free of tools: facts and rules are plain Python.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
INSPECTION = "media_house.modules.media_inspection"
INSPECTION_CONTRACT = f"{INSPECTION}.application.contracts"
PROCESSING_MODULES = (
    "media_house.modules.audio_improvement",
    "media_house.modules.audio_intelligence",
    "media_house.modules.image_adjustment",
)
LIBRARY_CONTRACT = "media_house.modules.media_library.application.contracts"


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


def test_inspection_depends_on_no_processing_module() -> None:
    modules = scan(SRC)

    for other in PROCESSING_MODULES:
        assert imports_of(modules, INSPECTION, other) == []


def test_inspection_reaches_the_library_only_through_its_contract() -> None:
    found = imports_of(scan(SRC), INSPECTION, "media_house.modules.media_library")

    assert found, "the library seam should exist"
    assert all(
        target == LIBRARY_CONTRACT or target.startswith(f"{LIBRARY_CONTRACT}.")
        for _, target in found
    ), found


def test_processing_modules_reach_inspection_only_through_its_contract() -> None:
    modules = scan(SRC)

    for package in PROCESSING_MODULES:
        for name, target in imports_of(modules, package, INSPECTION):
            assert target == INSPECTION_CONTRACT or target.startswith(f"{INSPECTION_CONTRACT}."), (
                name,
                target,
            )


def test_only_application_code_and_wiring_use_the_inspection_seam() -> None:
    modules = scan(SRC)
    layers: set[str] = set()
    for package in PROCESSING_MODULES:
        for name, _ in imports_of(modules, package, INSPECTION):
            layers.add(modules[name].location.layer)

    assert layers <= {"application", "module"}, layers


def test_the_audio_modules_really_use_the_seam() -> None:
    modules = scan(SRC)

    for package in PROCESSING_MODULES[:2]:
        assert imports_of(modules, package, INSPECTION), package


def test_inspection_domain_and_application_know_no_media_tools() -> None:
    leaks = [
        f"{name} imports {i.target}"
        for name, info in scan(SRC).items()
        if name.startswith(INSPECTION) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in {"numpy", "scipy", "subprocess", "PIL", "cv2"}
    ]

    assert leaks == []
