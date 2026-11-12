"""The golden upload: a plan from the script, then the real folder listing classified, named,
validated and reconciled (all evidence synthetic)."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from media_house.modules.production_project.domain.assets import (
    ValidationContext,
    settle,
    usable,
    validate_asset,
)
from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.brief_parser import parse_brief
from media_house.modules.production_project.domain.classify import (
    FileEvidence,
    assign_slot,
    classify,
)
from media_house.modules.production_project.domain.compliance import compile_rules
from media_house.modules.production_project.domain.conform import TranscriptLine, conform
from media_house.modules.production_project.domain.facts import (
    derive,
    products,
    standard_formulas,
)
from media_house.modules.production_project.domain.manifest import Manifest, ManifestEntry
from media_house.modules.production_project.domain.material import (
    AssetRequirement,
    PlanInputs,
    plan_materials,
)
from media_house.modules.production_project.domain.naming import (
    natural_sorted,
    normalize,
    target_name,
)
from media_house.modules.production_project.domain.reconcile import (
    AssignedFile,
    CoverageState,
    reconcile,
)
from media_house.modules.production_project.domain.scaffold import ScaffoldInputs, scaffold_script
from media_house.modules.production_project.domain.script import Script
from media_house.modules.production_project.domain.script_tools import resolve_script
from media_house.modules.production_project.domain.slots import fill, fill_values, standard_slots
from media_house.modules.production_project.domain.values import (
    FlagKind,
    MultiFile,
    Priority,
    Resolution,
    Role,
    SlotStatus,
    Timing,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


@pytest.fixture(scope="module")
def brief() -> BriefModel:
    return parse_brief(FIXTURE.read_text(encoding="utf-8"), reference_year=2026)


@pytest.fixture(scope="module")
def script(brief: BriefModel) -> Script:
    rows = products(brief.facts)
    raw = scaffold_script(
        ScaffoldInputs("Test", rows, brief.facts, brief, None, "abonnieren, liken", True)
    )
    slots = standard_slots(rows)
    for s in list(slots):
        slots = fill(slots, s.key, "x")
    facts = derive(brief.facts, standard_formulas(brief.facts))
    return resolve_script(raw, slots=fill_values(slots), facts=facts)


def test_the_scaffold_script_has_about_seventeen_scenes_named_by_function(script: Script) -> None:
    assert len(script.scenes) == 17
    assert [s.function for s in script.scenes[:3]] == ["hook", "context", "facts"]
    assert script.scenes[-1].function == "outro"
    assert {s.scene_id for s in script.scenes if s.function == "product"} == {
        "S04",
        "S05",
        "S06",
        "S07",
        "S08",
    }
    assert "Das Versprechen" not in " ".join(s.heading for s in script.scenes)
    assert any("OPTIONAL" in s.tags for s in script.scenes)


def plan(
    script: Script,
    brief: BriefModel,
    *,
    off_blocks: frozenset[str] = frozenset(),
) -> tuple[AssetRequirement, ...]:
    return plan_materials(
        PlanInputs(
            "lidlnico",
            script,
            brief,
            compile_rules(brief),
            products(brief.facts),
            off_blocks=off_blocks,
        )
    )


def test_the_planner_asks_for_the_fewest_files_in_a_recording_order(
    script: Script, brief: BriefModel
) -> None:
    reqs = plan(script, brief)
    by_id = {r.requirement_id: r for r in reqs}

    assert 13 <= len(reqs) <= 17
    talking = by_id["talking"]
    assert talking.multi_file is MultiFile.ORDERED_CHUNKS and talking.priority is Priority.REQUIRED
    assert talking.depends_on == ("fact-check", "slots-filled")
    assert talking.target_name_pattern == "lidlnico_talking_{nn}.mp4"
    assert len([r for r in reqs if r.role is Role.SCREENREC]) == 1
    assert len([r for r in reqs if r.role is Role.GAGS]) == 1
    assert len([r for r in reqs if r.role is Role.PACKSHOT]) == 5
    assert by_id["screenrec"].timing is Timing.AFTER_SOURCE_LIVE
    assert [r.order for r in reqs] == list(range(1, len(reqs) + 1))
    assert (
        by_id["screenrec"].order > by_id["talking"].order > by_id["packshot-raketenset-orion"].order
    )
    # raw footage and pictures only: never music or sound effects
    assert not any("musik" in (r.title + r.instruction).lower() for r in reqs)
    assert "sichtbare Fremdmarken" in " ".join(talking.forbidden)
    assert by_id["packshot-raketenset-orion"].target_name_pattern == (
        "lidlnico_packshot_raketenset-orion.png"
    )


def test_switching_an_optional_block_off_removes_nothing_the_video_still_needs(
    script: Script, brief: BriefModel
) -> None:
    on = {r.requirement_id for r in plan(script, brief)}
    off = {r.requirement_id for r in plan(script, brief, off_blocks=frozenset({"spedition"}))}

    assert off <= on
    talking = next(
        r
        for r in plan(script, brief, off_blocks=frozenset({"spedition"}))
        if r.requirement_id == "talking"
    )
    assert "S12" not in talking.scene_ids and "S11" in talking.scene_ids


def test_the_archive_needs_the_approval_the_brief_asks_for(
    script: Script, brief: BriefModel
) -> None:
    archive_script = replace(
        script,
        scenes=(
            replace(script.scenes[0], graphics_intent="Archivaufnahme älterer Prospekte"),
            *script.scenes[1:],
        ),
    )
    archive = next(r for r in plan(archive_script, brief) if r.role is Role.ARCHIVE)

    assert archive.priority is Priority.OPTIONAL
    assert archive.depends_on == (f"approval:{archive.switched_by}",)
    assert archive.scene_ids == (script.scenes[0].scene_id,)


def test_an_archive_permission_alone_does_not_request_unused_material(
    script: Script, brief: BriefModel
) -> None:
    assert not any(r.role is Role.ARCHIVE for r in plan(script, brief))


def test_natural_order_and_names() -> None:
    names = [f"talking_{n}.mp4" for n in (1, 10, 2, 9, 11, 3)]

    assert natural_sorted(names) == [f"talking_{n}.mp4" for n in (1, 2, 3, 9, 10, 11)]
    assert (
        target_name("Lidl Nico", Role.TALKING, index=1, label="", ext="MP4")
        == "lidl-nico_talking_01.mp4"
    )
    assert (
        target_name("lidlnico", Role.PACKSHOT, index=None, label="Seven Kings", ext="png")
        == "lidlnico_packshot_seven-kings.png"
    )
    n = normalize("lidl_logo.svg.webp")
    assert (n.stem, n.ext, n.convert_to) == ("lidl_logo", "webp", "png") and "Doppelte" in n.reason
    assert normalize("logo.svg", is_vector=True).convert_to == ""
    assert normalize("lidl_letztes_jahr_überblick.MP4").ext == "mp4"


def ev(name: str, kind: str = "video", **kw: Any) -> FileEvidence:
    return FileEvidence(name, kind, sha256=f"h-{name}", **kw)


TALKING = [
    ev(
        f"talking_{n}.mp4",
        folder="01_cam",
        duration=40.0,
        speech_words=110,
        faces=1,
        has_audio=True,
        width=3840,
        height=2160,
        fps=25.0,
    )
    for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14)
]

LISTING: dict[str, FileEvidence] = {
    "reaction": ev("reaktion_wecker_2.mp4", folder="01_cam", duration=6.0, speech_words=0, faces=1),
    "gag": ev("wecker_gag_1.mp4", folder="01_cam", duration=9.6, speech_words=0),
    "screen": ev(
        "aufnahme_pc.mp4",
        folder="02_scr",
        duration=95.0,
        tags=("browser_ui", "cursor"),
        ocr_text="https://shop.example.test/feuerwerk anna.muster@example.test",
    ),
    "shot": ev("shop.png", "image", folder="02_scr", width=1440, height=5200, tags=("browser_ui",)),
    "packshot": ev(
        "raketenset-orion.png",
        "image",
        folder="03_pck",
        width=900,
        height=1100,
        has_alpha=True,
        tags=("product",),
    ),
    "prospect": ev(
        "prospekt_seite_03.png",
        "image",
        folder="03_pck",
        width=1400,
        height=1900,
        tags=("brochure",),
        ocr_text="Prospektseite Vorschau Preise nur intern",
    ),
    "archive": ev(
        "lidl_letztes_jahr_überblick.MP4",
        folder="04_arc",
        duration=70.0,
        faces=4,
        tags=("crowd",),
        ocr_text="Weco Aldi",
    ),
    "stock": ev(
        "pexels-fireworks-night-sky-slow-motion-4k-uhd-1234567.mp4",
        folder="05_bg",
        duration=300.0,
        tags=("night_sky", "fireworks"),
        faces=0,
    ),
    "logo": ev(
        "lidl_logo.svg.webp", "image", folder="06_gfx", width=600, height=600, has_alpha=True
    ),
    "intro": ev("intro.mp4", folder="06_gfx", duration=4.0),
}


def test_every_file_of_the_listing_gets_the_right_role() -> None:
    expected = {
        "reaction": Role.GAGS,
        "gag": Role.GAGS,
        "screen": Role.SCREENREC,
        "shot": Role.SCREENSHOT,
        "packshot": Role.PACKSHOT,
        "archive": Role.ARCHIVE,
        "stock": Role.BACKGROUND,
        "logo": Role.LOGO,
        "intro": Role.BRAND,
    }
    for key, role in expected.items():
        result = classify(LISTING[key])
        assert result.role is role, (key, result)
        assert result.confidence > 0.3 and result.reasons
    assert all(classify(e).role is Role.TALKING for e in TALKING)
    assert classify(ev("x.dat", "other")).role is None


def test_a_file_date_or_name_alone_never_decides() -> None:
    plain = ev("clip.mp4", duration=300.0)

    assert classify(plain).role is None or classify(plain).confidence < 0.5


def test_the_validators_flag_what_the_brief_forbids(brief: BriefModel, script: Script) -> None:
    archive_script = replace(
        script,
        scenes=(
            replace(script.scenes[0], graphics_intent="Archivaufnahme älterer Prospekte"),
            *script.scenes[1:],
        ),
    )
    reqs = {r.requirement_id: r for r in plan(archive_script, brief)}
    ctx = ValidationContext(compile_rules(brief), brief)

    stock = validate_asset(LISTING["stock"], Role.BACKGROUND, reqs["background"], ctx)
    prospect = validate_asset(
        LISTING["prospect"], Role.PACKSHOT, reqs["packshot-raketenset-orion"], ctx
    )
    screen = validate_asset(LISTING["screen"], Role.SCREENREC, reqs["screenrec"], ctx)
    archive = validate_asset(LISTING["archive"], Role.ARCHIVE, reqs["archive"], ctx)

    assert [f.kind for f in stock] == [FlagKind.LICENSE_UNKNOWN]
    assert FlagKind.CONFIDENTIAL in {f.kind for f in prospect}
    assert FlagKind.PRIVATE_DATA in {f.kind for f in screen}
    kinds = {f.kind for f in archive}
    assert {FlagKind.BYSTANDER_FACES, FlagKind.THIRD_PARTY_BRAND} <= kinds
    brand = next(f for f in archive if f.kind is FlagKind.THIRD_PARTY_BRAND)
    assert "Prospekten" in brand.message and brand.severity.value == "warning"
    assert not usable(stock)


def test_a_flag_is_settled_by_a_person_and_the_file_becomes_usable(
    brief: BriefModel, script: Script
) -> None:
    reqs = {r.requirement_id: r for r in plan(script, brief)}
    flags = validate_asset(
        LISTING["stock"],
        Role.BACKGROUND,
        reqs["background"],
        ValidationContext(compile_rules(brief), brief),
    )

    settled = settle(
        flags, FlagKind.LICENSE_UNKNOWN, Resolution.APPROVED, "Lizenz vom 3.11. liegt vor"
    )

    assert usable(settled) and settled[0].note.startswith("Lizenz")
    licensed = validate_asset(
        LISTING["stock"],
        Role.BACKGROUND,
        reqs["background"],
        ValidationContext(
            compile_rules(brief), brief, licensed=frozenset({"h-" + LISTING["stock"].name})
        ),
    )
    assert licensed == ()


def test_a_packshot_goes_to_its_product(brief: BriefModel, script: Script) -> None:
    reqs = plan(script, brief)

    slot, why = assign_slot(LISTING["packshot"], Role.PACKSHOT, reqs)

    assert slot == "packshot-raketenset-orion" and "Produktname" in why
    assert assign_slot(ev("irgendwas.png", "image"), Role.PACKSHOT, reqs)[0] == ""


def test_fourteen_chunks_are_laid_onto_the_scenes_and_the_missing_scene_is_named(
    brief: BriefModel, script: Script
) -> None:
    reqs = plan(script, brief)
    spoken = [s for s in script.scenes if s.sentences and any(x.usable for x in s.sentences)]
    dropped = "S12"  # the creator forgot this scene
    sentences = [x for s in spoken if s.scene_id != dropped for x in s.sentences if x.usable]
    chunks = [f"talking_{n}.mp4" for n in range(1, 15)]
    per = -(-len(sentences) // len(chunks))
    lines = [
        TranscriptLine(chunks[i // per], (i % per) * 4.0, (i % per) * 4.0 + 3.0, x.text)
        for i, x in enumerate(sentences)
    ]
    # a retake: scene S01 said twice, the second one is the last complete take
    lines.append(TranscriptLine(chunks[-1], 80.0, 83.0, sentences[0].text))

    order = natural_sorted(chunks)
    result = conform(script, lines, order)
    files = [
        AssignedFile(f"lidlnico_talking_{i + 1:02d}.mp4", "talking", Role.TALKING, c)
        for i, c in enumerate(order)
    ]
    rec = reconcile(reqs, files, script, result, order)

    assert rec.missing_scenes == (dropped,)
    talking = next(r for r in rec.requirements if r.requirement_id == "talking")
    assert talking.status is SlotStatus.PARTIAL
    assert rec.messages[0] == (
        "Du hast 14 Talking-Dateien statt 1 hochgeladen, ich habe sie den Szenen zugeordnet. "
        "Szene S12 fehlt noch."
    )
    first = next(c for c in rec.coverage if c.scene_id == "S01")
    assert first.state is CoverageState.MULTIPLE and first.chosen is not None
    # the last complete take wins; the earlier one stays as an alternative
    assert first.chosen.asset_id == order[-1]
    assert [t.asset_id for t in first.alternatives] == [order[0]]
    assert "packshot-raketenset-orion" in rec.missing_required


def test_everything_filled_gives_no_missing_scene_and_filled_status(
    brief: BriefModel, script: Script
) -> None:
    reqs = plan(script, brief)
    sentences = [x for s in script.scenes for x in s.sentences if x.usable]
    lines = [
        TranscriptLine("one.mp4", i * 4.0, i * 4.0 + 3.0, x.text) for i, x in enumerate(sentences)
    ]

    rec = reconcile(
        reqs,
        [AssignedFile("lidlnico_talking_01.mp4", "talking", Role.TALKING, "one.mp4")],
        script,
        conform(script, lines, ["one.mp4"]),
        ["one.mp4"],
    )

    assert rec.missing_scenes == ()
    assert (
        next(r for r in rec.requirements if r.requirement_id == "talking").status
        is SlotStatus.FILLED
    )
    assert rec.messages[0] == "Dein Talking-Take ist zugeordnet. Alle Szenen sind abgedeckt."


def test_flagged_files_are_not_in_the_usable_manifest() -> None:
    from media_house.modules.production_project.domain.assets import AssetFlag
    from media_house.modules.production_project.domain.values import Severity

    flag = AssetFlag(FlagKind.LICENSE_UNKNOWN, "m", "e", Severity.WARNING, (Resolution.APPROVED,))
    ok = ManifestEntry(
        "a.mp4", "A.mp4", "h1", "talking", "talking", ("S01",), (), "", (), "own", "checked"
    )
    bad = ManifestEntry(
        "b.mp4",
        "B.mp4",
        "h2",
        "background",
        "background",
        (),
        (),
        "",
        (flag,),
        "unknown",
        "unchecked",
    )
    manifest = Manifest("p", "lidlnico", (ok, bad), ignored=("07_thumb",))

    assert [e.file for e in manifest.usable_entries()] == ["a.mp4"]
    assert [e.file for e in manifest.flagged()] == ["b.mp4"]
