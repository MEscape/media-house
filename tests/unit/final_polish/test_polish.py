"""The final polish: the presentation checks, what it carries from other modules, readiness, and
its identity."""

from dataclasses import replace
from fractions import Fraction

import pytest

from media_house.modules.final_polish.domain import checks
from media_house.modules.final_polish.domain import facts as f
from media_house.modules.final_polish.domain import model as f2
from media_house.modules.final_polish.domain.errors import InvalidPolishDocument
from media_house.modules.final_polish.domain.model import FinalPolishReport
from media_house.modules.final_polish.domain.report import assemble
from media_house.modules.final_polish.domain.serialization import report_from_json, report_to_json
from media_house.modules.final_polish.domain.values import (
    Readiness,
    SectionState,
    Severity,
    Source,
)

FPS = Fraction(25)


def clip(clip_id: str, kind: str, start: int, end: int, **kw: object) -> f.PolishClip:
    base = f.PolishClip(
        clip_id, kind, f"{kind} {clip_id}", start, end, True, False, "media_house", "s1"
    )
    return replace(base, **kw)  # type: ignore[arg-type]


def timeline(**changes: object) -> f.PolishTimeline:
    base = f.PolishTimeline(
        timeline_id="tl", version=2, content_hash="h2", anchor_asset_id="anchor", project_id="p",
        fps=FPS, duration_frames=200,
        clips=(
            clip("pA", "primary", 0, 100),
            clip("pB", "primary", 100, 200),
            clip("g1", "graphic", 25, 75),
        ),
        sounds=(f.PolishSound("a1", "music", Fraction(0), Fraction(8), Fraction(2), True),),
        transitions=(f.PolishTransition("t1", "dissolve", 100, 90, 110, "pA", "pB"),),
        markers=(), cues=(f.PolishCue(1, 0, 50, "hello there"),),
        sections=(f.PolishSection("s1", "Hook", 0, 200),),
    )  # fmt: skip
    return replace(base, **changes)  # type: ignore[arg-type]


def quality(*issues: f.QualityIssueFacts, not_run: tuple[str, ...] = ()) -> f.QualityFacts:
    return f.QualityFacts("qr1", tuple(issues), not_run)


def issue(code: str, severity: str = "medium", **kw: object) -> f.QualityIssueFacts:
    base = f.QualityIssueFacts(
        f"q_{code}", code, severity, "objective", f"{code} happened", False, 10, 20, (), ()
    )
    return replace(base, **kw)  # type: ignore[arg-type]


REVISIONS = f.RevisionFacts((), (), ())


def review(**kw: object) -> f.ReviewFacts:
    base = f.ReviewFacts("sess", 3, "h2", (), (), (), "Project", "Timeline")
    return replace(base, **kw)  # type: ignore[arg-type]


def report(
    tl: f.PolishTimeline | None = None,
    q: f.QualityFacts | None = None,
    *,
    mix: f.MixFacts | None = None,
    frames: tuple[f.FrameFacts, ...] = (),
    revisions: f.RevisionFacts = REVISIONS,
    context: f.ReviewFacts | None = None,
) -> FinalPolishReport:
    return assemble(
        timeline=tl or timeline(),
        quality=q if q is not None else quality(),
        mix=mix,
        frames=frames,
        revisions=revisions,
        review=context or review(),
        preview_asset_id="",
        created_at="t0",
        inputs=(("x", "1"),),
        warnings=(),
    )


def codes(section_findings: tuple[f2.Finding, ...]) -> list[str]:
    return [x.code for x in section_findings]


# --- carried from quality control ------------------------------------------------------------


def test_what_quality_control_found_lands_in_its_section_with_its_source_named() -> None:
    q = quality(
        issue("caption.overlap", "high"), issue("graphic.overlap"), issue("visual.crushed_shadows")
    )

    result = report(q=q)

    caption = result.caption_consistency_results
    assert (
        caption.state is SectionState.PROBLEM
        and caption.findings[0].source is Source.QUALITY_CONTROL
    )
    assert caption.findings[0].reference == "q_caption.overlap"
    assert result.graphic_consistency_results.state is SectionState.WARNING
    assert result.visual_consistency_results.findings[0].code == "visual.crushed_shadows"


def test_a_picture_check_that_did_not_run_is_not_said_to_be_fine() -> None:
    result = report(q=quality(not_run=("pictures",)))

    assert result.visual_consistency_results.state is SectionState.NOT_CHECKED
    assert "no preview" in result.visual_consistency_results.detail


# --- new here: opening, ending, transitions, leftovers ----


def test_a_clean_timeline_has_nothing_to_fix() -> None:
    result = report(mix=f.MixFacts("completed", "youtube", (), -14.0, -14.0, 1.0))

    assert result.readiness_status is Readiness.READY_FOR_FINAL_REVIEW
    assert result.remaining_issues == ()
    assert result.approval == "not_approved"


def test_a_picture_that_starts_late_or_stops_early_is_a_problem() -> None:
    tl = timeline(clips=(clip("pA", "primary", 10, 100), clip("pB", "primary", 100, 190)))

    section = checks.opening_and_ending(tl)

    assert {"opening.late_start", "ending.early_stop"} <= set(codes(section.findings))
    assert section.state is SectionState.PROBLEM


def test_a_text_that_appears_a_few_frames_before_the_end_may_be_cut_off() -> None:
    tl = timeline(clips=(*timeline().clips, clip("late", "caption", 195, 200)))

    section = checks.opening_and_ending(tl)

    assert (
        "ending.cut_off_text" in codes(section.findings) and section.state is SectionState.WARNING
    )


def test_an_ending_with_no_fade_is_a_note_not_a_problem() -> None:
    tl = timeline(
        sounds=(f.PolishSound("a1", "music", Fraction(0), Fraction(8), Fraction(0), True),)
    )

    findings = checks.opening_and_ending(tl).findings

    assert next(x for x in findings if x.code == "ending.no_close").severity is Severity.NOTE


def test_transitions_too_close_together_and_too_many_kinds_are_found() -> None:
    cuts = tuple(
        f.PolishTransition(f"t{i}", mode, 50 + 10 * i, 45 + 10 * i, 55 + 10 * i, "pA", "pB")
        for i, mode in enumerate(("dissolve", "fade", "dip", "wipe", "dissolve"))
    )
    section = checks.transition_review(timeline(transitions=cuts), quality())

    assert "transition.too_close" in codes(section.findings)
    assert "transition.many_kinds" in codes(section.findings)
    assert section.metrics[0] == ("transitions", "5")


def test_placeholder_text_is_a_problem_when_it_would_be_seen_and_a_warning_when_it_is_a_name() -> (
    None
):
    tl = timeline(
        cues=(f.PolishCue(1, 0, 40, "TODO write the real caption"),),
        clips=(*timeline().clips, clip("tmp", "cover", 30, 60, name="placeholder broll")),
    )

    section = checks.placeholder_detection(tl)

    by_code = {x.code: x for x in section.findings}
    assert by_code["placeholder.caption_text"].severity is Severity.PROBLEM
    assert by_code["placeholder.clip_name"].severity is Severity.WARNING


def test_an_element_that_is_unresolved_is_unfinished_and_leftovers_are_noted() -> None:
    tl = timeline(
        clips=(
            *timeline().clips,
            clip("miss", "graphic", 10, 40, unresolved=True),
            clip("off", "cover", 50, 60, enabled=False),
        ),
        markers=(f.PolishMarker("m1", "review", 20, "check this", ""),),
    )

    section = checks.placeholder_detection(tl)

    found = {x.code: x.severity for x in section.findings}
    assert found["unfinished.element"] is Severity.PROBLEM
    assert found["leftover.disabled_clips"] is Severity.NOTE
    assert found["leftover.review_markers"] is Severity.WARNING


def test_a_black_first_or_last_frame_is_only_a_note_when_a_fade_is_planned() -> None:
    black = (f.FrameFacts("first", 2, 0.99, 3.0), f.FrameFacts("last", 198, 0.99, 3.0))
    no_fade = timeline(transitions=())

    assert checks.final_frames(no_fade, black).state is SectionState.WARNING
    assert (
        checks.final_frames(
            timeline(transitions=(f.PolishTransition("t", "fade", 190, 180, 200, "pB", None),)),
            black,
        ).state
        is SectionState.OK
    )
    assert checks.final_frames(timeline(), ()).state is SectionState.NOT_CHECKED


# --- the review's own decisions --------------------------------------------------------------


def test_a_rejected_change_that_was_applied_and_an_approved_one_that_was_not_are_found() -> None:
    revisions = f.RevisionFacts(("rp1",), ("v:pB",), ("rp2",))

    section = checks.presentation_validation(timeline(), quality(), revisions, review())

    found = {x.code: x.severity for x in section.findings}
    assert found["validation.rejected_applied"] is Severity.PROBLEM
    assert found["validation.approved_not_applied"] is Severity.WARNING
    assert ("approved revisions included", "1") in section.metrics


def test_a_locked_section_that_a_person_changed_is_noted_and_a_person_edit_is_counted_as_kept() -> (
    None
):
    tl = timeline(
        clips=(
            clip("pA", "primary", 0, 100, origin="human_manual"),
            clip("pB", "primary", 100, 200),
        )
    )

    section = checks.presentation_validation(
        tl, quality(), REVISIONS, review(locked_sections=("s1",), drifted_locks=("s1",))
    )

    assert "validation.locked_changed" in codes(section.findings)
    assert ("clips edited by a person (kept)", "1") in section.metrics


def test_the_report_must_be_about_the_version_the_review_is_at() -> None:
    section = checks.presentation_validation(
        timeline(), quality(), REVISIONS, review(session_content_hash="another")
    )

    assert (
        section.findings[0].code == "validation.not_current"
        and section.state is SectionState.PROBLEM
    )


def test_an_export_blocking_issue_still_open_makes_the_video_not_ready() -> None:
    blocking = issue("video.black_frames", "critical", blocks_export=True)

    result = report(q=quality(blocking))

    assert result.readiness_status is Readiness.NOT_READY
    assert (
        any("blocks" in r.lower() or "black" in r.lower() for r in result.recommendations)
        or result.remaining_issues
    )
    assert result.remaining_issues[0].severity is Severity.PROBLEM


def test_a_missing_quality_report_is_a_problem_not_a_pass() -> None:
    result = assemble(
        timeline=timeline(),
        quality=None,
        mix=None,
        frames=(),
        revisions=REVISIONS,
        review=review(),
        preview_asset_id="",
        created_at="t",
        inputs=(),
        warnings=(),
    )

    assert result.readiness_status is Readiness.NOT_READY
    assert result.presentation_validation.findings[0].code == "validation.no_quality_report"


def test_a_mix_that_is_not_finished_or_has_errors_is_a_problem_and_no_mix_is_a_warning() -> None:
    mix = f.MixFacts(
        "failed", "youtube", (("clipping", "error", "the mix clips"),), -9.0, -14.0, 1.0
    )

    bad = checks.audio_refinement(timeline(), quality(), mix)
    none = checks.audio_refinement(timeline(), quality(), None)

    assert {"audio.mix.clipping", "audio.mix_not_ready"} <= set(
        codes(bad.findings)
    ) and bad.state is SectionState.PROBLEM
    assert none.findings[0].code == "audio.no_mix" and none.state is SectionState.WARNING


# --- the report ------------------------------------------------------------------------------


def test_the_identity_comes_from_the_inputs_and_the_package_names_what_a_person_reviews() -> None:
    a, b = report(), report()
    other = report(tl=timeline(content_hash="h3"))

    assert a.final_polish_report_id == b.final_polish_report_id and a.content_hash == b.content_hash
    assert other.final_polish_report_id != a.final_polish_report_id
    package = a.final_review_package
    assert (package.timeline_version, package.review_version, package.quality_report_id) == (
        2,
        3,
        "qr1",
    )
    assert (package.resolve_project, package.resolve_timeline) == ("Project", "Timeline")
    assert len(package.sections) == 9


def test_a_report_round_trips_and_a_foreign_document_is_refused() -> None:
    result = report(q=quality(issue("caption.overlap")))

    assert report_from_json(report_to_json(result)) == result
    with pytest.raises(InvalidPolishDocument):
        report_from_json('{"document_type": "other"}')
