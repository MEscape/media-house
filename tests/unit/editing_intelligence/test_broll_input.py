"""Editing reads the B-roll analysis: its candidates (from any recording) for story visuals."""

from dataclasses import replace

from media_house.modules.editing_intelligence.domain.engine import plan_edit
from media_house.modules.editing_intelligence.domain.facts import (
    BrollFacts,
    BrollNote,
    BrollPlace,
    ShotChoice,
)
from media_house.modules.editing_intelligence.domain.model import EditPlan
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.values import (
    Executability,
    PlacementRole,
    Severity,
    StageState,
    TrackKind,
)
from tests.support.edit_fakes import Recording, S, Sec, meta, story, talk, visual, with_video

SHOTS = [(0.0, 12.0), (20.0, 26.0), (26.0, 30.0), (30.0, 36.0)]
LIBRARY_SHOTS = [(0.0, 60.0)]


def scene() -> Recording:
    rec = talk(
        [
            S("welcome to the review of this phone"),
            S("this battery lasts two full days", gap=0.4),
            S("overall I like it a lot", gap=0.4),
        ],
        duration=50.0,
    )
    return with_video(rec, SHOTS, speaking=[0])


def library() -> Recording:
    """Footage of a different recording: one long shot, nobody speaking in it."""
    return with_video(talk([S("b-roll")], asset_id="lib", duration=60.0), LIBRARY_SHOTS)


def facts(
    *choices: ShotChoice, missing: bool = False, notes: tuple[BrollNote, ...] = ()
) -> BrollFacts:
    return BrollFacts(
        analysis_id="ba_1",
        profile="generic",
        places=(
            BrollPlace(
                visual_id="vis1", choices=tuple(choices), start=0.0, end=60.0, missing=missing
            ),
        ),
        notes=notes,
        analysed=tuple((c.asset_id, c.asset_id) for c in choices),
        identity="broll-1",
    )


def plan_with(broll: BrollFacts | None, *, baseline: bool = False) -> EditPlan:
    rec = scene()
    base = story(rec, [Sec("all", (0, 1, 2), "essential")])
    shots = [("shot_1", "described", 1)] if baseline else []
    vis = visual(base, rec, 1, shots, purpose="demonstration")
    return plan_edit(
        story(rec, [Sec("all", (0, 1, 2), "essential")], visuals=(vis,)),
        {"a1": rec.evidence},
        EditConfig(),
        meta(),
        None,
        None,
        broll,
        {"lib": library().evidence},
    )


def direct(**changes: object) -> ShotChoice:
    base = ShotChoice(
        asset_id="lib",
        shot_id="shot_0",
        grade="broll_direct",
        rank=1,
        hint_start=30.0,
        hint_end=34.0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_footage_from_another_recording_covers_the_passage_and_is_a_known_source() -> None:
    plan = plan_with(facts(direct()))

    (request,) = plan.coverage
    assert request.chosen is not None and request.chosen.asset_id == "lib"
    assert request.executability is Executability.EXECUTABLE
    cover = plan.placement(request.placement_id or "")
    assert cover is not None and cover.role is PlacementRole.COVER and cover.layer == 1
    assert cover.source.asset_id == "lib"
    assert {s.asset_id for s in plan.sources} == {"a1", "lib"}
    assert [i for i in plan.issues if i.severity is Severity.ERROR] == []


def test_the_suggested_part_of_the_shot_is_used_not_its_start() -> None:
    plan = plan_with(facts(direct()))

    (request,) = plan.coverage
    assert request.chosen is not None
    assert 29.5 <= request.chosen.range.start.value <= 31.0
    assert request.chosen.range.end.value <= 36.0


def test_a_hint_outside_the_shot_is_clamped_to_the_shot() -> None:
    plan = plan_with(facts(direct(hint_start=500.0, hint_end=510.0)))

    (request,) = plan.coverage
    assert request.chosen is not None
    assert request.chosen.range.end.value <= 60.0


def test_illustrative_footage_is_offered_but_needs_a_persons_check() -> None:
    plan = plan_with(
        facts(
            direct(
                grade="broll_illustrative",
                warnings=("the footage relates to the passage but does not prove it",),
            ),
            missing=True,
        )
    )

    (request,) = plan.coverage
    assert request.chosen is not None
    assert request.executability is Executability.NEEDS_REVIEW
    assert any("does not prove" in w for w in request.chosen.warnings)


def test_a_candidate_the_video_analysis_does_not_know_is_refused_not_trusted() -> None:
    plan = plan_with(facts(direct(shot_id="no_such_shot")))

    (request,) = plan.coverage
    assert request.chosen is None
    assert request.executability is Executability.UNRESOLVED


def test_without_a_broll_analysis_the_alignments_shots_stand() -> None:
    plan = plan_with(None, baseline=True)

    (request,) = plan.coverage
    assert request.chosen is not None and request.chosen.asset_id == "a1"
    stage = next(s for s in plan.stages if s.stage == "broll")
    assert stage.state is StageState.NOT_AVAILABLE


def test_broll_candidates_come_first_and_the_alignments_shots_stay_as_fallbacks() -> None:
    plan = plan_with(facts(direct()), baseline=True)

    (request,) = plan.coverage
    assert request.chosen is not None and request.chosen.asset_id == "lib"
    assert [a.asset_id for a in request.alternatives] == ["a1"]
    assert next(s for s in plan.stages if s.stage == "broll").state is StageState.OK


def test_footage_for_places_the_story_did_not_name_is_shown_as_a_note_never_placed() -> None:
    note = BrollNote(
        asset_id="a1",
        start=40.0,
        end=44.0,
        need="variety",
        detail="cutaway footage kitchen is available",
    )

    plan = plan_with(facts(direct(), notes=(note,)))

    shown = [n for n in plan.pacing_notes if n.level == "broll"]
    assert len(shown) == 1 and "kitchen" in shown[0].detail
    assert len([p for p in plan.placements if p.role is PlacementRole.COVER]) == 1


def test_the_plan_is_deterministic_and_uses_only_stored_facts() -> None:
    assert plan_with(facts(direct())) == plan_with(facts(direct()))


def test_cover_only_assets_never_change_the_story_timeline() -> None:
    with_cover = plan_with(facts(direct()))
    without = plan_with(None)

    def main(plan: EditPlan) -> list[object]:
        return [
            (c.source.start, c.source.end, c.timeline_start)
            for c in plan.track(TrackKind.VIDEO)
            if c.layer == 0
        ]

    assert main(with_cover) == main(without)
