"""Brief to cut on the golden fixture: every wizard step through the workflow, real storage
codec, real folder and document adapters, fake model and fake analysis."""

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from media_house.core.domain import json_codec
from media_house.modules.production_project.application.views import spoken_script
from media_house.modules.production_project.application.workflow import ProductionWorkflowService
from media_house.modules.production_project.domain.claims import ClaimKind, RiskAction
from media_house.modules.production_project.domain.classify import FileEvidence
from media_house.modules.production_project.domain.compliance import TimelineFacts
from media_house.modules.production_project.domain.conform import TranscriptLine
from media_house.modules.production_project.domain.context import (
    Criterion,
    RunMeasurements,
    Score,
)
from media_house.modules.production_project.domain.manifest import Manifest
from media_house.modules.production_project.domain.project import Project, ProjectSettings
from media_house.modules.production_project.domain.values import (
    FlagKind,
    Resolution,
    SlotStatus,
)
from media_house.modules.style_memory.domain.values import Status
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok
from tests.unit.production_project.fakes import (
    FakeEvidence,
    FakeRunner,
    FakeTimelines,
    ScriptedGenerator,
)
from tests.unit.production_project.test_material_and_ingestion import LISTING, TALKING
from tests.unit.production_project.world import World, build_world

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"
CTX = JobContext.detached()
SETTINGS = ProjectSettings("lidlnico", "Lidl Feuerwerk", "Lidl", "ranking", 2026)


def ok(result: object) -> Any:
    """The value of an ``Ok`` (the test fails with the error otherwise)."""
    assert isinstance(result, Ok), result
    return result.value


def start(world: World) -> Project:
    project: Project = ok(world.workflow.create_project(SETTINGS))
    ok(world.workflow.extract_brief(project.project_id, FIXTURE.read_text(encoding="utf-8")))
    opened: Project = ok(world.workflow.open_project(project.project_id))
    return opened


def no_evidence() -> FakeEvidence:
    return FakeEvidence({}, {})


def test_the_brief_becomes_a_model_facts_and_a_technik_form() -> None:
    world = build_world(no_evidence())
    project = start(world)

    assert project.brief is not None and project.brief.client == "Lidl"
    assert len(project.slots) == 25 and not any(s.filled for s in project.slots)
    assert {f.key for f in project.facts} >= {"price_total", "price_spread", "order_window"}
    assert project.generated[0].generator == "deterministic"
    assert project.revision > 1  # every step is a saved revision


def test_the_risk_panel_replaces_or_keeps_with_a_reason() -> None:
    world = build_world(no_evidence())
    project = start(world)
    ideas = (
        "Das gibt es bei keinem anderen Discounter. "
        "Ich zeige die Prospektseite mit den Vorschau-Preisen im Bild."
    )
    ok(
        world.workflow.extract_brief(
            project.project_id, FIXTURE.read_text(encoding="utf-8"), ideas=ideas
        )
    )

    risks = ok(world.workflow.risk_panel(project.project_id)).risks

    kinds = {r.kind for r in risks}
    assert {ClaimKind.EXCLUSIVITY, ClaimKind.CONFIDENTIAL_SHOWN} <= kinds
    excl = next(r for r in risks if r.kind is ClaimKind.EXCLUSIVITY)
    assert isinstance(
        world.workflow.decide_risk(project.project_id, excl.risk_id, RiskAction.KEPT), Err
    )
    replaced = ok(world.workflow.decide_risk(project.project_id, excl.risk_id, RiskAction.REPLACED))
    assert replaced.decisions[0].replacement == excl.safe_alternative
    conf = next(r for r in risks if r.kind is ClaimKind.CONFIDENTIAL_SHOWN)
    kept = ok(
        world.workflow.decide_risk(
            project.project_id,
            conf.risk_id,
            RiskAction.KEPT,
            reason="Der Kunde hat die Seite schriftlich freigegeben.",
            remember_for_client=True,
        )
    )
    assert {d.action for d in kept.decisions} == {RiskAction.REPLACED, RiskAction.KEPT}
    learned = [e for e in world.style_store.list_events() if e.kind.value == "conflict_override"]
    assert learned and learned[0].client == "Lidl" and learned[0].note.startswith("Der Kunde")


def test_three_concepts_a_script_of_seventeen_scenes_and_the_live_lint() -> None:
    world = build_world(no_evidence())
    project = start(world)
    pid = project.project_id

    concepts = ok(world.workflow.brainstorm(pid)).concepts
    assert len(concepts) == 3 and len({c.logline for c in concepts}) == 3
    assert all(c.evidence and len(c.titles) == 3 for c in concepts)
    ok(world.workflow.choose_concept(pid, concepts[0].concept_id, why="Gefällt mir"))
    assert any(e.kind.value == "concept_picked" for e in world.style_store.list_events())

    scripted = ok(world.workflow.generate_script(pid))
    spoken = spoken_script(scripted)
    assert len(spoken.scenes) == 17 and "AUFHAENGER" in " ".join(s.key for s in scripted.slots)
    before = ok(world.workflow.lint(pid))
    assert "slots.unfilled" in {f.rule_id for f in before.findings}

    for slot in scripted.slots:
        ok(world.workflow.fill_slot(pid, slot.key, "x" if not slot.unit else "30"))
    after = ok(world.workflow.lint(pid))
    assert "slots.unfilled" not in {f.rule_id for f in after.findings}
    assert after.seconds > 60 and len(after.lengths) == 17


def test_every_edit_marks_what_was_written_down_dirty_and_the_script_is_restorable() -> None:
    world = build_world(no_evidence())
    pid = start(world).project_id
    ok(world.workflow.generate_script(pid))
    first = ok(world.workflow.open_project(pid))
    ok(world.workflow.plan_material(pid))

    edited = ok(world.workflow.edit_script(pid, first.script.replace("Einstieg", "Start")))
    assert "Start" in edited.script and "requirements" in {a.value for a in edited.dirty}
    restored = ok(world.workflow.restore_script(pid, 1))
    assert restored.script == first.script and len(restored.script_versions) == 3
    changed = ok(world.workflow.set_fact(pid, "price_raketenset-orion", "10,99"))
    assert {"metadata", "context"} <= {a.value for a in changed.dirty}
    assert not next(f for f in changed.facts if f.key == "price_raketenset-orion").verified
    verified = ok(
        world.workflow.verify_fact(
            pid, "price_raketenset-orion", "Shop S. 2", "Raketenset Orion 10,99 €"
        )
    )
    assert next(f for f in verified.facts if f.key == "price_raketenset-orion").verified
    assert isinstance(world.workflow.verify_fact(pid, "price_raketenset-orion", "", ""), Err)


def test_a_model_answer_is_validated_repaired_and_the_scaffold_catches_a_failure() -> None:
    ideas = (
        ("Countdown durch alle Feuerwerksartikel", "Platz fünf eröffnet die Show"),
        ("Ein ganzer Morgen im Alltag mit dem Wecker", "Der Wecker klingelt, und dann knallt es"),
        ("Welches Set landet im Einkaufswagen", "Eine Frage, die niemand sofort beantwortet"),
    )
    good = json.dumps(
        {
            "concepts": [
                {
                    "logline": logline,
                    "hook": hook,
                    "red_thread": "Faden",
                    "retention": ["Zähler", "Leiter"],
                    "callback": "Zurück",
                    "why_you": "Weil du ...",
                    "evidence": ["video:t_BTkcPy5sw"],
                    "titles": ["A", "B", "C"],
                }
                for logline, hook in ideas
            ]
        }
    )
    generator = ScriptedGenerator(["das ist kein json", good])
    world = build_world(no_evidence(), generator)
    pid = start(world).project_id

    from media_house.shared.concurrency import CancellationToken

    concepts = ok(world.workflow.brainstorm(pid, cancellation=CancellationToken())).concepts

    assert [c.concept_id for c in concepts] == ["llm-1", "llm-2", "llm-3"]
    assert len(generator.prompts) == 2 and "ungültig" in generator.prompts[1][1]
    broken = ScriptedGenerator(["nope"])
    world2 = build_world(no_evidence(), broken)
    pid2 = start(world2).project_id
    fallback = ok(world2.workflow.brainstorm(pid2, cancellation=CancellationToken())).concepts
    assert [c.concept_id for c in fallback] == ["det-1", "det-2", "det-3"]


def test_a_confidential_project_never_sends_text_to_a_remote_model() -> None:
    remote = ScriptedGenerator(["{}"], identity="openai-compatible-chat@api.example.test")
    world = build_world(no_evidence(), remote)
    pid = start(world).project_id

    from media_house.shared.concurrency import CancellationToken

    refused = world.workflow.brainstorm(pid, cancellation=CancellationToken())

    assert isinstance(refused, Err) and "vertraulich" in refused.error.user_message
    assert remote.prompts == []


def make_upload(root: Path) -> None:
    for name, folder in (
        *((e.name, "01_cam") for e in TALKING),
        ("reaktion_wecker_2.mp4", "01_cam"),
        ("wecker_gag_1.mp4", "01_cam"),
        ("aufnahme_pc.mp4", "02_scr"),
        ("shop.png", "02_scr"),
        ("raketenset-orion.png", "03_pck"),
        ("prospekt_seite_03.png", "03_pck"),
        ("lidl_letztes_jahr_überblick.MP4", "04_arc"),
        ("pexels-fireworks-night-sky-slow-motion-4k-uhd-1234567.mp4", "05_bg"),
        ("intro.mp4", "06_gfx"),
        ("outro.mp4", "06_gfx"),
    ):
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / name).write_bytes(name.encode())
    (root / "06_gfx").mkdir(exist_ok=True)
    from PIL import Image

    Image.new("RGBA", (8, 8), (255, 0, 0, 128)).save(
        root / "06_gfx" / "lidl_logo.svg.webp", format="WEBP"
    )
    (root / "07_thumb").mkdir(exist_ok=True)


@pytest.fixture
def full(tmp_path: Path) -> tuple[World, str, Path]:
    """A project with script, filled slots, plan and the golden upload ingested."""
    probe = build_world(no_evidence())
    pid = start(probe).project_id
    workflow = probe.workflow
    ok(workflow.brainstorm(pid))
    ok(workflow.choose_concept(pid, "det-1"))
    scripted = ok(workflow.generate_script(pid))
    for slot in scripted.slots:
        ok(workflow.fill_slot(pid, slot.key, f"Testwert {slot.key.lower()}"))
    ok(workflow.plan_material(pid))
    project = ok(workflow.open_project(pid))
    spoken = spoken_script(project)
    sentences = [x for s in spoken.scenes if s.scene_id != "S12" for x in s.sentences if x.usable]
    names = [e.name for e in TALKING]
    per = -(-len(sentences) // len(names))
    lines: dict[str, list[TranscriptLine]] = {n: [] for n in names}
    for i, sentence in enumerate(sentences):
        n = names[i // per]
        lines[n].append(TranscriptLine(n, (i % per) * 4.0, (i % per) * 4.0 + 3.0, sentence.text))
    evidence = FakeEvidence(
        {
            **{e.name: e for e in TALKING},
            **{e.name: e for e in LISTING.values()},
            "outro.mp4": FileEvidence("outro.mp4", "video", duration=5.0),
        },
        {n: tuple(v) for n, v in lines.items()},
    )
    world = build_world(evidence)
    # the new world has its own (empty) store: replay the project into it through the codec
    world.store.create(project)
    root = tmp_path / "upload"
    make_upload(root)
    return world, pid, root


def test_the_golden_upload_is_classified_named_flagged_and_reconciled(
    full: tuple[World, str, Path],
) -> None:
    world, pid, root = full

    report = ok(world.workflow.ingest(pid, root, CTX))

    manifest = report.project.manifest
    assert manifest is not None
    by_original = {e.original_name: e for e in manifest.entries}
    assert len(manifest.entries) == 14 + 11
    assert by_original["talking_1.mp4"].file == "01_cam/lidlnico_talking_01.mp4"
    assert by_original["talking_10.mp4"].file == "01_cam/lidlnico_talking_10.mp4"  # natural order
    assert by_original["lidl_logo.svg.webp"].file == "06_gfx/lidlnico_logo_logo.png"
    assert "Doppelte" in by_original["lidl_logo.svg.webp"].normalization
    assert (
        by_original["raketenset-orion.png"].file == "03_pck/lidlnico_packshot_raketenset-orion.png"
    )
    assert (
        by_original["intro.mp4"].slot == "brand-intro"
        and by_original["outro.mp4"].slot == "brand-outro"
    )
    assert report.ignored == ("07_thumb (leer)",) or "07_thumb" in report.ignored[0]
    stock = by_original["pexels-fireworks-night-sky-slow-motion-4k-uhd-1234567.mp4"]
    assert stock.license_state == "unknown" and not stock.usable
    prospect = by_original["prospekt_seite_03.png"]
    assert FlagKind.CONFIDENTIAL in {f.kind for f in prospect.flags}
    assert report.reconciliation.messages[0] == (
        "Du hast 14 Talking-Dateien statt 1 hochgeladen, ich habe sie den Szenen zugeordnet. "
        "Szene S12 fehlt noch."
    )
    talking = next(r for r in report.reconciliation.requirements if r.requirement_id == "talking")
    assert talking.status is SlotStatus.PARTIAL
    assert report.flagged >= 3  # stock, prospect, archive faces/brands, private data on the screen


def test_flagged_files_stay_out_until_a_person_settles_them(full: tuple[World, str, Path]) -> None:
    world, pid, root = full
    report = ok(world.workflow.ingest(pid, root, CTX))
    manifest = report.project.manifest
    assert manifest is not None
    usable_before = len(manifest.usable_entries())

    for entry in manifest.flagged():
        for flag in entry.flags:
            how = (
                Resolution.REPLACED
                if Resolution.REPLACED in flag.options and Resolution.APPROVED not in flag.options
                else Resolution.APPROVED
            )
            ok(
                world.workflow.settle_flag(
                    pid, entry.file, flag.kind, how, "geprüft, Freigabe liegt vor"
                )
            )

    after = ok(world.workflow.open_project(pid)).manifest
    assert after is not None and after.flagged() == ()
    assert len(after.usable_entries()) > usable_before
    assert isinstance(
        world.workflow.settle_flag(
            pid, manifest.entries[0].file, FlagKind.LICENSE_UNKNOWN, Resolution.APPROVED, ""
        ),
        Err,
    )


def test_context_variants_ab_run_and_the_winner(full: tuple[World, str, Path]) -> None:
    world, pid, _root = full
    built = ok(world.workflow.build_context(pid))

    sizes = {v.level: v.word_count for v in built.contexts}
    assert (
        40 <= sizes["minimal"] <= 75
        and 130 <= sizes["normal"] <= 215
        and 330 <= sizes["detailed"] <= 520
    )
    assert all("Faktentabelle" in v.text for v in built.contexts)
    assert isinstance(world.workflow.choose_context(pid), Err)  # nothing run yet

    measured = {
        "minimal": RunMeasurements(8.0, 0.7, 1.0, 4, 3.0, 0.7, 2),
        "normal": RunMeasurements(4.5, 0.92, 1.0, 3, 5.0, 0.96, 0),
        "detailed": RunMeasurements(5.5, 0.85, 1.0, 3, 5.5, 0.9, 1),
    }
    for level, m in measured.items():
        ok(
            world.workflow.record_evaluation(
                pid, level, "excerpt", m, [Score(Criterion.GRAPHICS, 4.0, "manual")]
            )
        )
    chosen = ok(world.workflow.choose_context(pid))
    assert chosen.context_winner == "normal"
    edited = ok(world.workflow.edit_context(pid, "normal", "Mein eigener Text zum Test."))
    assert next(v for v in edited.contexts if v.level == "normal").edited
    candidates = (
        [r for r in world.style_store.list() if r.status is Status.CANDIDATE]
        if hasattr(world.style_store, "list")
        else []
    )
    assert candidates == []


def test_the_gate_blocks_until_everything_is_settled_and_then_exports_the_package(
    full: tuple[World, str, Path], tmp_path: Path
) -> None:
    world, pid, root = full
    workflow: ProductionWorkflowService = world.workflow
    report = ok(workflow.ingest(pid, root, CTX))
    manifest = report.project.manifest
    assert manifest is not None
    ok(workflow.build_context(pid))
    ok(workflow.build_metadata(pid))

    gate = ok(workflow.check_gate(pid))
    assert not gate.can_export
    assert any(f.stage.value == "asset" for f in gate.blocking)
    assert any(f.rule_id == "metadata.tracking_link" for f in gate.blocking)
    refused = workflow.export_package(pid, tmp_path / "out", CTX)
    assert isinstance(refused, Err) and "gesperrt" in refused.error.user_message

    for entry in manifest.flagged():
        for flag in entry.flags:
            how = (
                Resolution.REPLACED
                if flag.options == (Resolution.REPLACED,)
                else Resolution.APPROVED
            )
            ok(workflow.settle_flag(pid, entry.file, flag.kind, how, "Freigabe liegt vor"))
    ok(workflow.build_metadata(pid, tracking_link="https://example.test/lidl?track=1"))
    gate = ok(workflow.check_gate(pid))
    assert gate.can_export, [f.message for f in gate.blocking]

    out = tmp_path / "out"
    exported = ok(workflow.export_package(pid, out, CTX))

    assert (out / "00_kontext" / "lidlnico-transcript.md").is_file()
    assert (out / "pipeline-context.md").read_text(encoding="utf-8").startswith("Thema:")
    assert (out / "01_cam" / "lidlnico_talking_01.mp4").is_file()
    assert (out / "06_gfx" / "lidlnico_logo_logo.png").read_bytes()[:4] == b"\x89PNG"
    assert not any("07_thumb" in f or "thumb" in f for f in exported.files)
    assert (out / "kurzkonzept.pdf").read_bytes().startswith(b"%PDF-")
    with zipfile.ZipFile(out / "rueckfragen.docx") as z:
        assert "Rückfragen" in z.read("word/document.xml").decode("utf-8")
    manifest_doc = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest_doc.pop("document_type")
    assert json_codec.decode(Manifest, manifest_doc).flagged() == ()
    meta = json.loads((out / "metadaten.json").read_text(encoding="utf-8"))
    assert meta["hashtags"][0] == "#LidlFeuerwerk" and len(meta["titles"]) >= 1
    assert "https://example.test/lidl?track=1" in meta["description"]
    assert "run_pipeline.py --project" in (out / "README-handoff.md").read_text(encoding="utf-8")
    assert "Thumbnail" not in " ".join(exported.files)


def test_a_variant_runs_the_pipeline_and_the_built_timeline_is_measured() -> None:
    timeline = TimelineFacts(
        duration_seconds=600.0,
        label_spans=((0.0, 300.0),),  # the label is gone for the second half: a blocker
        transitions=("cut",),
        max_simultaneous_overlays=2,
        used_assets=(),
        on_screen_texts=(),
        cuts=50,
    )
    runner = FakeRunner()
    world = build_world(
        no_evidence(), runner=runner, timelines=FakeTimelines({"anchor-1": timeline})
    )
    project = start(world)
    pid = project.project_id
    ok(world.workflow.build_context(pid))

    ran = ok(world.workflow.run_variant(pid, "normal", [Path("a.mp4")], CTX))

    (paths, text, shape) = runner.calls[0]
    assert paths == (Path("a.mp4"),) and shape == "16:9"
    assert text == next(v.text for v in ran.contexts if v.level == "normal")
    run = next(r for r in ran.eval_runs if r.variant == "normal")
    scores = {s.criterion: s for s in run.scores}
    assert scores[Criterion.RHYTHM].method == "auto"
    assert "5.0 Schnitte/min" in scores[Criterion.RHYTHM].note
    assert scores[Criterion.RULES].value < 5.0  # the missing label counted
    assert Criterion.HOOK not in scores  # not measurable: left to the creator


def test_a_variant_run_fails_plainly_without_a_variant_a_media_file_or_a_timeline() -> None:
    world = build_world(no_evidence(), runner=FakeRunner(), timelines=FakeTimelines({}))
    pid = start(world).project_id

    assert isinstance(world.workflow.run_variant(pid, "normal", [Path("a.mp4")], CTX), Err)
    ok(world.workflow.build_context(pid))
    assert isinstance(world.workflow.run_variant(pid, "nope", [Path("a.mp4")], CTX), Err)
    failed = build_world(no_evidence(), runner=FakeRunner(fail=True), timelines=FakeTimelines({}))
    fid = start(failed).project_id
    ok(failed.workflow.build_context(fid))
    assert isinstance(failed.workflow.run_variant(fid, "normal", [Path("a.mp4")], CTX), Err)
    assert isinstance(failed.workflow.attach_timeline(fid, "x"), Err)
    assert isinstance(build_world(no_evidence()).workflow.run_variant("p", "normal", [], CTX), Err)
