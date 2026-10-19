"""Audio-Visual Alignment relates results; it analyses nothing and decides nothing.

* Upstream modules know nothing about it.
* It reaches other modules only through their public contracts, and only the resolver, the view
  adapter, the use case and the module wiring do so (the domain never sees an upstream type).
* Its domain is pure: no array maths, no model runtime, no process, no network, no media writer.
* Nothing in it names an editing action (checked on the result model in the unit tests).
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "media_house.modules.audio_visual_alignment"
UPSTREAM = (
    "audio_intelligence",
    "video_intelligence",
    "content_intelligence",
    "media_inspection",
    "media_library",
)
OTHER_MODULES = (
    *UPSTREAM,
    "audio_improvement",
    "video_improvement",
    "workspace",
    "image_adjustment",
)
HEAVY_LIBRARIES = frozenset(
    {
        "numpy", "scipy", "torch", "torchvision", "torchaudio", "transformers", "tokenizers",
        "huggingface_hub", "onnxruntime", "PIL", "cv2", "av", "subprocess", "shutil", "http",
        "urllib", "socket", "requests", "httpx", "sqlite3", "os",
    }
)  # fmt: skip


def imports_of(modules: dict[str, ModuleInfo], package: str, target_prefix: str) -> list[str]:
    return [
        f"{name}:{i.lineno} -> {i.target}"
        for name, info in modules.items()
        if name.startswith(package)
        for i in info.imports
        if i.target.startswith(target_prefix)
    ]


def test_nobody_but_bootstrap_depends_on_its_internals() -> None:
    modules = scan(SRC)
    contract = f"{PACKAGE}.application.contracts"
    leaks = [
        f"{name}:{i.lineno} -> {i.target}"
        for name, info in modules.items()
        if not name.startswith((PACKAGE, "media_house.bootstrap"))
        for i in info.imports
        if i.target.startswith(PACKAGE)
        and not (i.target == contract or i.target.startswith(f"{contract}."))
    ]

    assert leaks == []


def test_no_other_module_knows_it_exists() -> None:
    modules = scan(SRC)

    for other in OTHER_MODULES:
        found = imports_of(modules, f"media_house.modules.{other}", PACKAGE)
        assert found == [], found


def test_it_reaches_other_modules_only_through_their_contracts() -> None:
    modules = scan(SRC)
    offenders: list[str] = []
    for other in OTHER_MODULES:
        contract = f"media_house.modules.{other}.application.contracts"
        for line in imports_of(modules, PACKAGE, f"media_house.modules.{other}"):
            target = line.split("-> ")[1]
            if not (target == contract or target.startswith(f"{contract}.")):
                offenders.append(line)

    assert offenders == []


def test_the_domain_never_sees_another_module() -> None:
    modules = scan(SRC)

    seen = [
        f"{name} imports {i.target}"
        for name, info in modules.items()
        if name.startswith(f"{PACKAGE}.domain")
        for i in info.imports
        if i.target.startswith("media_house.modules.") and not i.target.startswith(PACKAGE)
    ]

    assert seen == []


def test_only_the_adapters_to_other_modules_import_them() -> None:
    modules = scan(SRC)
    users: dict[str, set[str]] = {}
    for other in UPSTREAM:
        users[other] = {
            name
            for name, info in modules.items()
            if name.startswith(PACKAGE)
            and any(i.target.startswith(f"media_house.modules.{other}") for i in info.imports)
        }

    allowed = {
        f"{PACKAGE}.application.align",
        f"{PACKAGE}.application.resolver",
        f"{PACKAGE}.application.results",
        f"{PACKAGE}.application.views",
        f"{PACKAGE}.application.contracts",
        f"{PACKAGE}.module",
    }
    for module, layer_users in users.items():
        assert layer_users <= allowed, f"{module}: {sorted(layer_users - allowed)}"
        assert not any(n.startswith(f"{PACKAGE}.domain") for n in layer_users)


def test_the_domain_and_application_stay_free_of_heavy_libraries() -> None:
    modules = scan(SRC)
    leaks = [
        f"{name} imports {i.target}"
        for name, info in modules.items()
        if name.startswith(PACKAGE) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in HEAVY_LIBRARIES
    ]

    assert leaks == []


def test_third_party_libraries_do_not_appear_in_the_public_contract() -> None:
    modules = scan(SRC)
    contract = modules[f"{PACKAGE}.application.contracts"]

    third_party = {
        i.target.split(".")[0]
        for i in contract.imports
        if not i.target.startswith("media_house") and i.target.split(".")[0] != "typing"
    }

    assert third_party == set()


def test_there_is_no_infrastructure_or_presentation_layer_to_hide_work_in() -> None:
    modules = scan(SRC)

    layers = {info.location.layer for name, info in modules.items() if name.startswith(PACKAGE)}

    assert not {"infrastructure", "presentation"} & layers


def test_the_scan_sees_the_module() -> None:
    modules = scan(SRC)

    layers = {info.location.layer for name, info in modules.items() if name.startswith(PACKAGE)}
    assert {"domain", "application", "module"} <= layers
