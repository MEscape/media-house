"""Content Intelligence sits after Audio and Video Intelligence and owns no probing or decoding.

* Upstream modules know nothing about it (no import of it from them).
* It reaches other modules only through their public contracts.
* Its domain is pure Python (no array maths, no model runtimes); its application layer never
  touches the network or a model library; each model/network library is named in exactly one
  infrastructure adapter.
* It never edits media: nothing here imports a media-writing or rendering path.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "media_house.modules.content_intelligence"
HEAVY_LIBRARIES = frozenset(
    {
        "numpy", "scipy", "torch", "torchvision", "torchaudio", "transformers", "tokenizers",
        "huggingface_hub", "onnxruntime", "sklearn", "nltk", "spacy", "PIL", "cv2", "av",
        "subprocess", "http", "urllib", "socket", "requests", "httpx",
    }
)  # fmt: skip
UPSTREAM = ("media_inspection", "audio_improvement", "video_improvement", "media_library")
OTHER_MODULES = (
    *UPSTREAM,
    "audio_intelligence",
    "video_intelligence",
    "workspace",
    "image_adjustment",
)


def imports_of(modules: dict[str, ModuleInfo], package: str, target_prefix: str) -> list[str]:
    return [
        f"{name}:{i.lineno} -> {i.target}"
        for name, info in modules.items()
        if name.startswith(package)
        for i in info.imports
        if i.target.startswith(target_prefix)
    ]


def test_nobody_but_bootstrap_depends_on_content_intelligence_internals() -> None:
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


def test_upstream_modules_do_not_know_content_intelligence() -> None:
    modules = scan(SRC)

    for upstream in (*UPSTREAM, "audio_intelligence", "video_intelligence"):
        found = imports_of(modules, f"media_house.modules.{upstream}", PACKAGE)
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

    assert users["audio_intelligence"] == {
        f"{PACKAGE}.application.analyze_content",
        f"{PACKAGE}.application.inputs",
        f"{PACKAGE}.module",
    }
    assert users["video_intelligence"] == users["audio_intelligence"]
    assert users["media_library"] == {
        f"{PACKAGE}.application.analyze_content",
        f"{PACKAGE}.application.results",
        f"{PACKAGE}.application.contracts",
        f"{PACKAGE}.module",
    }
    for module in (
        "media_inspection",
        "audio_improvement",
        "video_improvement",
        "workspace",
        "image_adjustment",
    ):
        assert users[module] == set(), f"{module} is not a source of content analysis"
    for layer_users in users.values():
        assert not any(
            n.startswith((f"{PACKAGE}.domain", f"{PACKAGE}.infrastructure")) for n in layer_users
        )


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


def test_each_runtime_or_network_library_has_one_adapter() -> None:
    modules = scan(SRC)
    importers: dict[str, set[str]] = {}
    for name, info in modules.items():
        if name.startswith(f"{PACKAGE}.infrastructure"):
            for i in info.imports:
                top = i.target.split(".")[0]
                if top in {"torch", "transformers", "huggingface_hub", "http", "socket", "numpy"}:
                    importers.setdefault(top, set()).add(name.rsplit(".", 1)[-1])

    model_adapters = {"transformers_embedder", "transformers_llm"}
    assert importers["torch"] == importers["transformers"] == model_adapters
    assert importers["http"] == importers["socket"] == {"loopback_llm"}
    assert importers["numpy"] == {"numpy_similarity", "transformers_embedder"}


def test_nothing_in_the_module_imports_a_media_writer() -> None:
    modules = scan(SRC)
    writers = [
        f"{name} imports {i.target}"
        for name, info in modules.items()
        if name.startswith(PACKAGE)
        for i in info.imports
        if i.target.split(".")[0] in {"subprocess", "shutil", "PIL", "cv2", "av"}
        or ".ffmpeg" in i.target
    ]

    assert writers == []


def test_the_scan_sees_the_module() -> None:
    modules = scan(SRC)

    layers = {info.location.layer for name, info in modules.items() if name.startswith(PACKAGE)}
    assert {"domain", "application", "infrastructure", "module"} <= layers
