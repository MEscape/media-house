"""Asset Relationships sits beside the library, inspection and proxy modules; it owns only claims.

* It reaches other modules only through their public contracts, and only from application code and
  wiring: the library, and the proxy manager (for proxy provenance and which proxy is served).
* It depends on no processing module, and nobody reaches into it.
* The library, inspection and proxy modules do not know about it.
* Databases stay in infrastructure: the domain and application layers are plain Python.
* Reconciliation (stored state) never touches recorded lineage: it imports neither the lineage
  projection nor the proxy manager.
"""

from pathlib import Path

from tests.architecture.analysis import ModuleInfo, scan

SRC = Path(__file__).resolve().parents[2] / "src"
REL = "media_house.modules.asset_relationships"
LIBRARY_CONTRACT = "media_house.modules.media_library.application.contracts"
PROXY_CONTRACT = "media_house.modules.proxy_manager.application.contracts"
REL_CONTRACT = f"{REL}.application.contracts"
#: Seam and the one contract it may reach. Inspection is added here only when a slice uses it.
SEAMS = {
    "media_house.modules.media_library": LIBRARY_CONTRACT,
    "media_house.modules.proxy_manager": PROXY_CONTRACT,
}
OTHERS = (
    "media_house.modules.audio_improvement",
    "media_house.modules.audio_intelligence",
    "media_house.modules.audio_visual_alignment",
    "media_house.modules.content_intelligence",
    "media_house.modules.image_adjustment",
    "media_house.modules.media_inspection",
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


def test_it_reaches_the_library_and_proxy_manager_only_through_their_contracts() -> None:
    modules = scan(SRC)

    for package, contract in SEAMS.items():
        found = imports_of(modules, REL, package)
        assert found, f"the {package} seam should exist"
        for name, target in found:
            assert target == contract or target.startswith(f"{contract}."), (name, target)
    for other in OTHERS:
        assert imports_of(modules, REL, other) == [], other


def test_only_application_code_and_wiring_use_those_seams() -> None:
    modules = scan(SRC)

    for package in SEAMS:
        layers = {modules[name].location.layer for name, _ in imports_of(modules, REL, package)}
        assert layers <= {"application", "module"}, (package, layers)


def test_stored_claims_do_not_depend_on_the_proxy_manager() -> None:
    """Only the lineage projection reads proxy provenance; claims, decisions and the store do not."""
    modules = scan(SRC)

    users = {name for name, _ in imports_of(modules, REL, "media_house.modules.proxy_manager")}

    assert users <= {f"{REL}.application.lineage_projection", f"{REL}.module"}, users


def test_nobody_depends_on_it_except_through_its_contract() -> None:
    modules = scan(SRC)

    for name, info in modules.items():
        if name.startswith(REL) or name.startswith("media_house.bootstrap"):
            continue
        for found in info.imports:
            if found.target.startswith(REL):
                assert found.target == REL_CONTRACT or found.target.startswith(
                    f"{REL_CONTRACT}."
                ), (name, found.target)


def test_the_modules_it_reads_do_not_know_about_it() -> None:
    modules = scan(SRC)

    for package in (
        "media_house.modules.media_library",
        "media_house.modules.media_inspection",
        "media_house.modules.proxy_manager",
    ):
        assert imports_of(modules, package, REL) == [], package


def test_databases_stay_in_infrastructure() -> None:
    leaks = [
        f"{name} imports {i.target}"
        for name, info in scan(SRC).items()
        if name.startswith(REL) and info.location.layer in {"domain", "application"}
        for i in info.imports
        if i.target.split(".")[0] in {"subprocess", "sqlite3", "shutil", "os", "numpy"}
    ]

    assert leaks == []


def test_the_domain_stays_free_of_other_modules_and_the_application_layer() -> None:
    modules = scan(SRC)

    outside = [
        f"{name} imports {i.target}"
        for name, info in modules.items()
        if name.startswith(f"{REL}.domain")
        for i in info.imports
        if i.target.startswith("media_house.modules") and not i.target.startswith(f"{REL}.domain")
    ]

    assert outside == []


RECONCILIATION = (
    f"{REL}.application.reconciliation",
    f"{REL}.domain.reconciliation",
    f"{REL}.infrastructure.sqlite_reconciliation_store",
)


def test_reconciliation_never_touches_lineage_or_the_proxy_manager() -> None:
    modules = scan(SRC)

    forbidden = (
        f"{REL}.application.lineage_projection",
        f"{REL}.domain.lineage",
        "media_house.modules.proxy_manager",
    )
    for name in RECONCILIATION:
        assert name in modules, name
        for found in modules[name].imports:
            assert not found.target.startswith(forbidden), (name, found.target)


def test_reconciliation_reads_the_library_only_through_the_narrow_port() -> None:
    modules = scan(SRC)

    direct = [
        name
        for name in RECONCILIATION
        for found in modules[name].imports
        if found.target.startswith("media_house.modules.media_library")
    ]

    assert direct == []  # the use case takes the library as `AssetLookup` (application.ports)


def test_reconciliation_uses_the_shared_job_context_and_builds_no_scheduler() -> None:
    modules = scan(SRC)

    imported = {
        found.target
        for name in RECONCILIATION
        for found in modules[name].imports
        if found.target.startswith("media_house.shared.concurrency")
    }

    assert imported <= {
        "media_house.shared.concurrency",
        "media_house.shared.concurrency.JobContext",
    }
    for name, info in modules.items():
        if name.startswith(REL):
            assert not any(
                i.target.endswith(("ThreadPoolJobScheduler", "JobScheduler")) for i in info.imports
            ), name
