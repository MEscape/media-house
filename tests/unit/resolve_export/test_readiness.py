"""When the final export may be authorized: what can never be overridden and what a person may
knowingly accept."""

from dataclasses import replace

from media_house.modules.resolve_export.domain.readiness import (
    IssueFact,
    PolishProblemFact,
    ReadinessFacts,
    assess,
)
from media_house.modules.resolve_export.domain.values import BlockerKind

H = "hash-1"


def facts(**changes: object) -> ReadinessFacts:
    base = ReadinessFacts(
        timeline_hash=H, review_hash=H, quality_report_id="qr1", quality_hash=H,
        quality_issues=(), polish_report_id="fp1", polish_hash=H,
        polish_readiness="ready_for_final_review",
        polish_problems=(), mix_state="completed", mix_errors=(), offline_sources=(),
        resolve_hash=H, resolve_conflicts=(), profile_known=True, profile_problems=(),
        high_issues_block=True,
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def issue(ident: str, severity: str, blocks: bool = False) -> IssueFact:
    return IssueFact(ident, "x.y", severity, f"{ident} is wrong", blocks)


def test_a_version_that_is_checked_polished_mixed_and_in_resolve_is_clear() -> None:
    result = assess(facts())

    assert result.clear and result.can_authorize and result.warnings == ()


def test_a_critical_issue_is_a_hard_blocker_no_override_can_lift() -> None:
    result = assess(facts(quality_issues=(issue("q1", "critical", blocks=True),)))

    assert not result.can_authorize
    assert [(b.kind, b.hard) for b in result.hard] == [(BlockerKind.CRITICAL_ISSUE, True)]


def test_a_high_issue_stops_the_export_until_resolved_or_knowingly_accepted() -> None:
    result = assess(facts(quality_issues=(issue("q2", "high"),)))

    assert result.can_authorize and not result.clear  # a person may override it
    assert [b.blocker_id for b in result.soft] == ["q2"]
    assert assess(facts(quality_issues=(issue("q2", "high"),), high_issues_block=False)).clear


def test_medium_and_low_issues_do_not_stop_an_export() -> None:
    assert assess(facts(quality_issues=(issue("a", "medium"), issue("b", "low")))).clear


def test_reports_of_another_version_or_none_at_all_are_hard_blockers() -> None:
    stale_quality = assess(facts(quality_hash="old"))
    no_polish = assess(facts(polish_report_id="", polish_hash=""))
    stale_review = assess(facts(review_hash="older"))
    nothing = assess(facts(quality_report_id="", quality_hash=""))

    assert stale_quality.hard[0].kind is BlockerKind.NOT_CURRENT
    assert no_polish.hard[0].kind is BlockerKind.NO_POLISH_REPORT
    assert stale_review.hard[0].kind is BlockerKind.NOT_CURRENT
    assert nothing.hard[0].kind is BlockerKind.NO_QUALITY_REPORT


def test_a_problem_the_polish_found_is_soft_unless_it_means_the_video_is_unfinished() -> None:
    placeholder = PolishProblemFact("placeholder.caption_text", "reads TODO", "", 40)
    unfinished = PolishProblemFact("unfinished.element", "a graphic is missing", "", 60)

    result = assess(facts(polish_problems=(placeholder, unfinished)))

    assert [(b.kind, b.hard) for b in result.soft] == [(BlockerKind.POLISH_PROBLEM, False)]
    assert [(b.kind, b.hard) for b in result.hard] == [(BlockerKind.UNFINISHED, True)]
    assert result.soft[0].blocker_id == "polish:placeholder.caption_text:40"


def test_the_same_finding_through_quality_control_and_the_polish_is_listed_once() -> None:
    both = assess(
        facts(
            quality_issues=(issue("q9", "high"),),
            polish_problems=(PolishProblemFact("graphic.overlap", "overlap", "q9", 10),),
        )
    )

    assert [b.blocker_id for b in both.soft] == ["q9"]


def test_a_mix_that_is_not_finished_media_that_is_offline_or_resolve_out_of_date_are_hard() -> None:
    result = assess(
        facts(
            mix_state="failed",
            mix_errors=("clipping",),
            offline_sources=("rec1.mov",),
            resolve_hash="old",
            resolve_conflicts=("v:pB",),
        )
    )

    kinds = {b.kind for b in result.hard}
    assert {
        BlockerKind.MIX_NOT_READY,
        BlockerKind.MEDIA_OFFLINE,
        BlockerKind.RESOLVE_STALE,
        BlockerKind.RESOLVE_CONFLICT,
    } <= kinds
    assert not result.can_authorize


def test_a_timeline_never_carried_out_in_resolve_cannot_be_exported_through_it() -> None:
    assert assess(facts(resolve_hash=None)).hard[0].kind is BlockerKind.NOT_IN_RESOLVE


def test_an_output_profile_the_render_manager_does_not_know_or_that_does_not_fit_is_hard() -> None:
    unknown = assess(facts(profile_known=False))
    unfit = assess(facts(profile_problems=("9:16 needs 1080x1920",)))

    assert unknown.hard[0].kind is BlockerKind.UNKNOWN_PROFILE
    assert unfit.hard[0].kind is BlockerKind.PROFILE_UNUSABLE


def test_a_polish_that_ended_with_warnings_is_a_note_not_a_blocker() -> None:
    result = assess(facts(polish_readiness="ready_with_warnings"))

    assert result.clear and "warnings" in result.warnings[0]
