"""QC of a transition plan (contracts sec. 6): frame accuracy, handles, overlaps, technical
compatibility, audio continuity through speech, and repetition.

CRITICAL = the plan is invalid (a planner bug) and is never stored. WARNING / INFO are recorded.
A CREATIVE finding is never above WARNING.
"""

from collections.abc import Mapping
from fractions import Fraction
from itertools import pairwise

from media_house.modules.transition_engine.domain.facts import (
    SourceFacts,
    TransitionClip,
    TransitionSequence,
    fr,
)
from media_house.modules.transition_engine.domain.model import QcIssue, Transition
from media_house.modules.transition_engine.domain.profile import TransitionProfile
from media_house.modules.transition_engine.domain.timing import time_of
from media_house.modules.transition_engine.domain.values import (
    CUT_KINDS,
    LIBRARY,
    AudioKind,
    QcCategory,
    Severity,
)


def _issue(
    code: str,
    severity: Severity,
    message: str,
    t: Transition | None = None,
    *,
    creative: bool = False,
    evidence: str = "",
) -> QcIssue:
    return QcIssue(
        code=code,
        severity=severity,
        category=QcCategory.CREATIVE if creative else QcCategory.TECHNICAL,
        message=message,
        item_id=t.transition_id if t else None,
        timeline_start=t.timeline_start if t else None,
        timeline_end=t.timeline_end if t else None,
        evidence=evidence,
    )


def check_plan(
    transitions: tuple[Transition, ...],
    sequence: TransitionSequence,
    sources: Mapping[str, SourceFacts],
    profile: TransitionProfile,
) -> list[QcIssue]:
    fps = fr(sequence.frame_rate)
    clips = {c.clip_id: c for c in sequence.clips}
    found: list[QcIssue] = []
    found += _frames(transitions, sequence, fps)
    found += _overlaps(transitions)
    for t in transitions:
        spec = LIBRARY[t.kind]
        if spec.overlap and not t.handles.sufficient:
            found.append(
                _issue(
                    "handles.insufficient",
                    Severity.CRITICAL,
                    f"{t.kind} needs {t.handles.required_out}/{t.handles.required_in} frames of"
                    f" handle, the sources hold {t.handles.available_out}/"
                    f"{t.handles.available_in}",
                    t,
                )
            )
        if spec.overlap:
            found += _compatibility(t, clips, sources, fps)
        found += _speech(t, clips, sources, profile)
    found += _repetition(transitions, sequence, profile)
    if sequence.frame_rate_assumed:
        found.append(
            _issue(
                "timebase.assumed",
                Severity.INFO,
                "the edit's frame rate was assumed (VFR or unknown source): seconds are"
                " authoritative, frames are a projection",
            )
        )
    return found


def _frames(
    transitions: tuple[Transition, ...], sequence: TransitionSequence, fps: Fraction
) -> list[QcIssue]:
    found: list[QcIssue] = []
    for t in transitions:
        if (
            t.duration_frames != t.end_frame - t.start_frame
            or not t.start_frame <= t.cut_frame <= t.end_frame
            or fr(t.timeline_start) != time_of(t.start_frame, fps)
            or fr(t.timeline_end) != time_of(t.end_frame, fps)
            or fr(t.cut_at) != time_of(t.cut_frame, fps)
        ):
            found.append(
                _issue("frames.inconsistent", Severity.CRITICAL, "frames and times disagree", t)
            )
    for c in sequence.main():
        exact = fr(c.timeline_start) * fps
        if exact.denominator != 1:
            found.append(
                QcIssue(
                    code="frames.off_grid",
                    severity=Severity.WARNING,
                    category=QcCategory.TECHNICAL,
                    message=f"clip {c.clip_id} starts between frames ({float(exact):.3f}); the"
                    f" cut is snapped to frame {round(exact)}",
                    item_id=c.clip_id,
                    timeline_start=c.timeline_start,
                )
            )
    return found


def _overlaps(transitions: tuple[Transition, ...]) -> list[QcIssue]:
    found: list[QcIssue] = []
    ordered = sorted((t for t in transitions if t.duration_frames > 0), key=lambda t: t.start_frame)
    for a, b in pairwise(ordered):
        if a.end_frame > b.start_frame:
            found.append(
                _issue(
                    "transitions.overlap",
                    Severity.CRITICAL,
                    f"{a.transition_id} and {b.transition_id} cover the same frames",
                    b,
                )
            )
    return found


def _compatibility(
    t: Transition,
    clips: Mapping[str, TransitionClip],
    sources: Mapping[str, SourceFacts],
    fps: Fraction,
) -> list[QcIssue]:
    found: list[QcIssue] = []
    for clip_id in (t.outgoing_clip_id, t.incoming_clip_id):
        clip = clips.get(clip_id or "")
        source = sources.get(clip.asset_id) if clip else None
        if source is None:
            continue
        if source.variable_frame_rate:
            found.append(
                _issue(
                    "source.vfr",
                    Severity.WARNING,
                    f"{source.asset_id} has a variable frame rate: the handle was counted with a"
                    " safety frame; conform the source before rendering the overlap",
                    t,
                )
            )
        if source.frame_rate is not None and source.frame_rate != fps:
            found.append(
                _issue(
                    "source.frame_rate",
                    Severity.INFO,
                    f"{source.asset_id} runs at {float(source.frame_rate):.3f} fps, the edit at"
                    f" {float(fps):.3f}: handles are counted in edit frames",
                    t,
                )
            )
    return found


def _speech(
    t: Transition,
    clips: Mapping[str, TransitionClip],
    sources: Mapping[str, SourceFacts],
    profile: TransitionProfile,
) -> list[QcIssue]:
    """No word may lie inside an audio fade by more than the click guard (``min_ms``)."""
    a = t.audio
    if a.kind is AudioKind.NONE:
        return []
    slack = Fraction(profile.audio.min_ms, 1000)
    at = fr(a.timeline_at)
    found: list[QcIssue] = []
    out = clips.get(t.outgoing_clip_id or "")
    inc = clips.get(t.incoming_clip_id or "")
    cut = fr(inc.timeline_start) if inc else (out.timeline_end if out else at)
    fade_start, fade_end = at - fr(a.before), at + fr(a.after)
    for clip, side in ((out, "out"), (inc, "in")):
        if clip is None:
            continue
        source = sources.get(clip.asset_id)
        if source is None:
            continue
        anchor = fr(clip.source_end) if side == "out" else fr(clip.source_start)
        point = anchor + (at - cut)
        lo, hi = point - (at - fade_start), point + (fade_end - at)
        for w in source.words:
            straddles = w.start < point < w.end
            inside = min(w.end, hi) - max(w.start, lo)
            if not straddles and inside > slack:
                found.append(
                    _issue(
                        "audio.word_in_fade",
                        Severity.CRITICAL,
                        f"the {side} fade covers a word at {float(w.start):.3f} s",
                        t,
                    )
                )
                break
    return found


def _repetition(
    transitions: tuple[Transition, ...], sequence: TransitionSequence, profile: TransitionProfile
) -> list[QcIssue]:
    found: list[QcIssue] = []
    used = [t for t in transitions if t.kind not in CUT_KINDS]
    run: list[Transition] = []
    for t in used:
        if t.intensity >= 3 and run and run[-1].kind is t.kind:
            run.append(t)
        else:
            run = [t] if t.intensity >= 3 else []
        if len(run) == profile.budget.max_repeats + 1:
            found.append(
                _issue(
                    "repetition.same_kind",
                    Severity.WARNING,
                    f"{t.kind} is used {len(run)} times in a row: the pattern becomes tiring",
                    t,
                    creative=True,
                )
            )
    for a, b in pairwise(transitions):
        if a.intensity >= 3 and b.intensity >= 3:
            found.append(
                _issue(
                    "repetition.consecutive_expressive",
                    Severity.WARNING,
                    f"expressive transitions on adjacent boundaries ({a.kind}, {b.kind})",
                    b,
                    creative=True,
                )
            )
    main = sequence.main()
    minutes = max(c.timeline_end for c in main) / 60 if main else Fraction(0)
    counted = [t for t in used if t.status.value in ("recommended", "requested", "overridden")]
    if minutes > 0 and len(counted) > profile.budget.per_minute * float(minutes) + 1:
        found.append(
            _issue(
                "budget.exceeded",
                Severity.WARNING,
                f"{len(counted)} transitions in {float(minutes):.1f} min exceed the profile's"
                f" {profile.budget.per_minute}/min",
                creative=True,
            )
        )
    return found
