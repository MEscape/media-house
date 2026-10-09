"""Video Intelligence sits after Media Inspection and Video Improvement and owns no probing.

* Upstream modules know nothing about it (no import of it from them).
* It reaches other modules only through their public contracts, and only where the seam is.
* Its domain stays pure; array maths and FFmpeg stay in infrastructure.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "media_house.modules.video_intelligence"
#: Array maths, media tools and model runtimes: infrastructure only (behind engine adapters).
HEAVY_LIBRARIES = frozenset(
    {
        "numpy",
        "scipy",
        "subprocess",
        "cv2",
        "av",
        "PIL",
        "torch",
        "torchvision",
        "transformers",
        "huggingface_hub",
        "mediapipe",
        "rapidocr",
        "onnxruntime",
        "urllib",
        "skimage",
    }
)
OTHER_MODULES = (
    "media_inspection",
    "video_improvement",
    "media_library",
    "audio_intelligence",
    "audio_improvement",
)


def imports_of(modules: dict[str, ModuleInfo], package: str, target_prefix: str) -> list[str]:
    return [
        f"{name}:{i.lineno} -> {i.target}"
        for name, info in modules.items()
        if name.startswith(package)
        for i in info.imports
        if i.target.startswith(target_prefix)
    ]


def test_no_other_module_depends_on_video_intelligence_internals() -> None:
    modules = scan(SRC)
    contract = f"{PACKAGE}.application.contracts"
    leaks = [
        line
        for name, info in modules.items()
        if not name.startswith(PACKAGE)
        for i in info.imports
        if i.target.startswith(PACKAGE)
        and not (i.target == contract or i.target.startswith(f"{contract}."))
        for line in [f"{name}:{i.lineno} -> {i.target}"]
        if not name.startswith("media_house.bootstrap")
    ]

    assert leaks == []


def test_upstream_modules_do_not_know_video_intelligence() -> None:
    modules = scan(SRC)

    for upstream in ("media_inspection", "video_improvement", "media_library"):
        found = imports_of(modules, f"media_house.modules.{upstream}", PACKAGE)
        assert found == [], found


def test_video_intelligence_reaches_other_modules_only_through_their_contracts() -> None:
    modules = scan(SRC)
    offenders: list[str] = []
    for other in OTHER_MODULES:
        contract = f"media_house.modules.{other}.application.contracts"
        for line in imports_of(modules, PACKAGE, f"media_house.modules.{other}"):
            target = line.split("-> ")[1]
            if not (target == contract or target.startswith(f"{contract}.")):
                offenders.append(line)

    assert offenders == []


def test_the_seams_to_other_modules_are_where_they_belong() -> None:
    modules = scan(SRC)
    users: dict[str, set[str]] = {}
    for other in OTHER_MODULES:
        users[other] = {
            name
            for name, info in modules.items()
            if name.startswith(PACKAGE)
            and any(i.target.startswith(f"media_house.modules.{other}") for i in info.imports)
        }

    assert users["media_inspection"] == {f"{PACKAGE}.application.resolver", f"{PACKAGE}.module"}
    assert users["video_improvement"] == {f"{PACKAGE}.application.resolver"}
    assert users["audio_intelligence"] == set() and users["audio_improvement"] == set()
    assert not any(n.startswith(f"{PACKAGE}.domain") for names in users.values() for n in names)
    assert not any(
        n.startswith(f"{PACKAGE}.infrastructure") for names in users.values() for n in names
    )


def test_array_maths_and_media_tools_stay_in_infrastructure() -> None:
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


def test_only_adapters_import_a_model_runtime_and_each_names_one_family() -> None:
    modules = scan(SRC)
    importers = {
        name.rsplit(".", 1)[-1]
        for name, info in modules.items()
        if name.startswith(f"{PACKAGE}.infrastructure")
        for i in info.imports
        if i.target.split(".")[0]
        in {"torch", "torchvision", "transformers", "mediapipe", "rapidocr"}
    }

    assert importers <= {
        "torchvision_detector",
        "torch_support",
        "mediapipe_engines",
        "rapidocr_text",
        "clip_engines",
        "vlm_descriptions",
        "hf_models",
    }


def test_the_scan_sees_the_module() -> None:
    modules = scan(SRC)

    layers = {info.location.layer for name, info in modules.items() if name.startswith(PACKAGE)}
    assert {"domain", "application", "infrastructure", "module"} <= layers
