"""Cover footage that is fit to use, J/L bridges, continuity at the cuts and optional advisors."""

from collections.abc import Sequence
from typing import Any

import pytest

from media_house.modules.editing_intelligence.domain.facts import ShotChoice, StoryFacts
from media_house.modules.editing_intelligence.domain.hooks import (
    ContinuityVerdict,
    CoverageQuery,
    Hooks,
)
from media_house.modules.editing_intelligence.domain.model import EditPlan, Placement
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    AudioBehavior,
    BridgeKind,
    EffectIntent,
    EffectKind,
    EvidenceKind,
    Executability,
    IssueKind,
    PlacementRole,
    Severity,
    SignalBasis,
    SignalName,
    TrackKind,
)
from tests.support.edit_fakes import (
    Recording,
    S,
    Sec,
    decision,
    edit,
    story,
    talk,
    visual,
    with_video,
)

SHOTS = [(0.0, 12.0), (20.0, 26.0), (26.0, 30.0), (30.0, 36.0)]


def scene(text: str = "this battery lasts two full days", **kw: Any) -> Recording:
    sentences = [
        S("welcome to the review of this phone"),
        S(text, gap=0.4, **kw),
        S("overall I like it a lot", gap=0.4),
    ]
    rec = talk(sentences, duration=50.0)
    return with_video(rec, SHOTS, speaking=[0])


def plan_with(
    rec: Recording,
    shots: Sequence[tuple[str, str, int]],
    config: EditConfig | None = None,
    **visual_kw: Any,
) -> EditPlan:
    base = story(rec, [Sec("all", (0, 1, 2), "essential")])
    vis = visual(base, rec, 1, shots, **visual_kw)
    return edit(story(rec, [Sec("all", (0, 1, 2), "essential")], visuals=(vis,)), rec, config)


# --- fit footage ------------------------------------------------------------------------------
def test_specific_footage_over_a_passage_is_executable_and_sits_on_a_second_layer() -> None:
    rec = scene()
    plan = plan_with(rec, [("shot_1", "described", 1)])

    (request,) = plan.coverage
    assert request.chosen is not None and request.chosen.shot_id == "shot_1"
    assert request.executability is Executability.EXECUTABLE
    cover = plan.placement(request.placement_id or "")
    assert cover is not None and cover.layer == 1 and cover.role is PlacementRole.COVER
    assert cover.track is TrackKind.VIDEO and cover.audio is AudioBehavior.MUTED
    assert cover.source.start.value >= 20.0 and cover.source.end.value <= 26.0  # inside its shot
    main = plan.track(TrackKind.VIDEO)[0]
    assert to_fraction(main.timeline_start) <= to_fraction(cover.timeline_start)
    assert to_fraction(cover.timeline_end) <= to_fraction(plan.duration.estimated) + 1
    kinds = {e.kind for e in decision(plan, request.request_id).evidence}
    assert {
        EvidenceKind.STORY_VISUAL,
        EvidenceKind.ALIGNMENT_MATCH,
        EvidenceKind.VIDEO_SHOT,
    } <= kinds
    assert decision(plan, "aud:vis1").action is Action.PRESERVE_AUDIO
    assert not plan.issues_of(Severity.ERROR)


def test_footage_that_shares_a_keyword_with_a_passage_that_negates_it_is_not_run_unreviewed() -> (
    None
):
    rec = scene("this battery does not last two full days")
    plan = plan_with(rec, [("shot_1", "described", 1)])

    (request,) = plan.coverage
    assert request.chosen is not None
    assert request.executability is Executability.NEEDS_REVIEW
    assert any("negates, doubts or imagines" in n for n in request.notes)
    assert any(
        i.kind is IssueKind.COVER_RISK and i.severity is Severity.WARNING for i in plan.issues
    )


def test_a_weak_association_is_a_suggestion() -> None:
    rec = scene()
    plan = plan_with(rec, [("shot_1", "semantic_candidate", 1)])

    assert plan.coverage[0].executability is Executability.NEEDS_REVIEW
    assert any("association is weak" in n for n in plan.coverage[0].notes)


def test_the_shot_already_on_screen_and_a_speaking_face_never_qualify() -> None:
    rec = scene()
    plan = plan_with(
        rec, [("shot_0", "described", 1)]
    )  # shot_0 is both on screen and has the speaking face

    (request,) = plan.coverage
    assert request.chosen is None and request.placement_id is None
    assert request.executability is Executability.UNRESOLVED
    assert any("already on screen" in n for n in request.notes)
    assert all(p.layer == 0 for p in plan.placements)
    assert any(
        i.kind is IssueKind.COVER_NEEDED and i.severity is Severity.UNRESOLVED for i in plan.issues
    )


def test_a_speaking_face_in_the_footage_is_refused() -> None:
    rec = with_video(
        talk(
            [S("welcome to the review"), S("this battery lasts two full days", gap=0.4)],
            duration=50.0,
        ),
        SHOTS,
        speaking=[1],
    )
    base = story(rec, [Sec("all", (0, 1), "essential")])
    plan = edit(
        story(
            rec,
            [Sec("all", (0, 1), "essential")],
            visuals=(visual(base, rec, 1, [("shot_1", "described", 1)]),),
        ),
        rec,
    )

    assert plan.coverage[0].chosen is None
    assert any("visible face is speaking" in n for n in plan.coverage[0].notes)


def test_unusable_and_too_short_shots_are_refused_with_the_reason() -> None:
    rec = with_video(
        talk(
            [S("welcome to the review"), S("this battery lasts two full days", gap=0.4)],
            duration=50.0,
        ),
        [(0.0, 12.0), (20.0, 20.5)],
    )
    base = story(rec, [Sec("all", (0, 1), "essential")])
    plan = edit(
        story(
            rec,
            [Sec("all", (0, 1), "essential")],
            visuals=(visual(base, rec, 1, [("shot_1", "described", 1)]),),
        ),
        rec,
    )

    assert plan.coverage[0].chosen is None
    assert any("too short" in n for n in plan.coverage[0].notes)


def test_a_shot_missing_from_the_video_analysis_is_refused_not_invented() -> None:
    rec = scene()
    plan = plan_with(rec, [("shot_99", "described", 1)])

    assert plan.coverage[0].chosen is None
    assert any("not found in the video analysis" in n for n in plan.coverage[0].notes)


def test_a_passage_the_edit_cut_gets_no_cover() -> None:
    rec = scene("so yeah okay")  # a filler sentence in an optional section is cut
    secs = [Sec("a", (0,), "essential"), Sec("b", (1,), "optional"), Sec("c", (2,), "essential")]
    base = story(rec, secs)
    plan = edit(
        story(rec, secs, visuals=(visual(base, rec, 1, [("shot_1", "described", 1)]),)), rec
    )

    assert decision(plan, "dec:0:u001").action is Action.CUT
    assert plan.coverage == ()


def test_a_graphic_is_requested_from_a_later_stage_not_drawn_here() -> None:
    rec = scene()
    plan = plan_with(rec, [], purpose="explainer_graphic")

    (effect,) = [e for e in plan.effect_requests if e.kind is EffectKind.GRAPHIC]
    assert effect.intent is EffectIntent.EXPLAIN and effect.decision_id == "cov:vis1"
    assert plan.coverage[0].chosen is None and all(p.layer == 0 for p in plan.placements)


def test_a_low_need_gets_no_cover_even_when_footage_exists() -> None:
    rec = scene()
    plan = plan_with(rec, [("shot_1", "described", 1)], necessity="optional", strength=0.3)

    assert plan.coverage == ()


def test_the_need_is_a_named_signal_with_its_reason() -> None:
    plan = plan_with(scene(), [("shot_1", "described", 1)])

    need = plan.coverage[0].need
    assert need.name is SignalName.COVER_NEED and need.basis is SignalBasis.HEURISTIC
    assert need.value is not None and need.value >= 0.45 and "appetite" in need.reason


# --- J-cuts and L-cuts ----------------------------------------------------------------------------
def bridge_scene() -> Recording:
    sentences = [
        S("welcome to the review of this phone"),
        S("this battery lasts two full days of use", gap=0.4),
        S("overall I like it a lot", gap=2.5),
    ]
    rec = talk(sentences, duration=50.0)
    return with_video(rec, SHOTS, speaking=[0])


def test_a_cover_that_spans_a_cut_in_the_speech_is_an_explicit_bridge() -> None:
    rec = bridge_scene()
    secs = [Sec("a", (0,), "essential"), Sec("b", (1,), "essential"), Sec("c", (2,), "essential")]
    base = story(rec, secs)
    plan = edit(
        story(rec, secs, visuals=(visual(base, rec, 1, [("shot_1", "described", 1)]),)),
        rec,
        EditConfig(profile="talking_head"),
    )

    (bridge,) = plan.audio_bridges
    assert bridge.kind is BridgeKind.BRIDGE
    cover = plan.placement(bridge.placement_ids[0])
    assert cover is not None and cover.layer == 1
    at = to_fraction(bridge.audio_changes_at)
    assert to_fraction(cover.timeline_start) < at < to_fraction(cover.timeline_end)
    sound = plan.track(TrackKind.AUDIO)
    assert len(sound) >= 3 and all(c.layer == 0 for c in sound)  # the speech itself is untouched
    assert decision(plan, "brg:vis1").action is Action.BRIDGE_AUDIO
    assert not plan.issues_of(Severity.ERROR)


# --- continuity -------------------------------------------------------------------------------
def cut_scene(
    shots: Sequence[tuple[float, float]], **shot_fields: Any
) -> tuple[Recording, StoryFacts]:
    rec = with_video(
        talk(
            [
                S("welcome to the review of this phone"),
                S("this battery lasts two full days", gap=2.5),
            ],
            duration=50.0,
        ),
        shots,
        speaking=[0],
        **shot_fields,
    )
    secs = [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")]
    return rec, story(rec, secs)


def second_picture(plan: EditPlan) -> Placement:
    return plan.track(TrackKind.VIDEO)[1]


def test_a_cut_inside_one_shot_with_a_visible_speaker_is_a_jump_cut_risk() -> None:
    rec, st = cut_scene([(0.0, 20.0)])

    plan = edit(st, rec, EditConfig(profile="documentary"))

    risk = second_picture(plan).continuity_risk
    assert risk is not None and risk.value is not None and risk.value >= 0.5
    assert risk.basis is SignalBasis.HEURISTIC and "one continuous shot" in risk.reason


def test_a_style_that_accepts_jump_cuts_scores_the_same_cut_lower() -> None:
    rec, st = cut_scene([(0.0, 20.0)])

    doc = edit(st, rec, EditConfig(profile="documentary"))
    head = edit(st, rec, EditConfig(profile="talking_head"))

    low, high = second_picture(head).continuity_risk, second_picture(doc).continuity_risk
    assert low is not None and high is not None and (low.value or 0) < (high.value or 0)


def test_neighbouring_shots_use_the_measured_jump_cut_likelihood() -> None:
    rec, st = cut_scene([(0.0, 2.3), (2.3, 20.0)], jump_cut_likelihood=0.85)

    plan = edit(st, rec, EditConfig(profile="documentary"))

    risk = second_picture(plan).continuity_risk
    assert risk is not None and risk.value == 0.85 and risk.basis is SignalBasis.MEASURED
    assert any(
        i.kind is IssueKind.CONTINUITY and i.severity is Severity.WARNING for i in plan.issues
    )


def test_shots_that_are_not_neighbours_are_not_assessed_not_called_safe() -> None:
    rec, st = cut_scene([(0.0, 2.0), (2.0, 4.0), (4.0, 20.0)])

    plan = edit(st, rec, EditConfig(profile="documentary"))

    risk = second_picture(plan).continuity_risk
    assert risk is not None and risk.value is None and "not neighbours" in risk.reason


def test_without_video_analysis_continuity_is_unknown() -> None:
    rec = talk(
        [S("welcome to the review of this phone"), S("this battery lasts two full days", gap=2.5)]
    )
    plan = edit(story(rec, [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")]), rec)

    risk = second_picture(plan).continuity_risk
    assert risk is not None and risk.value is None and "no video analysis" in risk.reason


def test_a_cutaway_over_the_cut_hides_it() -> None:
    sentences = [
        S("welcome to the review of this phone"),
        S("this battery lasts two full days", gap=0.4),
        S("overall I like it a lot", gap=2.5),
    ]
    rec = with_video(talk(sentences, duration=50.0), SHOTS, speaking=[0])
    secs = [Sec("a", (0,), "essential"), Sec("b", (1,), "essential"), Sec("c", (2,), "essential")]
    base = story(rec, secs)
    plan = edit(
        story(rec, secs, visuals=(visual(base, rec, 1, [("shot_1", "described", 1)]),)),
        rec,
        EditConfig(profile="documentary"),
    )

    covered = [
        p
        for p in plan.track(TrackKind.VIDEO)
        if p.continuity_risk and p.continuity_risk.reason == "covered by cutaway footage"
    ]
    risk = covered[0].continuity_risk if covered else None
    assert risk is not None and risk.value == 0.1


# --- optional advisors ------------------------------------------------------------------------
def test_a_coverage_advisor_can_rerank_candidates_but_cannot_invent_shots() -> None:
    rec = scene()
    base = story(rec, [Sec("all", (0, 1, 2), "essential")])
    vis = visual(base, rec, 1, [("shot_1", "described", 1), ("shot_2", "described", 2)])
    st = story(rec, [Sec("all", (0, 1, 2), "essential")], visuals=(vis,))
    asked: list[CoverageQuery] = []

    def advise(query: CoverageQuery) -> list[ShotChoice]:
        asked.append(query)
        return [
            ShotChoice(asset_id="a1", shot_id="shot_2", grade="described", rank=1),
            ShotChoice(asset_id="a1", shot_id="invented", grade="described", rank=2),
        ]

    plan = edit(st, rec, hooks=Hooks(coverage=advise))

    assert asked and asked[0].baseline[0].shot_id == "shot_1"
    assert plan.coverage[0].chosen is not None
    assert plan.coverage[0].chosen.shot_id == "shot_2"
    assert not plan.issues_of(Severity.ERROR)


def test_a_continuity_advisor_overrides_the_baseline_and_none_means_no_opinion() -> None:
    rec, st = cut_scene([(0.0, 20.0)])

    advised = edit(
        st,
        rec,
        hooks=Hooks(continuity=lambda q: ContinuityVerdict(risk=0.33, reason="pose differs")),
    )
    declined = edit(st, rec, hooks=Hooks(continuity=lambda q: None))
    baseline = edit(st, rec)

    risk = second_picture(advised).continuity_risk
    assert risk is not None and risk.value == 0.33
    assert second_picture(declined).continuity_risk == second_picture(baseline).continuity_risk


def test_a_plan_is_complete_without_any_advisor() -> None:
    plan = plan_with(scene(), [("shot_1", "described", 1)])

    advisor_stage = next(s for s in plan.stages if s.stage == "advisors")
    assert advisor_stage.state.value == "skipped" and "baseline" in advisor_stage.detail
    assert plan.provenance.advisors == ()
    assert plan.placements and pytest.approx(0) == 0
