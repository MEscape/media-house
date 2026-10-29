"""Configuration, storage, validation, incremental reuse, decisions and score semantics."""

from dataclasses import replace
from fractions import Fraction

import pytest

from media_house.core.domain import Rational
from media_house.modules.continuity_intelligence.domain.errors import (
    InvalidContinuityConfig,
    InvalidContinuityDocument,
)
from media_house.modules.continuity_intelligence.domain.model import (
    ContinuityAnalysis,
    IssueDecision,
    Score,
)
from media_house.modules.continuity_intelligence.domain.profile import PROFILES, ContinuityConfig
from media_house.modules.continuity_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.continuity_intelligence.domain.validation import validate
from media_house.modules.continuity_intelligence.domain.values import (
    Decision,
    IssueType,
    Level,
    RepairKind,
    ScoreBasis,
    ScoreName,
    Severity,
    TransitionKind,
)
from media_house.shared.errors import InvariantViolation
from tests.support.cont_fakes import edge, issue_of, run, secs, side, subject, transition


def jump(tid: str = "tr1", change: float = 0.1, **extra: object):  # type: ignore[no-untyped-def]
    a = side("c1", edge_=edge(subject=subject(cx=0.4)))
    b = side(
        "c2", outgoing=False, start=12.0, end=20.0, edge_=edge(subject=subject(cx=0.4 + change))
    )
    return transition(tid, a=a, b=b, kind=TransitionKind.SAME_SHOT, gap=2.0, **extra)  # type: ignore[arg-type]


class TestConfiguration:
    def test_every_profile_is_valid(self) -> None:
        assert all(p.problems() == [] for p in PROFILES.values())

    def test_unknown_profiles_and_out_of_range_strictness_are_refused(self) -> None:
        with pytest.raises(InvalidContinuityConfig):
            ContinuityConfig(profile="nonsense").resolve()
        with pytest.raises(InvalidContinuityConfig):
            ContinuityConfig(strictness=3.0).resolve()

    def test_the_story_format_picks_the_profile(self) -> None:
        assert ContinuityConfig().resolve("review").name == "product_review"
        assert ContinuityConfig().resolve("unheard of").name == "generic"

    def test_stricter_runs_judge_the_same_evidence_more_severely(self) -> None:
        lenient = issue_of(run([jump()], strictness=0.5), IssueType.JUMP_CUT)
        strict = issue_of(run([jump()], strictness=1.5), IssueType.JUMP_CUT)

        assert strict is not None
        order = [Level.INFO, Level.LOW, Level.MEDIUM, Level.HIGH]
        assert lenient is None or order.index(strict.level) >= order.index(lenient.level)
        assert strict.level is not Level.INFO


class TestScoreSemantics:
    def test_an_unknown_score_says_why_and_a_known_one_stays_in_range(self) -> None:
        with pytest.raises(InvariantViolation):
            Score(
                name=ScoreName.JUMP_CUT_SEVERITY, value=None, basis=ScoreBasis.HEURISTIC, reason=""
            )
        with pytest.raises(InvariantViolation):
            Score(
                name=ScoreName.JUMP_CUT_SEVERITY, value=1.4, basis=ScoreBasis.HEURISTIC, reason="x"
            )

    def test_the_peak_of_an_issue_is_in_the_risk_direction_for_every_kind_of_score(self) -> None:
        a = side("c1", edge_=edge(subject=subject(vx=0.25, vy=0.0), luma=0.3))
        b = side(
            "c2",
            outgoing=False,
            start=12.0,
            end=20.0,
            edge_=edge(subject=subject(vx=-0.25, vy=0.0), luma=0.7),
        )

        analysis = run([transition(a=a, b=b, gap=0.2)], profile="cinematic_dialogue")

        for found in analysis.issues:
            assert found.peak is not None and 0.0 <= found.peak <= 1.0
        spatial = issue_of(analysis, IssueType.SCREEN_DIRECTION)
        assert spatial is not None and spatial.peak is not None and spatial.peak > 0.5

    def test_the_issue_ranking_is_deterministic_and_strongest_first(self) -> None:
        cuts = [jump("tr1", 0.05), jump("tr2", 0.12, at=20.0), jump("tr3", 0.08, at=30.0)]

        first = run(cuts, profile="cinematic_dialogue")
        second = run(list(reversed(cuts)), profile="cinematic_dialogue")

        assert [i.issue_id for i in first.issues] == [i.issue_id for i in first.issues]
        peaks = [i.peak or 0 for i in first.issues if i.level == first.issues[0].level]
        assert peaks == sorted(peaks, reverse=True)
        assert {i.issue_id for i in first.issues} == {i.issue_id for i in second.issues}


class TestExactness:
    def test_source_places_stay_exact_fractions(self) -> None:
        a = side("c1", end=10.0, moment=10.0)
        b = side("c2", outgoing=False, start=1 / 3, end=5.0)
        b = replace(b, clip_start=Fraction(1, 3), moment=Fraction(1, 3))

        analysis = run([transition(a=a, b=b)])

        span = analysis.transitions[0].incoming_at
        assert (span.numerator, span.denominator) == (1, 3)
        assert isinstance(span, Rational)


class TestStorage:
    def analysis(self) -> ContinuityAnalysis:
        return run([jump()], profile="cinematic_dialogue")

    def test_it_round_trips_exactly(self) -> None:
        analysis = self.analysis()

        assert analysis_from_json(analysis_to_json(analysis)) == analysis

    def test_another_schema_document_or_damage_is_refused(self) -> None:
        text = analysis_to_json(self.analysis())

        with pytest.raises(InvalidContinuityDocument):
            analysis_from_json(text.replace('"schema_version": 1', '"schema_version": 99'))
        with pytest.raises(InvalidContinuityDocument):
            analysis_from_json(text.replace('"continuity_analysis"', '"other"', 1))
        with pytest.raises(InvalidContinuityDocument):
            analysis_from_json("{not json")


class TestValidation:
    def broken(self, **change: object) -> ContinuityAnalysis:
        base = run([jump()], profile="cinematic_dialogue")
        issue = replace(base.issues[0], **change)  # type: ignore[arg-type]
        return replace(base, issues=(issue, *base.issues[1:]))

    def errors(self, analysis: ContinuityAnalysis) -> list[str]:
        return [v.detail for v in validate(analysis) if v.severity is Severity.ERROR]

    def test_a_clean_result_has_no_errors(self) -> None:
        assert self.errors(run([jump()], profile="cinematic_dialogue")) == []

    def test_a_span_that_does_not_contain_its_boundary_is_an_error(self) -> None:
        base = run([jump()], profile="cinematic_dialogue")
        span = replace(base.issues[0].spans[0], boundary=Rational(999, 1))

        errors = self.errors(self.broken(spans=(span, base.issues[0].spans[1])))

        assert any("does not contain its boundary" in e for e in errors)

    def test_an_issue_for_a_cut_that_is_not_judged_is_an_error(self) -> None:
        assert any(
            "not in the sequence" in e for e in self.errors(self.broken(transition_id="tr:zzz"))
        )

    def test_an_informational_issue_cannot_be_surfaced(self) -> None:
        assert any(
            "surfaced" in e for e in self.errors(self.broken(level=Level.INFO, surfaced=True))
        )

    def test_a_recommendation_must_be_an_available_repair(self) -> None:
        assert any(
            "recommended" in e for e in self.errors(self.broken(recommended=RepairKind.REFRAME))
        )

    def test_the_metrics_must_agree_with_the_result(self) -> None:
        base = run([jump()], profile="cinematic_dialogue")
        wrong = replace(base, metrics=replace(base.metrics, issues=99))

        assert any("metrics" in e for e in self.errors(wrong))


class TestIncremental:
    def test_an_unchanged_cut_is_carried_over_without_its_evidence(self) -> None:
        first = run([jump("tr1"), jump("tr2", at=20.0)], profile="cinematic_dialogue")
        bare = replace(
            jump("tr9", at=15.0, fingerprint=first.transitions[0].fingerprint),
            outgoing=side("c1", edge_=None),
            incoming=side("c2", outgoing=False, start=12.0, end=20.0, edge_=None),
        )

        second = run(
            [bare, jump("tr2b", 0.2, at=25.0)], profile="cinematic_dialogue", previous=first
        )

        assert second.metrics.reused == 1 and second.metrics.evaluated == 1
        assert second.record("tr9").reused  # type: ignore[union-attr]
        carried = second.issues_at("tr9")
        assert {i.type for i in carried} == {i.type for i in first.issues_at("tr1")}
        assert all(i.timeline_at == Rational(15, 1) for i in carried)

    def test_issue_ids_survive_a_replan_that_leaves_the_cut_alone(self) -> None:
        first = run([jump("tr1")], profile="cinematic_dialogue")
        fp = first.transitions[0].fingerprint

        second = run(
            [jump("tr1", fingerprint=fp, at=40.0)], profile="cinematic_dialogue", previous=first
        )

        assert {i.issue_id for i in second.issues} == {i.issue_id for i in first.issues}

    def test_a_changed_cut_is_judged_again(self) -> None:
        first = run([jump("tr1")], profile="cinematic_dialogue")

        second = run([jump("tr1", 0.01)], profile="cinematic_dialogue", previous=first)

        assert second.metrics.reused == 0 and second.metrics.evaluated == 1
        assert issue_of(second, IssueType.JUMP_CUT) is None


class TestDecisions:
    def test_a_rejected_finding_is_not_surfaced_and_does_not_count_as_a_risk(self) -> None:
        first = run([jump()], profile="cinematic_dialogue")
        found = first.issues[0]

        second = run(
            [jump()],
            profile="cinematic_dialogue",
            decisions=(
                IssueDecision(issue_id=found.issue_id, decision=Decision.REJECTED, note="ok"),
            ),
        )

        kept = second.issue(found.issue_id)
        assert (
            kept is not None and kept.decision is Decision.REJECTED and kept.decision_note == "ok"
        )
        assert kept not in second.surfaced()
        risk = second.risk_between("a1", 10.0, 12.0)
        assert risk is not None and risk[0] == 0.1 and "rejected by a person" in risk[1]

    def test_an_accepted_finding_stays_visible_with_its_decision(self) -> None:
        first = run([jump()], profile="cinematic_dialogue")
        found = first.issues[0]

        second = run(
            [jump()],
            profile="cinematic_dialogue",
            decisions=(IssueDecision(issue_id=found.issue_id, decision=Decision.ACCEPTED),),
        )

        kept = second.issue(found.issue_id)
        assert kept is not None and kept.decision is Decision.ACCEPTED and kept in second.surfaced()


class TestRiskForTheEditor:
    def test_risk_follows_the_strongest_open_issue_and_unknown_cuts_are_unknown(self) -> None:
        analysis = run([jump()], profile="cinematic_dialogue")

        known = analysis.risk_between("a1", 10.0, 12.0)
        other = analysis.risk_between("a1", 77.0, 78.0)

        assert known is not None and known[0] in (0.6, 0.85)
        assert other is None
        assert secs(10) == Fraction(10)

    def test_a_cut_judged_without_picture_evidence_has_no_risk_not_a_clean_one(self) -> None:
        a = side("c1", edge_=None)
        b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=None)

        analysis = run([transition(a=a, b=b, gap=2.0)])

        risk = analysis.risk_between("a1", 10.0, 12.0)
        assert risk is not None and risk[0] is None and "no picture evidence" in risk[1]
