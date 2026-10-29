"""Editing reads the continuity analysis: a measured verdict per cut, findings as notes."""

from dataclasses import replace
from fractions import Fraction

from media_house.modules.editing_intelligence.domain.engine import plan_edit
from media_house.modules.editing_intelligence.domain.facts import (
    ContinuityFacts,
    ContinuityNote,
    CutVerdict,
    StoryFacts,
)
from media_house.modules.editing_intelligence.domain.model import EditPlan
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.values import (
    IssueKind,
    SignalBasis,
    StageState,
    TrackKind,
)
from tests.support.edit_fakes import Recording, S, Sec, meta, story, talk, with_video


def scene() -> tuple[Recording, StoryFacts]:
    rec = with_video(
        talk(
            [
                S("welcome to the review of this phone"),
                S("this battery lasts two full days", gap=2.5),
            ],
            duration=50.0,
        ),
        [(0.0, 20.0)],
        speaking=[0],
    )
    return rec, story(rec, [Sec("a", (0,), "essential"), Sec("b", (1,), "essential")])


def plan(continuity: ContinuityFacts | None) -> EditPlan:
    rec, st = scene()
    return plan_edit(
        st,
        {"a1": rec.evidence},
        EditConfig(profile="documentary"),
        meta(),
        None,
        None,
        None,
        None,
        continuity,
    )


def seconds(rational: object) -> float:
    return float(Fraction(rational.numerator, rational.denominator))  # type: ignore[attr-defined]


def verdict_for(base: EditPlan, risk: float | None) -> ContinuityFacts:
    first, second = base.track(TrackKind.VIDEO)[:2]
    cut = CutVerdict(
        out_asset="a1",
        leaves=seconds(first.source.end),
        in_asset="a1",
        resumes=seconds(second.source.start),
        risk=risk,
        reason="continuity analysis: the subject jumps (high)",
    )
    return ContinuityFacts(
        analysis_id="ca1",
        profile="generic",
        cuts=(cut,),
        notes=(ContinuityNote(subject="jump_cut", detail="the subject jumps [high, at 6.0 s]"),),
        identity="c-1",
    )


def second_risk(p: EditPlan):  # type: ignore[no-untyped-def]
    return p.track(TrackKind.VIDEO)[1].continuity_risk


def test_a_measured_verdict_replaces_the_baseline_guess_for_that_cut() -> None:
    base = plan(None)

    judged = plan(verdict_for(base, 0.85))

    risk = second_risk(judged)
    assert risk is not None and risk.value == 0.85 and risk.basis is SignalBasis.MEASURED
    assert "continuity analysis" in risk.reason
    assert any(i.kind is IssueKind.CONTINUITY for i in judged.issues)


def test_a_cut_the_analysis_could_not_judge_keeps_the_baseline() -> None:
    base = plan(None)

    assert second_risk(plan(verdict_for(base, None))) == second_risk(base)


def test_a_cut_the_analysis_never_saw_keeps_the_baseline() -> None:
    base = plan(None)
    facts = verdict_for(base, 0.9)
    elsewhere = replace(facts, cuts=(replace(facts.cuts[0], leaves=99.0),))

    assert second_risk(plan(elsewhere)) == second_risk(base)


def test_findings_are_shown_as_notes_and_the_stage_is_reported() -> None:
    base = plan(None)
    judged = plan(verdict_for(base, 0.85))

    notes = [n for n in judged.pacing_notes if n.level == "continuity"]
    assert len(notes) == 1 and "subject jumps" in notes[0].detail
    stage = next(s for s in judged.stages if s.stage == "continuity_analysis")
    assert stage.state is StageState.OK
    missing = next(s for s in base.stages if s.stage == "continuity_analysis")
    assert missing.state is StageState.NOT_AVAILABLE
