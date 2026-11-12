"""Regression proof for reviewed edits, model grounding, and empty-project export."""

import json
from pathlib import Path

from media_house.modules.production_project.application.story_step import RewriteProposal
from media_house.modules.production_project.domain.project import ProjectSettings
from media_house.shared.concurrency import CancellationToken
from media_house.shared.errors import Err, Ok
from tests.unit.production_project.fakes import FakeEvidence, ScriptedGenerator
from tests.unit.production_project.world import build_world

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


def test_model_script_with_missing_products_is_repaired_then_falls_back_safely() -> None:
    response = json.dumps({"scenes": [
        {"scene_id": f"S{k:02d}", "title": "Start", "intent": "Test",
         "function": "hook" if k == 1 else "product", "graphics": "", "spoken": ["Ein Satz."]}
        for k in range(1, 4)
    ]})
    model = ScriptedGenerator([response])
    world = build_world(FakeEvidence({}, {}), model)
    created = world.workflow.create_project(ProjectSettings("video", "Video", "Lidl", "ranking", 2026))
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
        "project.missing_brief", "project.missing_script", "project.missing_material"
    }
