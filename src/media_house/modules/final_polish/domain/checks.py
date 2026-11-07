"""The presentation checks of the final polish.

It reuses, it does not repeat. Findings another module already made (the quality report, the audio
mix) are carried into the section they belong to, with their source named, so the report reads as
one picture; what is new here is what only the finished presentation shows: the opening and the
ending, the transitions as a whole, leftovers (placeholders, unresolved elements, markers), and
whether the review's own decisions are reflected in the timeline (approved revisions in, rejected
ones not, locked sections as they were, a person's edits kept).
"""

import re
from collections.abc import Iterable
from itertools import pairwise

from media_house.modules.final_polish.domain.facts import (
    FrameFacts,
    MixFacts,
    PolishTimeline,
    QualityFacts,
    QualityIssueFacts,
    ReviewFacts,
    RevisionFacts,
)
from media_house.modules.final_polish.domain.model import Finding, SectionResult
from media_house.modules.final_polish.domain.values import SectionState, Severity, Source

_PLACEHOLDER = re.compile(
    r"\b(todo|tbd|placeholder|lorem|ipsum|xxx+|dummy|sample text|temp|temporary|debug|untitled|"
    r"insert (title|text|logo)|your text here|new title)\b",
    re.IGNORECASE,
)
_SEVERITY = {
    "critical": Severity.PROBLEM,
    "high": Severity.PROBLEM,
    "medium": Severity.WARNING,
    "low": Severity.NOTE,
    "optional": Severity.NOTE,
}
#: Transitions closer together than this (seconds) feel busy.
_CLOSE_SECONDS = 1.0
_OPENING_SECONDS = 5.0


def _state(findings: Iterable[Finding]) -> SectionState:
    levels = {f.severity for f in findings}
    if Severity.PROBLEM in levels:
        return SectionState.PROBLEM
    if Severity.WARNING in levels:
        return SectionState.WARNING
    return SectionState.OK


def _section(
    name: str,
    findings: list[Finding],
    *,
    ran: bool = True,
    detail: str = "",
    metrics: tuple[tuple[str, str], ...] = (),
) -> SectionResult:
    if not ran:
        return SectionResult(
            name=name, state=SectionState.NOT_CHECKED, detail=detail, metrics=metrics
        )
    return SectionResult(
        name=name, state=_state(findings), findings=tuple(findings), metrics=metrics, detail=detail
    )


def _carry(issue: QualityIssueFacts) -> Finding:
    return Finding(
        code=issue.code,
        severity=_SEVERITY.get(issue.severity, Severity.NOTE),
        message=issue.description,
        source=Source.QUALITY_CONTROL,
        start_frame=issue.start_frame,
        end_frame=issue.end_frame,
        clips=issue.clips,
        reference=issue.issue_id,
        evidence_assets=issue.evidence_assets,
    )


def _carried(quality: QualityFacts, prefixes: tuple[str, ...]) -> list[Finding]:
    return [_carry(i) for i in quality.issues if i.code.startswith(prefixes)]


# --- sections made largely of what quality control found -------------------------------------


def visual_consistency(quality: QualityFacts) -> SectionResult:
    found = _carried(quality, ("visual.", "structure.stretch", "structure.heavy_upscale"))
    ran = "pictures" not in quality.not_run
    return _section(
        "visual consistency",
        found,
        ran=ran or bool(found),
        detail="the quality checks that look at pictures did not run (no preview render)",
    )


def graphic_consistency(quality: QualityFacts) -> SectionResult:
    return _section("graphic consistency", _carried(quality, ("graphic.",)))


def caption_consistency(quality: QualityFacts) -> SectionResult:
    return _section("caption consistency", _carried(quality, ("caption.",)))


# --- transitions as a whole ------------------------------------------------------------------


def transition_review(timeline: PolishTimeline, quality: QualityFacts) -> SectionResult:
    findings = _carried(quality, ("structure.transition", "structure.handles"))
    cuts = sorted(timeline.transitions, key=lambda t: t.cut_frame)
    fps = float(timeline.fps)
    for a, b in pairwise(cuts):
        if (b.cut_frame - a.cut_frame) / fps < _CLOSE_SECONDS:
            findings.append(
                Finding(
                    code="transition.too_close",
                    severity=Severity.WARNING,
                    message=(
                        f"Two transitions {(b.cut_frame - a.cut_frame) / fps:.1f} s apart "
                        f"(at {a.cut_frame / fps:.1f} s and {b.cut_frame / fps:.1f} s) feel busy."
                    ),
                    start_frame=a.cut_frame,
                    end_frame=b.cut_frame,
                    clips=tuple(x for x in (a.to_clip, b.from_clip) if x),
                )
            )
    clips = {c.clip_id: c for c in timeline.clips}
    for transition in cuts:
        length = transition.end_frame - transition.start_frame
        for neighbour in (transition.from_clip, transition.to_clip):
            clip = clips.get(neighbour or "")
            if (
                clip is not None
                and clip.end_frame > clip.start_frame
                and length > 0.4 * (clip.end_frame - clip.start_frame)
            ):
                findings.append(
                    Finding(
                        code="transition.too_long",
                        severity=Severity.NOTE,
                        message=f"A {length}-frame transition takes up most of '{clip.name}'.",
                        start_frame=transition.start_frame,
                        end_frame=transition.end_frame,
                        clips=(clip.clip_id,),
                    )
                )
                break
    modes = sorted({t.mode for t in timeline.transitions if t.mode != "cut"})
    if len(modes) > 3:
        findings.append(
            Finding(
                code="transition.many_kinds",
                severity=Severity.NOTE,
                message=(
                    f"{len(modes)} different kinds of transition ({', '.join(modes)}) may read as "
                    f"unplanned."
                ),
            )
        )
    metrics = (("transitions", str(len(cuts))), ("kinds", ", ".join(modes) or "none"))
    return _section("transition review", findings, metrics=metrics)


# --- sound -----------------------------------------------------------------------------------


def audio_refinement(
    timeline: PolishTimeline, quality: QualityFacts, mix: MixFacts | None
) -> SectionResult:
    findings = _carried(quality, ("audio.",))
    metrics: list[tuple[str, str]] = []
    ran = bool(timeline.sounds)
    if mix is not None:
        metrics.append(("mix state", mix.state))
        if mix.integrated_lufs is not None:
            metrics.append(("integrated loudness", f"{mix.integrated_lufs:.1f} LUFS"))
        findings.extend(
            Finding(
                code=f"audio.mix.{code}",
                severity=Severity.PROBLEM,
                message=message,
                source=Source.AUDIO_MIX_ENGINE,
            )
            for code, _severity, message in mix.errors
        )
        if mix.state not in ("completed", "partial", "ok"):
            findings.append(
                Finding(
                    code="audio.mix_not_ready",
                    severity=Severity.PROBLEM,
                    message=f"The audio mix is '{mix.state}', not a finished mix.",
                    source=Source.AUDIO_MIX_ENGINE,
                )
            )
    elif ran:
        findings.append(
            Finding(
                code="audio.no_mix",
                severity=Severity.WARNING,
                message=(
                    "The timeline has sound but no stored mix result: the delivery loudness is "
                    "unchecked."
                ),
            )
        )
    return _section(
        "audio refinement",
        findings,
        ran=ran,
        detail="the timeline has no sound",
        metrics=tuple(metrics),
    )


# --- opening and ending ----------------------------------------------------------------------


def opening_and_ending(timeline: PolishTimeline) -> SectionResult:
    findings: list[Finding] = []
    primary = sorted(
        (c for c in timeline.clips if c.kind == "primary" and c.enabled),
        key=lambda c: c.start_frame,
    )
    fps = float(timeline.fps)
    if not primary:
        return _section(
            "opening and ending", [], ran=False, detail="the timeline has no main picture"
        )
    first, last = primary[0], primary[-1]
    if first.start_frame > 1:
        findings.append(
            Finding(
                code="opening.late_start",
                severity=Severity.PROBLEM,
                message=(
                    f"The picture starts at frame {first.start_frame}, not at the start of the "
                    f"video."
                ),
                start_frame=0,
                end_frame=first.start_frame,
                clips=(first.clip_id,),
            )
        )
    if timeline.duration_frames - last.end_frame > 1:
        findings.append(
            Finding(
                code="ending.early_stop",
                severity=Severity.PROBLEM,
                message=(
                    f"The picture stops {(timeline.duration_frames - last.end_frame) / fps:.1f} s "
                    f"before the end."
                ),
                start_frame=last.end_frame,
                end_frame=timeline.duration_frames,
                clips=(last.clip_id,),
            )
        )
    if not any(
        c.kind in ("graphic", "caption") and c.start_frame < _OPENING_SECONDS * fps
        for c in timeline.clips
        if c.enabled
    ):
        findings.append(
            Finding(
                code="opening.bare",
                severity=Severity.NOTE,
                message=(
                    f"Nothing (title, graphic or caption) appears in the first "
                    f"{_OPENING_SECONDS:g} s."
                ),
            )
        )
    closing = any(
        t.mode in ("fade", "dip") and t.cut_frame >= last.start_frame for t in timeline.transitions
    )
    sound_closing = any(s.fade_out > 0 for s in timeline.sounds if s.enabled)
    if not closing and not sound_closing:
        findings.append(
            Finding(
                code="ending.no_close",
                severity=Severity.NOTE,
                message="The ending has no fade in picture or sound; check that it feels intended.",
                start_frame=timeline.duration_frames,
                end_frame=timeline.duration_frames,
            )
        )
    for clip in timeline.clips:
        if clip.kind in ("graphic", "caption") and clip.enabled:
            late = timeline.duration_frames - clip.start_frame
            if 0 < late <= 10 and clip.end_frame >= timeline.duration_frames - 1:
                findings.append(
                    Finding(
                        code="ending.cut_off_text",
                        severity=Severity.WARNING,
                        message=(
                            f"'{clip.name}' appears {late} frames before the end: it may be cut "
                            f"off."
                        ),
                        start_frame=clip.start_frame,
                        end_frame=clip.end_frame,
                        clips=(clip.clip_id,),
                    )
                )
    return _section(
        "opening and ending",
        findings,
        metrics=(
            ("first shot starts", f"{first.start_frame / fps:.2f} s"),
            ("last shot ends", f"{last.end_frame / fps:.2f} s"),
        ),
    )


def final_frames(timeline: PolishTimeline, frames: tuple[FrameFacts, ...]) -> SectionResult:
    if not frames:
        return _section(
            "final frames", [], ran=False, detail="no preview render of this version was given"
        )
    findings: list[Finding] = []
    fades = any(t.mode in ("fade", "dip") for t in timeline.transitions)
    for frame in frames:
        if frame.label in ("first", "last") and frame.black_fraction > 0.95:
            findings.append(
                Finding(
                    code=f"frame.{frame.label}_black",
                    severity=Severity.NOTE if fades else Severity.WARNING,
                    message=f"The {frame.label} frame is black"
                    + (
                        " (a fade is planned)."
                        if fades
                        else " and no fade or dip to black explains it."
                    ),
                    start_frame=frame.frame,
                    end_frame=frame.frame,
                    evidence_assets=(frame.asset_id,) if frame.asset_id else (),
                )
            )
    return _section("final frames", findings, metrics=(("frames looked at", str(len(frames))),))


# --- leftovers -------------------------------------------------------------------------------


def placeholder_detection(timeline: PolishTimeline) -> SectionResult:
    findings: list[Finding] = []
    for cue in timeline.cues:
        if _PLACEHOLDER.search(cue.text):
            findings.append(
                Finding(
                    code="placeholder.caption_text",
                    severity=Severity.PROBLEM,
                    message=f"Caption {cue.index} reads like placeholder text: '{cue.text[:60]}'.",
                    start_frame=cue.start_frame,
                    end_frame=cue.end_frame,
                )
            )
    for clip in timeline.clips:
        if not clip.enabled:
            continue
        if _PLACEHOLDER.search(clip.name):
            findings.append(
                Finding(
                    code="placeholder.clip_name",
                    severity=Severity.WARNING,
                    message=f"'{clip.name}' looks like a placeholder or temporary item.",
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip.clip_id,),
                )
            )
        if clip.unresolved:
            findings.append(
                Finding(
                    code="unfinished.element",
                    severity=Severity.PROBLEM,
                    message=(
                        f"'{clip.name}' is unresolved: something it needs is missing from the "
                        f"final."
                    ),
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip.clip_id,),
                )
            )
    disabled = [c for c in timeline.clips if not c.enabled]
    if disabled:
        findings.append(
            Finding(
                code="leftover.disabled_clips",
                severity=Severity.NOTE,
                message=(
                    f"{len(disabled)} disabled clips remain on the timeline (they are not in the "
                    f"export)."
                ),
                clips=tuple(c.clip_id for c in disabled[:10]),
            )
        )
    open_markers = [m for m in timeline.markers if m.kind == "review"]
    if open_markers:
        findings.append(
            Finding(
                code="leftover.review_markers",
                severity=Severity.WARNING,
                message=(
                    f"{len(open_markers)} review markers are still on the timeline (not rendered, "
                    f"but unresolved)."
                ),
            )
        )
    for marker in timeline.markers:
        if _PLACEHOLDER.search(f"{marker.name} {marker.note}"):
            findings.append(
                Finding(
                    code="placeholder.marker",
                    severity=Severity.NOTE,
                    message=f"Marker '{marker.name}' looks like a leftover.",
                    start_frame=marker.frame,
                )
            )
    return _section("placeholder detection", findings)


# --- the review's own decisions --------------------------------------------------------------


def presentation_validation(
    timeline: PolishTimeline,
    quality: QualityFacts | None,
    revisions: RevisionFacts,
    review: ReviewFacts | None,
) -> SectionResult:
    findings: list[Finding] = []
    metrics: list[tuple[str, str]] = []
    if quality is None:
        findings.append(
            Finding(
                code="validation.no_quality_report",
                severity=Severity.PROBLEM,
                message="There is no quality report for this version of the timeline.",
                source=Source.REVIEW,
            )
        )
    else:
        findings.extend(
            Finding(
                code="validation.blocking_issue",
                severity=Severity.PROBLEM,
                message=f"Export-blocking issue still open: {issue.description}",
                source=Source.QUALITY_CONTROL,
                start_frame=issue.start_frame,
                end_frame=issue.end_frame,
                clips=issue.clips,
                reference=issue.issue_id,
            )
            for issue in quality.issues
            if issue.blocks_export
        )
        findings.extend(_carry(i) for i in quality.issues if i.code.startswith("story."))
    if review is not None and review.session_content_hash != timeline.content_hash:
        findings.append(
            Finding(
                code="validation.not_current",
                severity=Severity.PROBLEM,
                message="The timeline is not the version the review is at.",
                source=Source.REVIEW,
            )
        )
    for plan_id in revisions.approved_not_executed:
        findings.append(
            Finding(
                code="validation.approved_not_applied",
                severity=Severity.WARNING,
                message=f"Revision {plan_id} was approved but has not been carried out.",
                source=Source.REVIEW,
            )
        )
    for element in revisions.rejected_but_applied:
        findings.append(
            Finding(
                code="validation.rejected_applied",
                severity=Severity.PROBLEM,
                message=f"A change a person rejected was applied to {element}.",
                source=Source.REVIEW,
                clips=(element,),
            )
        )
    if review is not None:
        findings.extend(
            Finding(
                code="validation.locked_changed",
                severity=Severity.WARNING,
                message=(
                    f"Locked section '{section}' was changed by a person after it was locked (the "
                    f"lock holds)."
                ),
                source=Source.REVIEW,
            )
            for section in review.drifted_locks
        )
        metrics.append(("locked sections", str(len(review.locked_sections))))
        metrics.append(("approved revisions included", str(len(revisions.executed_plans))))
    manual = sum(1 for c in timeline.clips if c.origin == "human_manual")
    metrics.append(("clips edited by a person (kept)", str(manual)))
    return _section("presentation validation", findings, metrics=tuple(metrics))
