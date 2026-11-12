"""Regression proof for reviewed edits, model grounding, and empty-project export."""

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from media_house.modules.production_project.application.story_step import RewriteProposal
from media_house.modules.production_project.domain.assets import AssetFlag
from media_house.modules.production_project.domain.brief import Fact
from media_house.modules.production_project.domain.claims import ClaimKind, RiskAction
from media_house.modules.production_project.domain.compliance import allowed_numbers
from media_house.modules.production_project.domain.manifest import Manifest, ManifestEntry
from media_house.modules.production_project.domain.project import ProjectSettings
from media_house.modules.production_project.domain.values import FlagKind, Resolution, Severity
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok
from tests.unit.production_project.fakes import (
    FakeEvidence,
    FakeRunner,
    FakeTimelines,
    ScriptedGenerator,
)
from tests.unit.production_project.world import build_world

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


def test_model_script_with_missing_products_is_repaired_then_falls_back_safely() -> None:
    response = json.dumps(
        {
            "scenes": [
                {
                    "scene_id": f"S{k:02d}",
                    "title": "Start",
                    "intent": "Test",
                    "function": "hook" if k == 1 else "product",
                    "graphics": "",
                    "spoken": ["Ein Satz."],
                }
                for k in range(1, 4)
            ]
        }
    )
    model = ScriptedGenerator([response])
    world = build_world(FakeEvidence({}, {}), model)
    created = world.workflow.create_project(
        ProjectSettings("video", "Video", "Lidl", "ranking", 2026)
    )
    assert isinstance(created, Ok)
    pid = created.value.project_id
    assert isinstance(world.workflow.extract_brief(pid, FIXTURE.read_text(encoding="utf-8")), Ok)
    concepts = world.workflow.brainstorm(pid)
    assert isinstance(concepts, Ok)
    assert isinstance(world.workflow.choose_concept(pid, concepts.value.concepts[0].concept_id), Ok)
    generated = world.workflow.generate_script(pid, cancellation=CancellationToken())
    assert isinstance(generated, Ok)
    assert len(model.prompts) == 3
    assert "Artikel fehlen" in model.prompts[-1][1]
    assert generated.value.generated[-1].generator == "deterministic"


def test_reviewed_proposal_cannot_overwrite_a_newer_script() -> None:
    world = build_world(FakeEvidence({}, {}))
    created = world.workflow.create_project(ProjectSettings("video", "Video", "", "generic", 2026))
    assert isinstance(created, Ok)
    pid = created.value.project_id
    assert isinstance(world.workflow.edit_script(pid, "Erste Version"), Ok)
    proposal = RewriteProposal("S01", "Kürzer", "Erste Version", "Vorschlag")
    assert isinstance(world.workflow.edit_script(pid, "Neuere Version"), Ok)
    assert isinstance(world.workflow.apply_proposal(pid, proposal), Err)
    latest = world.workflow.open_project(pid)
    assert isinstance(latest, Ok) and latest.value.script == "Neuere Version"


def test_no_material_and_no_brief_cannot_pass_the_final_gate() -> None:
    world = build_world(FakeEvidence({}, {}))
    created = world.workflow.create_project(ProjectSettings("video", "Video", "", "generic", 2026))
    assert isinstance(created, Ok)
    gate = world.workflow.check_gate(created.value.project_id)
    assert isinstance(gate, Ok) and not gate.value.can_export
    assert {f.rule_id for f in gate.value.blocking} >= {
        "project.missing_brief",
        "project.missing_script",
        "project.missing_material",
    }


def test_historical_prices_and_descriptions_never_authorize_current_numbers() -> None:
    facts = (
        Fact("old", "Vorjahr", "49,99", "€", None, kind="historical_price"),
        Fact("note", "Notiz", "2019", "", None, kind="description"),
        Fact("current", "Aktuell", "89", "Schuss", None),
    )
    numbers, _dates = allowed_numbers(facts)
    assert numbers == {Fraction(89)}


def test_accepting_safe_alternative_changes_the_actual_idea() -> None:
    world = build_world(FakeEvidence({}, {}))
    created = world.workflow.create_project(
        ProjectSettings("video", "Video", "Lidl", "ranking", 2026)
    )
    assert isinstance(created, Ok)
    pid = created.value.project_id
    assert isinstance(
        world.workflow.extract_brief(
            pid,
            FIXTURE.read_text(encoding="utf-8"),
            ideas="Das gibt es bei keinem anderen Discounter.",
        ),
        Ok,
    )
    scanned = world.workflow.risk_panel(pid)
    assert isinstance(scanned, Ok)
    risk = next(r for r in scanned.value.risks if r.kind is ClaimKind.EXCLUSIVITY)
    decided = world.workflow.decide_risk(pid, risk.risk_id, RiskAction.REPLACED)
    assert isinstance(decided, Ok)
    assert decided.value.ideas == risk.safe_alternative
    assert "keinem anderen Discounter" not in decided.value.ideas


def test_live_lint_reads_unsaved_text_without_saving_it() -> None:
    world = build_world(FakeEvidence({}, {}))
    created = world.workflow.create_project(
        ProjectSettings("video", "Video", "Lidl", "ranking", 2026)
    )
    assert isinstance(created, Ok)
    pid = created.value.project_id
    assert isinstance(world.workflow.extract_brief(pid, FIXTURE.read_text(encoding="utf-8")), Ok)
    scripted = world.workflow.generate_script(pid)
    assert isinstance(scripted, Ok)
    text = "## S01 · ca. 0:00-0:10 · Einstieg\nSPRECHTEXT:\nDas kostet 9999,99 Euro."
    report = world.workflow.lint(pid, text=text)
    assert isinstance(report, Ok)
    assert any(f.rule_id == "numbers.price_not_in_fact_table" for f in report.value.findings)
    opened = world.workflow.open_project(pid)
    assert isinstance(opened, Ok) and opened.value.script == scripted.value.script


def test_a_context_test_cannot_bypass_a_flagged_project_asset() -> None:
    runner = FakeRunner()
    world = build_world(FakeEvidence({}, {}), runner=runner, timelines=FakeTimelines({}))
    created = world.workflow.create_project(
        ProjectSettings("video", "Video", "Lidl", "ranking", 2026)
    )
    assert isinstance(created, Ok)
    pid = created.value.project_id
    assert isinstance(world.workflow.extract_brief(pid, FIXTURE.read_text(encoding="utf-8")), Ok)
    assert isinstance(world.workflow.build_context(pid), Ok)
    flag = AssetFlag(
        FlagKind.CONFIDENTIAL, "Vertraulich", "Quelle", Severity.BLOCKER, (Resolution.APPROVED,)
    )
    entry = ManifestEntry(
        "01_cam/clip.mp4",
        "clip.mp4",
        "hash",
        "talking",
        "talking",
        (),
        (),
        "",
        (flag,),
        "own",
        "unchecked",
        source_path="C:/material/clip.mp4",
    )
    assert isinstance(
        world.projects.update(pid, lambda p: replace(p, manifest=Manifest(pid, "video", (entry,)))),
        Ok,
    )
    result = world.workflow.run_variant(
        pid, "normal", [Path("C:/material/clip.mp4")], JobContext.detached()
    )
    assert isinstance(result, Err)
    assert runner.calls == []
