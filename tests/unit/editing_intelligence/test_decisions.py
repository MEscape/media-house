"""Editorial decisions on controlled fixtures: what is kept, cut, shortened, and why."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from media_house.modules.editing_intelligence.domain.engine import plan_edit
from media_house.modules.editing_intelligence.domain.facts import RelationFact
from media_house.modules.editing_intelligence.domain.model import (
    EditPlan,
    ManualOverride,
    Placement,
)
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    Executability,
    IssueKind,
    Origin,
    Severity,
    SignalName,
    StageState,
    TrackKind,
    TrimKind,
)
from tests.support.edit_fakes import Recording, S, Sec, decision, edit, meta, story, talk

TALKING_HEAD = EditConfig(profile="talking_head")


def plan_for(
    sentences: Sequence[S],
    sections: Sequence[Sec] | None = None,
    *,
    config: EditConfig = TALKING_HEAD,
    fmt: str = "talking_head",
    **story_kw: Any,
) -> tuple[Recording, EditPlan]:
    rec = talk(sentences)
    sections = sections or [Sec("all", tuple(range(len(sentences))), "essential")]
    return rec, edit(story(rec, sections, fmt=fmt, **story_kw), rec, config)


def audio(plan: EditPlan) -> tuple[Placement, ...]:
    return plan.track(TrackKind.AUDIO)


# --- distracting pauses -----------------------------------------------------------------------
def test_a_long_silence_inside_kept_speech_is_shortened_with_its_evidence() -> None:
    rec, plan = plan_for([S("the screen is bright and sharp"), S("it also gets very hot", gap=2.5)])

    second = plan.decision("dec:0:u001")
    assert second is not None and second.action is Action.SHORTEN
    (trim,) = second.trims
    assert trim.kind is TrimKind.PAUSE and trim.executability is Executability.EXECUTABLE
    assert any(e.detail == "silence ratio" and e.value == 0.97 for e in trim.evidence)
    first_clip, second_clip = audio(plan)
    removed = second_clip.source.start.value - first_clip.source.end.value
    gap = rec.spans[1][0] - rec.spans[0][1]
    assert gap - removed == pytest.approx(TALKING_HEAD.resolve("generic").pause_keep, abs=0.05)
    assert removed == pytest.approx(sum(t.range.seconds for t in second.trims))
    assert plan.duration.estimated.value == pytest.approx(
        audio(plan)[0].timeline_duration.value + audio(plan)[1].timeline_duration.value
    )
    assert plan.duration.estimated.value < (rec.spans[1][1] - rec.spans[0][0]) - removed + 1.0
    assert (second.value(SignalName.SHORTEN) or 0) > 0.3


def test_a_pause_before_an_emphasised_word_is_deliberate_and_kept() -> None:
    _, plan = plan_for(
        [S("the screen is bright and sharp"), S("it also gets very hot", gap=2.5, emphasis=0.9)]
    )

    assert decision(plan, "dec:0:u001").action is Action.KEEP
    assert len(audio(plan)) == 1


def test_a_gap_with_laughter_is_kept_and_recorded_as_a_reaction() -> None:
    _, plan = plan_for(
        [S("the screen is bright and sharp"), S("it also gets very hot", gap=2.5, event="laughter")]
    )

    assert len(audio(plan)) == 1
    (reaction,) = plan.decisions_for(Action.REACTION)
    assert "laughter" in reaction.rationale[0]


def test_silence_that_is_not_confirmed_is_only_a_suggestion() -> None:
    rec = talk(
        [S("the screen is bright and sharp"), S("it also gets very hot", gap=2.5)], silence=0.4
    )
    plan = edit(
        story(rec, [Sec("all", (0, 1), "essential")], fmt="talking_head"), rec, TALKING_HEAD
    )

    (trim,) = decision(plan, "dec:0:u001").trims
    assert trim.executability is Executability.NEEDS_REVIEW
    assert decision(plan, "dec:0:u001").executability is Executability.NEEDS_REVIEW


def test_a_documentary_keeps_a_pause_a_talking_head_would_trim() -> None:
    sentences = [S("the screen is bright and sharp"), S("it also gets very hot", gap=1.5)]
    _, talking = plan_for(sentences)
    _, documentary = plan_for(
        sentences, config=EditConfig(profile="documentary"), fmt="documentary"
    )

    assert len(audio(talking)) == 2 and len(audio(documentary)) == 1


def test_a_pause_before_a_payoff_is_never_trimmed() -> None:
    sentences = [S("so what is the verdict"), S("it is excellent value", gap=2.5)]
    _, plan = plan_for(
        sentences, [Sec("all", (0,), "essential"), Sec("end", (1,), "essential", "payoff")]
    )

    first, second = audio(plan)  # split at the section change, with nothing removed between them
    assert first.source.end == second.source.start
    assert decision(plan, "dec:0:u001").trims == ()


# --- filler -----------------------------------------------------------------------------------
def test_an_isolated_filler_word_is_trimmed_but_content_words_are_not() -> None:
    _, plan = plan_for([S("this phone is um really good value", inner=((2, 0.3), (3, 0.3)))])

    decision = plan.decision("dec:0:u000")
    assert decision is not None and decision.action is Action.SHORTEN
    (trim,) = decision.trims
    assert trim.kind is TrimKind.FILLER and trim.executability is Executability.EXECUTABLE
    assert [e.detail for e in trim.evidence] == ["um"]


def test_a_filler_glued_between_words_has_no_room_and_stays() -> None:
    _, plan = plan_for([S("this phone is um really good value")])

    assert decision(plan, "dec:0:u000").action is Action.KEEP


def test_an_interview_keeps_its_fillers() -> None:
    _, plan = plan_for(
        [S("this phone is um really good value", inner=((2, 0.3), (3, 0.3)))],
        config=EditConfig(profile="interview"),
        fmt="interview",
    )

    assert decision(plan, "dec:0:u000").action is Action.KEEP


def test_a_filler_at_the_start_of_the_passage_only_moves_the_cut_point() -> None:
    rec, plan = plan_for([S("um so this phone is quite good")])

    (clip,) = audio(plan)
    assert clip.source.start.value > rec.audio.words[0].end - 0.05  # the "um" is outside the clip
    assert decision(plan, "dec:0:u000").trims[0].kind is TrimKind.FILLER


# --- whole sentences --------------------------------------------------------------------------
def test_a_sentence_of_only_filler_is_cut_and_is_safe_to_run() -> None:
    _, plan = plan_for(
        [S("the screen is bright"), S("so yeah okay", gap=0.4), S("the speaker is loud")],
        [Sec("a", (0,), "essential"), Sec("b", (1,), "optional"), Sec("c", (2,), "essential")],
    )

    cut = plan.decision("dec:0:u001")
    assert cut is not None and cut.action is Action.CUT
    assert cut.executability is Executability.EXECUTABLE
    assert cut.value(SignalName.NARRATIVE_RISK) == pytest.approx(0.05)
    assert cut.value(SignalName.CUT) == 0.9 and cut.value(SignalName.KEEP) == 0.1


def test_removing_content_is_a_suggestion_not_an_executable_decision() -> None:
    _, plan = plan_for(
        [
            S("the screen is bright and sharp"),
            S("as I said the screen is bright", repeats="nothing_new"),
            S("the speaker is loud"),
        ],
        [Sec("a", (0,), "essential"), Sec("b", (1,), "optional"), Sec("c", (2,), "essential")],
    )

    cut = plan.decision("dec:0:u001")
    assert cut is not None and cut.action is Action.CUT
    assert cut.executability is Executability.NEEDS_REVIEW
    assert "meaning preservation is not established" in cut.warnings[0]
    assert (cut.value(SignalName.NARRATIVE_RISK) or 0) > 0.3


def test_a_recorded_copy_of_kept_material_is_safe_to_remove() -> None:
    copy = RelationFact(
        certainty="recorded",
        treatment="redundant_copy",
        affected_sections=("sec1",),
    )
    _, plan = plan_for(
        [
            S("the screen is bright and sharp"),
            S("the speaker is loud"),
            S("the battery lasts long"),
        ],
        [
            Sec("a", (0,), "essential"),
            Sec("copy", (1,), "redundant_candidate"),
            Sec("c", (2,), "essential"),
        ],
        relations=(copy,),
    )

    cut = plan.decision("dec:0:u001")
    assert (
        cut is not None
        and cut.action is Action.CUT
        and cut.executability is Executability.EXECUTABLE
    )


def test_a_greeting_is_a_suggestion_in_a_profile_that_trims_it() -> None:
    _, plan = plan_for(
        [S("hey guys welcome back to my channel"), S("the screen is bright and sharp")],
        [Sec("a", (0,), "optional"), Sec("b", (1,), "essential")],
    )

    greeting = plan.decision("dec:0:u000")
    assert greeting is not None and greeting.action is Action.CUT
    assert greeting.executability is Executability.NEEDS_REVIEW


def test_a_false_start_restated_by_the_next_sentence_is_cut() -> None:
    _, plan = plan_for(
        [
            S("I think the", clean_end=False),
            S("I think the screen is bright"),
            S("the speaker is loud"),
        ],
        [Sec("a", (0, 1), "supporting"), Sec("b", (2,), "essential")],
        config=EditConfig(profile="short"),
        fmt="short",
    )

    assert decision(plan, "dec:0:u000").action is Action.CUT
    assert decision(plan, "dec:0:u001").action is Action.KEEP


# --- hard constraints -------------------------------------------------------------------------
def test_essential_information_is_never_cut_even_when_the_content_score_says_so() -> None:
    _, plan = plan_for(
        [S("the screen is bright", removal=0.95), S("the speaker is loud")],
        [Sec("a", (0,), "optional"), Sec("b", (1,), "essential")],
        key_info=[("essential", 0, ())],
    )

    kept = plan.decision("dec:0:u000")
    assert kept is not None and kept.action is Action.KEEP
    assert any("key information" in c for c in kept.constraints)
    assert kept.value(SignalName.KEEP) == 1.0 and kept.value(SignalName.CUT) == pytest.approx(0.855)


def test_a_qualification_travels_with_the_claim_it_qualifies() -> None:
    _, plan = plan_for(
        [S("the battery lasts two days"), S("but only with the screen dimmed", removal=0.95)],
        [Sec("a", (0,), "essential"), Sec("b", (1,), "optional")],
        key_info=[("essential", 0, (1,))],
    )

    qualifier = plan.decision("dec:0:u001")
    assert qualifier is not None and qualifier.action is Action.KEEP
    assert any("qualifies" in c for c in qualifier.constraints)


def test_a_sentence_a_kept_passage_needs_is_restored_and_says_why() -> None:
    _, plan = plan_for(
        [
            S("the model is called aurora", removal=0.95),
            S("it is very fast", requires=(0,)),
            S("and loud"),
        ],
        [Sec("a", (0,), "optional"), Sec("b", (1, 2), "essential")],
    )

    restored = plan.decision("dec:0:u000")
    assert restored is not None and restored.action is Action.KEEP
    assert any("restored" in r for r in restored.rationale)


def test_a_segment_a_kept_segment_depends_on_is_not_removed() -> None:
    _, plan = plan_for(
        [
            S("the model is called aurora", removal=0.95),
            S("that one is very fast", depends_on=(0,)),
        ],
        [Sec("a", (0,), "optional"), Sec("b", (1,), "essential")],
    )

    assert decision(plan, "dec:0:u000").action is Action.KEEP


def test_an_essential_section_is_never_left_empty() -> None:
    _, plan = plan_for(
        [S("so yeah okay"), S("um okay so")],
        [Sec("a", (0, 1), "essential")],
        config=EditConfig(profile="generic"),
        fmt="generic",
    )

    assert sum(1 for d in plan.decisions if d.action is not Action.CUT and d.unit_ids) >= 1
    assert plan.placements


# --- a person's decisions ---------------------------------------------------------------------
def test_a_manual_override_beats_the_automatic_decision_and_is_marked() -> None:
    sentences = [
        S("the screen is bright and sharp"),
        S("so yeah okay", gap=0.4),
        S("the speaker is loud"),
    ]
    sections = [
        Sec("a", (0,), "essential"),
        Sec("b", (1,), "optional"),
        Sec("c", (2,), "essential"),
    ]
    rec = talk(sentences)
    st = story(rec, sections, fmt="talking_head")

    plan = edit(
        st,
        rec,
        TALKING_HEAD,
        overrides=(
            ManualOverride(decision_id="dec:0:u001", action=Action.KEEP, note="it is a joke"),
        ),
    )

    kept = plan.decision("dec:0:u001")
    assert kept is not None and kept.origin is Origin.MANUAL and kept.action is Action.KEEP
    assert kept.note == "it is a joke" and kept.executability is Executability.EXECUTABLE
    assert decision(plan, "dec:0:u000").origin is Origin.AUTOMATIC


def test_changing_one_override_changes_only_that_decision() -> None:
    sentences = [
        S("the screen is bright and sharp"),
        S("so yeah okay", gap=0.4),
        S("the speaker is loud"),
        S("um okay so", gap=0.4),
        S("the battery lasts long", gap=0.4),
    ]
    sections = [
        Sec("a", (0,), "essential"),
        Sec("b", (1,), "optional"),
        Sec("c", (2,), "essential"),
        Sec("d", (3,), "optional"),
        Sec("e", (4,), "essential"),
    ]
    rec = talk(sentences)
    st = story(rec, sections, fmt="talking_head")

    before = edit(st, rec, TALKING_HEAD)
    after = edit(
        st,
        rec,
        TALKING_HEAD,
        overrides=(ManualOverride(decision_id="dec:0:u001", action=Action.KEEP),),
    )

    assert decision(before, "dec:0:u001").action is Action.CUT
    assert decision(after, "dec:0:u001").action is Action.KEEP
    assert after.decision("dec:0:u003") == before.decision("dec:0:u003")
    assert after.decision("dec:0:u004") == before.decision("dec:0:u004")
    for decision_id in ("dec:0:u000", "dec:0:u002"):
        assert decision(after, decision_id).action is decision(before, decision_id).action


def test_a_person_removing_essential_information_is_a_warning_not_a_hidden_error() -> None:
    sentences = [S("the battery lasts two days"), S("the speaker is loud")]
    rec = talk(sentences)
    st = story(
        rec,
        [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")],
        fmt="talking_head",
        key_info=[("essential", 0, ())],
    )

    plan = edit(
        st,
        rec,
        TALKING_HEAD,
        overrides=(ManualOverride(decision_id="dec:0:u000", action=Action.CUT),),
    )

    assert decision(plan, "dec:0:u000").action is Action.CUT
    risky = [i for i in plan.issues if i.kind is IssueKind.MANUAL_OVERRIDE_RISK]
    assert risky and all(i.severity is Severity.WARNING for i in risky)
    assert not plan.issues_of(Severity.ERROR)


def test_an_override_for_an_unknown_decision_is_reported() -> None:
    rec = talk([S("the screen is bright")])
    plan = edit(
        story(rec, [Sec("a", (0,), "essential")]),
        rec,
        overrides=(ManualOverride(decision_id="dec:0:u099", action=Action.CUT),),
    )

    assert any(i.kind is IssueKind.UNKNOWN_REFERENCE and "u099" in i.detail for i in plan.issues)


# --- missing evidence -------------------------------------------------------------------------
def test_without_audio_analysis_cuts_rest_on_sentence_times_and_are_flagged() -> None:
    rec = talk(
        [S("the screen is bright and sharp"), S("so yeah okay", gap=0.4), S("the speaker is loud")],
        with_audio=False,
    )
    sections = [
        Sec("a", (0,), "essential"),
        Sec("b", (1,), "optional"),
        Sec("c", (2,), "essential"),
    ]

    plan = edit(story(rec, sections, fmt="talking_head"), rec, TALKING_HEAD)

    assert any(
        i.severity is Severity.UNRESOLVED and "no audio analysis" in i.detail for i in plan.issues
    )
    stage = next(s for s in plan.stages if s.stage == "cut_points")
    assert stage.state is StageState.NOT_AVAILABLE
    kept = plan.decision("dec:0:u000")
    assert kept is not None and kept.executability is Executability.NEEDS_REVIEW
    assert kept.value(SignalName.CUT_POINT_CONFIDENCE) <= 0.35  # type: ignore[operator]
    assert decision(plan, "dec:0:u001").executability is Executability.NEEDS_REVIEW
    assert any("sentence-level or estimated" in i.detail for i in plan.issues)


def test_without_a_content_analysis_the_story_beats_are_the_units_and_nothing_is_executable() -> (
    None
):
    rec = talk([S("the screen is bright and sharp"), S("the speaker is loud")], with_content=False)
    full = talk([S("the screen is bright and sharp"), S("the speaker is loud")])
    st = story(
        full,
        [Sec("a", (0,), "essential", "claim"), Sec("b", (1,), "essential", "claim")],
        fmt="talking_head",
    )

    plan = edit(st, rec, TALKING_HEAD)

    assert plan.placements
    assert all(
        d.executability is not Executability.EXECUTABLE
        for d in plan.decisions
        if d.action is Action.KEEP
    )
    assert any("no content analysis" in i.detail for i in plan.issues)


def test_missing_values_stay_none_never_zero() -> None:
    _, plan = plan_for([S("the screen is bright and sharp")])

    decision = plan.decision("dec:0:u000")
    assert decision is not None
    assert decision.signal(SignalName.ENGAGEMENT) is None  # no emphasis or hook evidence upstream
    assert decision.signal(SignalName.NARRATIVE_RISK) is None  # nothing is removed
    reorder = decision.signal(SignalName.REORDER)
    assert reorder is None


def test_media_the_inspection_found_unusable_is_reported() -> None:
    rec = talk([S("the screen is bright and sharp")])
    broken = replace(rec.evidence, source=replace(rec.evidence.source, usable=False))
    st = story(rec, [Sec("a", (0,), "essential")])

    plan = plan_edit(st, {"a1": broken}, EditConfig(), meta())

    assert any(i.severity is Severity.WARNING and "unusable" in i.detail for i in plan.issues)
