"""Audio Improvement and Audio Intelligence are independent subsystems with one explicit seam.

* Improvement knows nothing about Intelligence.
* Intelligence reads processing provenance ONLY through Improvement's public contract, and only
  in its application layer (domain stays pure).
* The processing engines (FFmpeg filters, NumPy/SciPy) stay in infrastructure.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
IMPROVEMENT = "media_house.modules.audio_improvement"
INTELLIGENCE = "media_house.modules.audio_intelligence"
IMPROVEMENT_CONTRACT = f"{IMPROVEMENT}.application.contracts"


def importing(modules: dict[str, ModuleInfo], package: str, target_prefix: str) -> list[str]:
    return [
        f"{name}:{i.lineno} -> {i.target}"
        for name, info in modules.items()
        if name.startswith(package)
        for i in info.imports
        if i.target.startswith(target_prefix)
    ]


def test_improvement_does_not_depend_on_intelligence() -> None:
    assert importing(scan(SRC), IMPROVEMENT, INTELLIGENCE) == []


def test_intelligence_reaches_improvement_only_through_its_contract() -> None:
    found = importing(scan(SRC), INTELLIGENCE, IMPROVEMENT)

    assert found, "the provenance seam should exist"
    # imports are recorded with the imported member: ``...contracts`` or ``...contracts.<name>``
    assert all(
        line.split("-> ")[1] == IMPROVEMENT_CONTRACT
        or line.split("-> ")[1].startswith(f"{IMPROVEMENT_CONTRACT}.")
        for line in found
    ), found


def test_only_the_application_layer_of_intelligence_uses_the_seam() -> None:
    modules = scan(SRC)
    users = {
        name
        for name, info in modules.items()
        if name.startswith(INTELLIGENCE)
        and any(i.target.startswith(IMPROVEMENT) for i in info.imports)
    }

    assert users == {f"{INTELLIGENCE}.application.prior_processing"}


def test_signal_processing_libraries_stay_in_infrastructure() -> None:
    modules = scan(SRC)
    leaks = [
        f"{name} imports {i.target}"
        for name, info in modules.items()
        if name.startswith(IMPROVEMENT) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in {"numpy", "scipy", "subprocess"}
    ]

    assert leaks == []
