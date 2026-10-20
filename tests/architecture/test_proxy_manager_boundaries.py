"""Proxy Manager is a derivative-lifecycle module with two explicit seams.

* It reaches the Media Library and Media Inspection only through their public contracts, and
  only from application code and wiring.
* It depends on no other processing module, and no other module reaches into it.
* Tools and databases stay in infrastructure: the domain and application layers are plain Python.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
PROXY = "media_house.modules.proxy_manager"
LIBRARY_CONTRACT = "media_house.modules.media_library.application.contracts"
INSPECTION_CONTRACT = "media_house.modules.media_inspection.application.contracts"
PROXY_CONTRACT = f"{PROXY}.application.contracts"
OTHERS = (
    "media_house.modules.audio_improvement",
    "media_house.modules.audio_intelligence",
    "media_house.modules.audio_visual_alignment",
    "media_house.modules.content_intelligence",
    "media_house.modules.image_adjustment",
    "media_house.modules.video_improvement",
    "media_house.modules.video_intelligence",
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


def test_proxy_manager_depends_on_no_other_processing_module() -> None:
    modules = scan(SRC)

    for other in OTHERS:
        assert imports_of(modules, PROXY, other) == []


def test_it_reaches_the_library_and_inspection_only_through_their_contracts() -> None:
    modules = scan(SRC)

    library = imports_of(modules, PROXY, "media_house.modules.media_library")
    inspection = imports_of(modules, PROXY, "media_house.modules.media_inspection")

    assert library and inspection, "both seams should exist"
    assert only_contract(library, LIBRARY_CONTRACT), library
    assert only_contract(inspection, INSPECTION_CONTRACT), inspection


def test_only_application_code_and_wiring_use_those_seams() -> None:
    modules = scan(SRC)

    for target in ("media_house.modules.media_library", "media_house.modules.media_inspection"):
        layers = {modules[name].location.layer for name, _ in imports_of(modules, PROXY, target)}
        assert layers <= {"application", "module"}, (target, layers)


def test_inspection_is_reached_only_by_the_use_case_its_facts_adapter_and_wiring() -> None:
    modules = scan(SRC)

    users = {name for name, _ in imports_of(modules, PROXY, "media_house.modules.media_inspection")}

    assert users <= {
        f"{PROXY}.application.ensure_proxy",
        f"{PROXY}.application.inspection_facts",
        f"{PROXY}.module",
    }, users


def test_nobody_depends_on_proxy_manager_except_through_its_contract() -> None:
    modules = scan(SRC)

    for name, info in modules.items():
        if name.startswith(PROXY) or name.startswith("media_house.bootstrap"):
            continue
        for found in info.imports:
            if found.target.startswith(PROXY):
                assert found.target == PROXY_CONTRACT or found.target.startswith(
                    f"{PROXY_CONTRACT}."
                ), (name, found.target)


def test_the_library_and_inspection_do_not_know_about_proxies() -> None:
    modules = scan(SRC)

    for package in ("media_house.modules.media_library", "media_house.modules.media_inspection"):
        assert imports_of(modules, package, PROXY) == []


def test_tools_and_databases_stay_in_infrastructure() -> None:
    leaks = [
        f"{name} imports {i.target}"
        for name, info in scan(SRC).items()
        if name.startswith(PROXY) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in {"subprocess", "sqlite3", "shutil", "os", "numpy"}
    ]

    assert leaks == []
