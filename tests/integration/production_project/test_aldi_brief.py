"""An editorial, unpriced briefing must not inherit the Lidl campaign's promises."""

from pathlib import Path

import pytest

from media_house.modules.production_project.domain.brief_parser import parse_brief
from media_house.modules.production_project.domain.compliance import tag_length
from media_house.modules.production_project.domain.facts import products, referenced_keys
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.infrastructure.brief_reader import DocumentBriefReader
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import Ok
from tests.unit.production_project.fakes import FakeEvidence, ScriptedGenerator
from tests.unit.production_project.world import build_world

pytestmark = pytest.mark.integration

CURRENT = {"Plasma Pulse", "Wonderland", "Power Circus", "Golden Phoenix", "Colour Beast"}


def _row(*cells: str) -> str:
    widths = (22, 28, 12, 12, 12, 32)
    return "".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=False)).rstrip()


def editorial_brief() -> str:
    """Small, independently readable version of the PDF's wrapped current/historical tables."""
    return "\n".join(
        [
            "ALDI Feuerwerk 2026/27 \u2013 Video-Briefing",
            "Die ALDI-Süd-Vitrine ist bekannt. ALDI Nord und alle Preise fehlen noch.",
            "Alle Preise sind offen. Keine Preisversprechen; vor Veröffentlichung prüfen.",
            "Artikelliste ALDI Süd 2026/27 (Vitrine, ohne Preise)",
            _row("Artikel", "Art", "Schuss", "Kaliber", "NEM", "Brenndauer / Höhe"),
            _row(
                "Plasma Pulse",
                "Blitzstern-Batterie",
                "100",
                "15 mm",
                "offen",
                "ca. 80 s / ca. 25 m",
            ),
            _row("Wonderland", "Feuertopf-Batterie", "89", "20 mm", "392 g", "ca. 40 s / ca. 40 m"),
            _row("Power Circus", "Gold-Batterie", "80", "20 mm", "488 g", "ca. 40 s / ca. 35 m"),
            _row("Golden", "Fächer-Batterie", "21", "30 mm", "offen", "ca. 40 s / ca. 70 m"),
            _row("Phoenix", "", "", "", "", ""),
            _row(
                "Colour Beast",
                "Fontänen-Kombination",
                "offen",
                "offen",
                "offen",
                "ca. 80 s / ca. 4 m",
            ),
            "\fVergleich mit 2025/26: historische Referenz, keine aktuellen Preise",
            _row("Artikel", "Händler", "Umfang", "Preis 2025/26"),
            _row("Dragons Breath (Nico)", "Nord", "160 Schuss", "ca. 49,99 €"),
            _row("Poison", "Süd und Nord", "16 Schuss", "ca. 3,79\u20133,99 €"),
            "Stand der Quellen: Vorjahrespreise vor dem Dreh am aktuellen Prospekt abgleichen.",
            "Alle drei Referenzvideos wurden am 10.11.2026 veröffentlicht.",
            "Video-Ziel: Neuheiten und vermutete Umlabelungen mit Belegen unterscheiden.",
            "Archivbilder älterer Prospekte nur mit Genehmigung nutzen.",
        ]
    )


def test_unpriced_products_are_retained_and_historical_prices_cannot_become_current() -> None:
    model = parse_brief(editorial_brief(), reference_year=2026)
    rows = products(model.facts)

    assert model.client == "ALDI"
    assert {row.name for row in rows} == CURRENT
    assert all(row.price is None for row in rows)
    assert all(not fact.verified for fact in model.facts)
    specs = {fact.key: fact.value for fact in model.facts if fact.kind == "spec"}
    assert specs["shots_plasma-pulse"] == "100"
    assert specs["kaliber_golden-phoenix"] == "30"
    assert specs["effektmasse_wonderland"] == "392"
    assert "shots_colour-beast" not in specs
    assert "effektmasse_plasma-pulse" not in specs
    assert not any(f.kind == "price" for f in model.facts)
    assert any(f.kind == "historical_price" and f.value == "49,99" for f in model.facts)
    assert not any(d.kind == "publish" for d in model.deadlines)
    assert not any(f.key in {"order_window", "delivery_window"} for f in model.facts)


def test_brief_import_never_waits_for_a_model_and_downstream_artifacts_are_grounded() -> None:
    generator = ScriptedGenerator(["this model must not be called during basic import"])
    world = build_world(FakeEvidence({}, {}), generator)
    created = world.workflow.create_project(
        ProjectSettings(
            "aldi", "ALDI Feuerwerk 2026/27", "ALDI", "brochure_review", 2026, sponsored=False
        )
    )
    assert isinstance(created, Ok)
    pid = created.value.project_id
    extracted = world.workflow.extract_brief(
        pid, editorial_brief(), cancellation=CancellationToken()
    )
    assert isinstance(extracted, Ok)
    assert generator.prompts == []
    assert extracted.value.project.brief is not None
    assert isinstance(world.workflow.brainstorm(pid), Ok)
    opened = world.workflow.open_project(pid)
    assert isinstance(opened, Ok)
    assert isinstance(world.workflow.choose_concept(pid, opened.value.concepts[0].concept_id), Ok)
    scripted = world.workflow.generate_script(pid, use_model=False)
    assert isinstance(scripted, Ok)
    project: Project = scripted.value
    keys = {f.key for f in project.facts}
    assert set(referenced_keys(project.script)) <= keys
    assert not any(f.key in {"price_total", "price_spread"} for f in project.facts)
    assert "Bestellen kannst du vom" not in project.script
    assert "Geliefert wird" not in project.script
    planned = world.workflow.plan_material(pid)
    assert isinstance(planned, Ok)
    assert all(requirement.scene_ids for requirement in planned.value.requirements)
    contexts = world.workflow.build_context(pid)
    assert isinstance(contexts, Ok)
    assert len(contexts.value.contexts) == 3
    assert all(
        all(
            f"{section}:" in variant.text
            for section in ("Thema", "Idee", "Look", "Regeln", "Material", "Freiheit")
        )
        for variant in contexts.value.contexts
    )
    assert all("Lidl" not in variant.text for variant in contexts.value.contexts)
    assert all("Werbevideo" not in variant.text for variant in contexts.value.contexts)
    meta = world.workflow.build_metadata(pid)
    assert isinstance(meta, Ok)
    assert meta.value.metadata is not None
    assert tag_length(meta.value.metadata.tags) <= 500
    gate = world.workflow.check_gate(pid)
    assert isinstance(gate, Ok)
    assert not gate.value.can_export
    assert any(f.rule_id == "project.missing_material" for f in gate.value.blocking)
    generated_text = project.script + "\n" + "\n".join(v.text for v in contexts.value.contexts)
    assert "thumbnail" not in generated_text.casefold()


def test_real_aldi_pdf_if_available() -> None:
    """Convenience check for the supplied local fixture; CI uses the portable tests above."""
    pdf = Path.home() / "Downloads" / "ALDI Feuerwerk 202627 \u2013 Video-Briefing.pdf"
    if not pdf.is_file():
        pytest.skip("Local ALDI PDF is not installed; portable editorial fixture still runs")
    pytest.importorskip("pypdf")
    read = DocumentBriefReader().read(pdf)
    assert isinstance(read, Ok)
    model = parse_brief(read.value, reference_year=2026)
    rows = products(model.facts)
    assert {row.name for row in rows} >= CURRENT
    assert all(row.price is None for row in rows)
    assert "Dragons Breath (Nico)" not in {row.name for row in rows}
    assert not any(d.kind == "publish" for d in model.deadlines)
