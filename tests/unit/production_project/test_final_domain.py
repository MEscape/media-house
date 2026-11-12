"""Timeline QC, metadata, context scoring and the schedule: the last mile of a project."""

from datetime import date
from pathlib import Path

import pytest

from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.brief_parser import parse_brief
from media_house.modules.production_project.domain.claims import ScanContext
from media_house.modules.production_project.domain.compliance import (
    TimelineFacts,
    compile_rules,
    lint_metadata,
    qc_timeline,
    tag_length,
)
from media_house.modules.production_project.domain.concepts import (
    StoryConcept,
    distinct,
    mix,
    problems,
)
from media_house.modules.production_project.domain.context import (
    LEVELS,
    Criterion,
    EvalRun,
    RunMeasurements,
    Score,
    auto_scores,
    learned_best_level,
    merge_ratings,
    total,
    winner,
)
from media_house.modules.production_project.domain.documents import schedule
from media_house.modules.production_project.domain.facts import derive, products, standard_formulas
from media_house.modules.production_project.domain.metadata import (
    MetadataInputs,
    build_metadata,
    clock,
)
from media_house.modules.production_project.domain.scaffold import ScaffoldInputs, scaffold_script
from media_house.modules.production_project.domain.script_tools import estimate_lengths
from tests.support.fakes import T0

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "brief_to_cut" / "lidl_brief.txt"


@pytest.fixture(scope="module")
def brief() -> BriefModel:
    return parse_brief(FIXTURE.read_text(encoding="utf-8"), reference_year=2026)


def qc(brief: BriefModel, facts: TimelineFacts, **kw: object) -> set[str]:
    args: dict[str, object] = {
        "allowed_transitions": ("hard_cut", "zoom_tunnel"),
        "forbidden_transitions": ("glitch",),
        "overlay_limit": 3,
        "flagged_assets": frozenset(),
        "forbidden_words": (),
    }
    args.update(kw)
    return {f.rule_id for f in qc_timeline(facts, compile_rules(brief), brief=brief, **args)}  # type: ignore[arg-type]


def test_the_timeline_qc_checks_the_label_transitions_overlays_assets_and_words(
    brief: BriefModel,
) -> None:
    good = TimelineFacts(600.0, ((0.0, 600.0),), ("hard_cut",), 2, ("a1",), ("Preis 9,99 €",))
    assert qc(brief, good) == set()

    gap = TimelineFacts(600.0, ((0.0, 100.0), (130.0, 600.0)), ("hard_cut",), 2, (), ())
    assert qc(brief, gap) == {"timeline.label_missing"}
    short = TimelineFacts(600.0, ((0.0, 590.0),), ("hard_cut",), 2, (), ())
    assert qc(brief, short) == {"timeline.label_missing"}

    bad = TimelineFacts(
        600.0, ((0.0, 600.0),), ("glitch", "wipe"), 5, ("a1", "a2"), ("Das beste Angebot",)
    )
    found = qc(brief, bad, flagged_assets=frozenset({"a2"}), forbidden_words=("beste",))
    assert found == {
        "timeline.transition",
        "timeline.overlays",
        "timeline.flagged_asset",
        "timeline.forbidden_text",
    }


def test_metadata_puts_the_mandatory_items_in_by_rule(brief: BriefModel) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    rows = products(facts)
    raw = scaffold_script(ScaffoldInputs("T", rows, facts, brief, None, "abonnieren, liken", True))
    starts = {}
    at = 0.0
    for item in estimate_lengths(raw):
        starts[item.scene_id] = at
        at += 20.0
    meta = build_metadata(
        MetadataInputs(
            brief, facts, raw, (), starts, ScanContext("Lidl"), catchphrase="abonnieren, liken"
        )
    )

    assert meta.hashtags[0] == "#LidlFeuerwerk"
    assert meta.description.splitlines()[0].startswith("WERBUNG:")
    assert "{TRACKING_LINK}" in meta.description and "@lidl_de" in meta.description
    assert "Kapitel:" in meta.description and meta.chapters[0].at_seconds == 0
    assert all(
        b.at_seconds - a.at_seconds >= 10
        for a, b in zip(meta.chapters, meta.chapters[1:], strict=False)
    )
    assert tag_length(meta.tags) <= 500 and len(meta.titles[0]) <= 100
    # the linter agrees, except the link that has not arrived yet
    found = {
        f.rule_id
        for f in lint_metadata(
            title=meta.titles[0],
            description=meta.description,
            tags=meta.tags,
            brief=brief,
            tracking_link_resolved=False,
        )
    }
    assert found == {"metadata.tracking_link"}
    assert clock(65) == "1:05" and clock(3725) == "1:02:05"


def test_a_risky_title_never_becomes_an_option(brief: BriefModel) -> None:
    facts = derive(brief.facts, standard_formulas(brief.facts))
    risky = StoryConcept(
        "c",
        "l",
        "h",
        "r",
        ("a", "b"),
        "cb",
        "w",
        ("video:x",),
        ("Das gibt es bei keinem anderen Discounter", "Fünf Artikel im Überblick", "X"),
    )
    meta = build_metadata(
        MetadataInputs(
            brief,
            facts,
            scaffold_script(ScaffoldInputs("T", products(facts), facts, brief, None, "x", True)),
            (risky,),
            {},
            ScanContext("Lidl"),
        )
    )

    assert "Das gibt es bei keinem anderen Discounter" not in meta.titles
    assert "Fünf Artikel im Überblick" in meta.titles


def test_a_concept_needs_evidence_and_two_or_three_mechanics_and_three_titles() -> None:
    ok = StoryConcept(
        "c1",
        "Eine Idee",
        "Ein Hook",
        "Ein Faden",
        ("a", "b"),
        "Zurück",
        "Weil",
        ("video:v",),
        ("1", "2", "3"),
    )
    assert problems(ok, {"video:v"}) == ()
    generic = StoryConcept("c2", "Idee", "Hook", "Faden", ("a",), "cb", "w", (), ("1",))
    found = problems(generic, {"video:v"})
    assert len(found) == 3 and any("zu allgemein" in p for p in found)
    assert any("Unbekannter Beleg" in p for p in problems(ok, set()))
    other = StoryConcept(
        "c3",
        "Etwas ganz Anderes",
        "Anderer Einstieg",
        "f",
        ("a", "b"),
        "z",
        "w",
        ("video:v",),
        ("1", "2", "3"),
    )
    assert distinct([ok, other]) and not distinct([ok, ok])
    mixed = mix([(ok, "logline"), (other, "hook")], "m1")
    assert (mixed.logline, mixed.hook) == ("Eine Idee", "Anderer Einstieg")


def run(level: str, **m: object) -> EvalRun:
    auto = auto_scores(RunMeasurements(**m), cuts=(4.0, 6.0), overlay_limit=3)  # type: ignore[arg-type]
    scores = merge_ratings(auto, [Score(Criterion.GRAPHICS, 4.0, "manual")])
    return EvalRun(f"r-{level}", level, "excerpt", scores, "", T0)


def test_automatic_scores_are_ladders_a_wrong_number_is_never_forgiven() -> None:
    good = {
        s.criterion: s.value
        for s in auto_scores(
            RunMeasurements(4.0, 0.95, 1.0, 3, 5.0, 0.97, 0), cuts=(4.0, 6.0), overlay_limit=3
        )
    }
    bad = {
        s.criterion: s.value
        for s in auto_scores(
            RunMeasurements(25.0, 0.5, 0.9, 5, 20.0, 0.4, 3), cuts=(4.0, 6.0), overlay_limit=3
        )
    }

    assert set(good.values()) == {5.0}
    assert (
        bad[Criterion.HOOK] == 1.0 and bad[Criterion.NUMBERS] == 1.0 and bad[Criterion.RULES] == 1.0
    )
    assert bad[Criterion.GRAPHICS] == 2.0 and bad[Criterion.RHYTHM] == 1.0


def test_a_winner_needs_all_three_variants_complete_and_a_tie_goes_to_the_shorter_text() -> None:
    same: dict[str, object] = {
        "hook_seconds": 4.0,
        "script_fidelity": 0.95,
        "numbers_correct": 1.0,
        "overlay_max": 3,
        "cuts_per_minute": 5.0,
        "asset_mapping": 0.97,
        "rule_violations": 0,
    }
    runs = [run(level, **same) for level in LEVELS]

    assert all(total(r) == total(runs[0]) for r in runs)
    assert winner(runs) == "minimal"
    assert winner(runs[:2]) is None
    worse = run("minimal", **{**same, "numbers_correct": 0.9})
    assert winner([worse, runs[1], runs[2]]) == "normal"
    unrated = EvalRun(
        "x",
        "normal",
        "excerpt",
        auto_scores(RunMeasurements(hook_seconds=3.0), cuts=(4.0, 6.0), overlay_limit=3),
        "",
        T0,
    )
    assert winner([runs[0], unrated, runs[2]]) is None  # the creator's rating is missing


def test_the_best_level_is_learned_per_format() -> None:
    history = [
        ("ranking", "normal", 4.5),
        ("ranking", "normal", 4.0),
        ("ranking", "minimal", 4.9),
        ("short", "minimal", 4.0),
    ]

    assert learned_best_level(history) == {"ranking": "normal", "short": "minimal"}


def test_the_schedule_is_a_countdown_and_a_reverse_plan(brief: BriefModel) -> None:
    stand = schedule(brief.deadlines, date(2026, 11, 9))

    drafts = [m for m in stand if m.origin == "Briefing" and m.on == "2026-11-10"]
    assert drafts and drafts[0].days == 1
    plan = [m for m in stand if m.origin == "Vorschlag"]
    assert [m.on for m in plan] == ["2026-11-07", "2026-11-08", "2026-11-10"]
    assert all(m.days <= 1 for m in plan)
