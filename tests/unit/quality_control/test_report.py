"""Classification, ordering, identity and comparison of reports; what other modules found."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

import pytest

from media_house.modules.quality_control.domain import facts as f
from media_house.modules.quality_control.domain.checks_external import (
    carry_continuity,
    carry_mix,
    compare_story,
)
from media_house.modules.quality_control.domain.classify import RULES, make_issue
from media_house.modules.quality_control.domain.compare import compare_reports
from media_house.modules.quality_control.domain.errors import InvalidQualityDocument
from media_house.modules.quality_control.domain.model import (
    CheckRecord,
    QualityIssue,
    QualityReport,
)
from media_house.modules.quality_control.domain.report import assemble, dedupe
from media_house.modules.quality_control.domain.sampling import plan_samples
from media_house.modules.quality_control.domain.serialization import (
    report_from_json,
    report_to_json,
)
from media_house.modules.quality_control.domain.values import (
    Area,
    AreaState,
    Basis,
    CheckState,
    ReviewStatus,
    Severity,
    TechnicalStatus,
)
from tests.unit.quality_control.fixtures import GENERIC, clean, codes, one


def issue(code: str = "structure.gap", start_frame: int = 10, **kw: Any) -> QualityIssue:
    return make_issue(clean(), code, "text", start_frame=start_frame, **kw)


# --- classification --------------------------------------------------------------------------


def test_every_rule_has_what_a_person_needs_to_act_on_it() -> None:
    for code, rule in RULES.items():
        assert rule.why and rule.correction and rule.owner and rule.method, code


def test_only_a_sure_objective_critical_finding_blocks_the_export() -> None:
    sure = issue("structure.media_missing", confidence=0.9)
    unsure = issue("structure.media_missing", confidence=0.3)
    opinion = issue("editing.shot_too_long", severity=Severity.CRITICAL)

    assert sure.blocks_export and sure.severity is Severity.CRITICAL
    assert unsure.severity is Severity.HIGH and not unsure.blocks_export
    assert opinion.basis is Basis.SUBJECTIVE and opinion.severity is Severity.MEDIUM
    assert not opinion.blocks_export


def test_a_findings_identity_is_stable_for_the_same_problem_and_per_version() -> None:
    a = issue("structure.gap")
    again = issue("structure.gap")
    other_version = make_issue(
        replace(clean(), content_hash="another"), "structure.gap", "text", start_frame=10
    )

    assert a.issue_id == again.issue_id
    assert other_version.fingerprint == a.fingerprint  # the same problem ...
    assert other_version.issue_id != a.issue_id  # ... on another version


# --- the report ------------------------------------------------------------------------------


def report_of(
    issues: list[QualityIssue],
    scope: list[CheckRecord] | None = None,
    previous: QualityReport | None = None,
    *,
    session: str | None = None,
    preview: str = "",
    inputs: tuple[tuple[str, str], ...] = (("timeline", "x"),),
) -> QualityReport:
    scope = (
        scope
        if scope is not None
        else [
            CheckRecord(name="timeline structure", area=Area.TECHNICAL, state=CheckState.RAN),
            CheckRecord(name="editing", area=Area.EDITING, state=CheckState.RAN),
        ]
    )
    return assemble(
        timeline=clean(), profile=GENERIC, issues=issues, scope=scope, created_at="t",
        review_session_id=session, preview_asset_id=preview, previous=previous, inputs=inputs,
        metrics={}, warnings=(), limits=("a limit",),
    )  # fmt: skip


def test_findings_are_ordered_by_seriousness_then_place_and_deduplicated() -> None:
    low = issue("editing.shot_too_long", start_frame=5)
    critical = issue("structure.media_missing", start_frame=90)
    medium = issue("editing.flash_frame", start_frame=50)

    assert [i.severity for i in dedupe([low, critical, medium, critical])] == [
        Severity.CRITICAL, Severity.MEDIUM, Severity.LOW,
    ]  # fmt: skip


def test_the_summary_and_status_follow_the_findings() -> None:
    blocked = report_of([issue("structure.media_missing"), issue("editing.shot_too_long")])

    assert blocked.technical_status is TechnicalStatus.BLOCKED
    assert blocked.review_status is ReviewStatus.NEEDS_ATTENTION
    assert blocked.severity_summary.critical == 1 and blocked.severity_summary.subjective == 1
    assert blocked.export_blocking_issues == (blocked.detected_issues[0].issue_id,)
    assert blocked.approval == "not_approved"
    assert report_of([issue("structure.gap")]).technical_status is TechnicalStatus.PROBLEMS
    assert report_of([issue("editing.flash_frame")]).technical_status is TechnicalStatus.WARNINGS
    clean_report = report_of([])
    assert clean_report.technical_status is TechnicalStatus.OK
    assert clean_report.review_status is ReviewStatus.READY_FOR_HUMAN_REVIEW


def test_an_area_nobody_checked_is_not_checked_and_not_ok() -> None:
    report = report_of([])

    assert report.story_quality_indicators.state is AreaState.NOT_CHECKED
    assert report.audio_quality_indicators.state is AreaState.NOT_CHECKED
    assert report.editing_quality_indicators.state is AreaState.OK


def test_the_identity_comes_from_the_inputs_and_a_new_input_is_a_new_report() -> None:
    a = report_of([])
    same = report_of([], inputs=(("timeline", "x"),))
    other = report_of([], inputs=(("timeline", "y"),))

    assert a.quality_report_id == same.quality_report_id and a.content_hash == same.content_hash
    assert other.quality_report_id != a.quality_report_id


def test_a_later_report_names_the_one_it_follows() -> None:
    first = report_of([issue("structure.gap")])
    second = report_of([], previous=first, inputs=(("timeline", "changed"),))

    assert (first.report_version, first.supersedes) == (1, None)
    assert (second.report_version, second.supersedes) == (2, first.quality_report_id)


def test_a_report_round_trips_and_a_foreign_document_is_refused() -> None:
    report = report_of([issue("structure.gap"), issue("editing.shot_too_long")])

    assert report_from_json(report_to_json(report)) == report
    with pytest.raises(InvalidQualityDocument):
        report_from_json('{"document_type": "something_else"}')
    with pytest.raises(InvalidQualityDocument):
        report_from_json("not json")


def test_comparing_two_reports_tells_fixed_still_there_and_new() -> None:
    before = report_of(
        [issue("structure.gap", start_frame=10), issue("editing.flash_frame", start_frame=50)]
    )
    after = report_of(
        [
            issue("structure.gap", start_frame=10, severity=Severity.CRITICAL),
            issue("structure.overlap", start_frame=70),
        ],
        inputs=(("timeline", "next"),),
    )

    result = compare_reports(before, after)

    assert [i.code for i in result.resolved] == ["editing.flash_frame"]
    assert [i.code for i in result.worsened] == ["structure.gap"]
    assert [i.code for i in result.introduced] == ["structure.overlap"]
    assert {i.code for i in result.regressions} == {"structure.gap", "structure.overlap"}


# --- what others found -----------------------------------------------------------------------


def mix(*issues: f.MixIssueFacts) -> f.MixFacts:
    return f.MixFacts("mix1", "youtube", -14.0, 1.0, -1.0, 11.0, "completed", issues)


def test_the_mix_engines_findings_keep_their_certainty() -> None:
    found = carry_mix(
        clean(),
        mix(
            f.MixIssueFacts("loudness", "error", "confirmed", "too quiet", None, None, None),
            f.MixIssueFacts(
                "masking",
                "warning",
                "recommendation",
                "music high",
                "a:music:seg1",
                Fraction(1),
                Fraction(2),
            ),
            f.MixIssueFacts("silence", "warning", "intentional", "planned", None, None, None),
            f.MixIssueFacts("note", "info", "confirmed", "fyi", None, None, None),
        ),
    )

    assert [i.code for i in found] == ["audio.mix.loudness", "audio.mix.masking"]
    assert found[0].severity is Severity.HIGH and found[0].origin == "audio_mix_engine"
    assert found[1].basis is Basis.SUBJECTIVE and found[1].start_frame == 25


def test_a_continuity_finding_that_is_probably_style_is_an_opinion() -> None:
    def issue_of(ident: str, style: float, detect: float) -> f.ContinuityIssueFacts:
        return f.ContinuityIssueFacts(
            ident,
            "jump_cut",
            "high",
            Fraction(4),
            "a jump",
            "why",
            detect,
            style,
            ("s1",),
            "cover",
            True,
        )

    found = carry_continuity(
        clean(), f.ContinuityFacts("c1", (issue_of("i1", 0.8, 0.9), issue_of("i2", 0.1, 0.9)))
    )

    by_id = {i.analysis_refs[0] + str(n): i for n, i in enumerate(found)}
    assert [i.basis for i in found] == [Basis.SUBJECTIVE, Basis.OBJECTIVE]
    assert all(
        i.origin == "continuity_intelligence" and i.code == "continuity.jump_cut" for i in found
    )
    assert len(by_id) == 2


def story(
    *sections: str, locked: tuple[str, ...] = (), links: tuple[f.StoryLinkFacts, ...] = ()
) -> f.StoryFacts:
    return f.StoryFacts(
        "story1",
        tuple(f.StorySectionFacts(s, s.title(), n, s in locked) for n, s in enumerate(sections)),
        links,
    )


def timeline_with_sections(*ids: str) -> f.TimelineFacts:
    return replace(
        clean(),
        sections=tuple(f.SectionFacts(s, s, n * 50, (n + 1) * 50, ()) for n, s in enumerate(ids)),
    )


def test_a_story_plan_that_does_not_refer_to_the_timeline_is_not_compared() -> None:
    outcome = compare_story(clean(), story("hook", "body"))

    assert not outcome.comparable and outcome.issues == ()


def test_a_planned_section_missing_or_out_of_order_is_found() -> None:
    plan = story("hook", "body", "end", locked=("end",))

    missing = compare_story(timeline_with_sections("hook", "end"), plan)
    order = compare_story(timeline_with_sections("end", "hook", "body"), plan)

    assert codes(list(missing.issues)) == ["story.section_missing"]
    assert (
        one(list(order.issues), "story.order_deviates").severity is Severity.HIGH
    )  # a locked order


def test_a_setup_without_its_payoff_is_found() -> None:
    plan = story(
        "hook", "body", "end", links=(f.StoryLinkFacts("question", "hook", "end", "open"),)
    )

    gone = compare_story(timeline_with_sections("hook", "body"), plan)

    assert "story.payoff_missing" in codes(list(gone.issues))


# --- sampling --------------------------------------------------------------------------------


def test_the_moments_to_look_at_are_bounded_deterministic_and_inside_their_clips() -> None:
    facts = clean()

    plan = plan_samples(facts, GENERIC)

    assert plan == plan_samples(facts, GENERIC)
    assert 0 < len(plan) <= GENERIC.max_frames
    tags = {p.tag for p in plan}
    assert {"edge:start", "edge:end", "cut:100:before", "cut:100:after"} <= tags
    cut_after = next(p for p in plan if p.tag == "cut:100:after")
    assert Fraction(4) < cut_after.at < Fraction(5)
    assert len({p.at for p in plan}) == len(plan)
    assert len(plan_samples(facts, replace(GENERIC, max_frames=3))) <= 3
