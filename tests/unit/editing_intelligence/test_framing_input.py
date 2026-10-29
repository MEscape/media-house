"""Editing reads the framing analysis: every picture clip carries its recommendations, and a clip
the recommendations cannot serve is an issue, a note and never a silent crop."""

from fractions import Fraction

from media_house.modules.editing_intelligence.domain.engine import plan_edit
from media_house.modules.editing_intelligence.domain.facts import (
    FramingFacts,
    FramingSpan,
    StoryFacts,
)
from media_house.modules.editing_intelligence.domain.model import EditPlan
from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.editing_intelligence.domain.values import (
    IssueKind,
    Severity,
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


def plan(framing: FramingFacts | None) -> EditPlan:
    rec, st = scene()
    return plan_edit(
        st,
        {"a1": rec.evidence},
        EditConfig(profile="documentary"),
        meta(),
        framing=framing,
    )


def span(
    rec_id: str, start: float, end: float, *, strategy: str = "static_crop",
    feasibility: str = "feasible", detail: str = "",
) -> FramingSpan:  # fmt: skip
    return FramingSpan(
        recommendation_id=rec_id,
        asset_id="a1",
        start=Fraction(start).limit_denominator(1000),
        end=Fraction(end).limit_denominator(1000),
        strategy=strategy,
        feasibility=feasibility,
        executable=feasibility in ("feasible", "feasible_with_tradeoffs", "low_quality"),
        detail=detail,
    )


def facts(*spans: FramingSpan, identity: str = "f-1") -> FramingFacts:
    return FramingFacts(
        analysis_id="fr_test", target="9:16", profile="generic", spans=spans, identity=identity
    )


def test_every_picture_clip_carries_the_recommendations_of_its_source_range() -> None:
    base = plan(None)
    everything = facts(span("fr_a", 0, 1000))

    framed = plan(everything)

    video = framed.track(TrackKind.VIDEO)
    assert video and all(p.framing for p in video)
    first = video[0].framing[0]
    assert (first.recommendation_id, first.analysis_id, first.target) == ("fr_a", "fr_test", "9:16")
    assert first.strategy == "static_crop" and first.executable
    # the reference carries the part of the clip it covers, in source seconds
    assert (
        float(first.start.numerator / first.start.denominator) >= video[0].source.start.value - 1e-3
    )
    assert all(not p.framing for p in framed.track(TrackKind.AUDIO))  # sound is not cropped
    assert [p.source for p in base.track(TrackKind.VIDEO)] == [p.source for p in video]  # kept


def test_a_range_the_analysis_does_not_cover_gets_no_reference() -> None:
    framed = plan(facts(span("fr_late", 900, 1000)))

    assert all(not p.framing for p in framed.track(TrackKind.VIDEO))


def test_a_clip_no_crop_can_serve_is_an_issue_with_the_alternative_the_analysis_named() -> None:
    bad = span(
        "fr_bad", 0, 1000, strategy="unreframeable", feasibility="impossible",
        detail="two people do not fit; alternative: shot s4 frames wider (needs a decision)",
    )  # fmt: skip

    framed = plan(facts(bad))

    issues = [i for i in framed.issues if i.kind is IssueKind.FRAMING]
    assert issues and all(i.severity is Severity.WARNING for i in issues)
    assert "fr_bad" in issues[0].detail and "shot s4" in issues[0].detail
    notes = [n for n in framed.pacing_notes if n.level == "framing"]
    assert notes and "fr_bad" in notes[0].detail
    # the editor keeps the clip it chose: nothing was cropped, moved or dropped
    assert [p.source for p in framed.track(TrackKind.VIDEO)] == [
        p.source for p in plan(None).track(TrackKind.VIDEO)
    ]


def test_uncertain_and_soft_recommendations_are_flagged_at_their_own_severity() -> None:
    soft = plan(facts(span("fr_soft", 0, 1000, feasibility="low_quality", detail="enlarged 4x")))
    unsure = plan(facts(span("fr_unsure", 0, 1000, feasibility="uncertain", detail="no tracks")))

    assert {i.severity for i in soft.issues if i.kind is IssueKind.FRAMING} == {Severity.WARNING}
    assert {i.severity for i in unsure.issues if i.kind is IssueKind.FRAMING} == {
        Severity.UNRESOLVED
    }


def test_a_clean_recommendation_raises_nothing() -> None:
    framed = plan(facts(span("fr_ok", 0, 1000)))

    assert not [i for i in framed.issues if i.kind is IssueKind.FRAMING]
    assert not [n for n in framed.pacing_notes if n.level == "framing"]


def test_the_stage_is_reported_and_missing_analysis_is_said() -> None:
    framed = plan(facts(span("fr_ok", 0, 1000)))

    assert next(s for s in framed.stages if s.stage == "framing_analysis").state is StageState.OK
    missing = next(s for s in plan(None).stages if s.stage == "framing_analysis")
    assert missing.state is StageState.NOT_AVAILABLE


def test_the_framing_references_survive_the_stored_plan() -> None:
    framed = plan(facts(span("fr_a", 0, 1000)))

    assert plan_from_json(plan_to_json(framed)) == framed
