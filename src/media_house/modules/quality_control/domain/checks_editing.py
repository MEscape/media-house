"""Checks of the edit itself: shot lengths, repetition, jump cuts, opening, ending, variation.

Most of these are opinions, not measurements, and are marked so: a subjective finding is never
worse than MEDIUM and never blocks an export. The editorial intent (story, pacing, continuity)
belongs to its own modules; this reads the edit as a person would and compares it with their plans
elsewhere (``checks_external``).
"""

from fractions import Fraction
from itertools import pairwise

from media_house.modules.quality_control.domain.classify import make_issue
from media_house.modules.quality_control.domain.facts import ClipFacts, TimelineFacts
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.profile import QualityProfile


def check_edit(timeline: TimelineFacts, profile: QualityProfile) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    fps = float(timeline.settings.fps)
    primary = timeline.primary()
    for clip in primary:
        frames = clip.end_frame - clip.start_frame
        seconds = frames / fps
        if frames <= profile.flash_frames:
            issues.append(
                make_issue(
                    timeline,
                    "editing.flash_frame",
                    f"'{clip.name}' is only {frames} frame(s) long.",
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                    measurements=(("clip", clip.clip_id), ("frames", str(frames))),
                )
            )
        elif seconds < profile.min_shot_seconds and not clip.freezes:
            issues.append(
                make_issue(
                    timeline,
                    "editing.shot_too_short",
                    f"'{clip.name}' is {seconds:.2f} s; this kind of video usually holds a shot "
                    f"for at least {profile.min_shot_seconds:g} s.",
                    confidence=0.5,
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                    measurements=(("clip", clip.clip_id), ("seconds", f"{seconds:.2f}")),
                )
            )
        elif seconds > profile.max_shot_seconds:
            issues.append(
                make_issue(
                    timeline,
                    "editing.shot_too_long",
                    f"'{clip.name}' runs {seconds:.1f} s without a cut.",
                    confidence=0.5,
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                    measurements=(("clip", clip.clip_id), ("seconds", f"{seconds:.2f}")),
                )
            )
    issues += _repeats(timeline, primary, profile)
    issues += _jump_cuts(timeline, primary, profile)
    issues += _opening_and_ending(timeline, primary, profile)
    issues += _static_stretches(timeline, primary, profile)
    return issues


def _span(clip: ClipFacts) -> tuple[Fraction, Fraction] | None:
    if (
        clip.original_in is None
        or clip.original_out is None
        or clip.original_out <= clip.original_in
    ):
        return None
    return clip.original_in, clip.original_out


def _repeats(
    timeline: TimelineFacts, primary: tuple[ClipFacts, ...], profile: QualityProfile
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    for i, a in enumerate(primary):
        span_a = _span(a)
        for b in primary[i + 1 :]:
            span_b = _span(b)
            if not span_a or not span_b or a.original_asset_id != b.original_asset_id:
                continue
            overlap = min(span_a[1], span_b[1]) - max(span_a[0], span_b[0])
            shorter = min(span_a[1] - span_a[0], span_b[1] - span_b[0])
            if overlap > 0 and overlap / shorter >= profile.repeat_overlap_fraction:
                issues.append(
                    make_issue(
                        timeline,
                        "editing.repeated_footage",
                        f"'{a.name}' and '{b.name}' show the same {float(overlap):.1f} s of the "
                        f"recording.",
                        confidence=0.85,
                        start_frame=b.start_frame,
                        end_frame=b.end_frame,
                        clips=(a, b),
                        evidence=(f"{float(overlap / shorter):.0%} of the shorter clip overlaps",),
                        measurements=(("keep_clip", a.clip_id), ("duplicate_clip", b.clip_id)),
                    )
                )
    return issues


def _jump_cuts(
    timeline: TimelineFacts, primary: tuple[ClipFacts, ...], profile: QualityProfile
) -> list[QualityIssue]:
    if profile.jump_cuts_expected:
        return []
    covered = [c for c in timeline.clips if c.kind in ("cover", "graphic", "vfx")]
    issues: list[QualityIssue] = []
    for a, b in pairwise(primary):
        span_a, span_b = _span(a), _span(b)
        if not span_a or not span_b or a.original_asset_id != b.original_asset_id:
            continue
        gap = float(span_b[0] - span_a[1])
        if not -0.2 <= gap <= profile.jump_cut_gap_seconds:
            continue
        if any(c.start_frame <= a.end_frame < c.end_frame for c in covered):
            continue
        issues.append(
            make_issue(
                timeline,
                "editing.jump_cut",
                f"The cut between '{a.name}' and '{b.name}' stays inside one take "
                f"({gap:.2f} s of the recording skipped) with nothing covering it.",
                confidence=0.5,
                start_frame=a.end_frame,
                end_frame=a.end_frame,
                clips=(a, b),
            )
        )
    return issues


def _opening_and_ending(
    timeline: TimelineFacts, primary: tuple[ClipFacts, ...], profile: QualityProfile
) -> list[QualityIssue]:
    if not primary:
        return []
    fps = float(timeline.settings.fps)
    issues: list[QualityIssue] = []
    first, last = primary[0], primary[-1]
    if (first.end_frame - first.start_frame) / fps > profile.opening_max_seconds and not any(
        c.start_frame < profile.opening_max_seconds * fps and c.kind in ("cover", "graphic")
        for c in timeline.clips
    ):
        issues.append(
            make_issue(
                timeline,
                "editing.weak_opening",
                f"The first shot holds for {(first.end_frame - first.start_frame) / fps:.0f} s "
                f"with "
                "nothing added on top of it.",
                confidence=0.4,
                start_frame=first.start_frame,
                end_frame=first.end_frame,
                clips=(first,),
            )
        )
    sound_fades = any(
        s.fade_out > 0
        for s in timeline.sounds
        if s.end >= timeline.seconds(last.end_frame) - Fraction(1, 2)
    )
    if (
        not last.has_fade_out
        and not sound_fades
        and not any(
            t.mode in ("fade", "dip") and t.cut_frame >= last.start_frame
            for t in timeline.transitions
        )
    ):
        issues.append(
            make_issue(
                timeline,
                "editing.abrupt_ending",
                "The last shot ends with no fade in picture or sound.",
                confidence=0.4,
                start_frame=last.end_frame,
                end_frame=last.end_frame,
                clips=(last,),
            )
        )
    return issues


def _uncovered(start: int, end: int, busy: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The parts of [start, end) that no interval of ``busy`` covers."""
    free: list[tuple[int, int]] = []
    cursor = start
    for a, b in sorted(busy):
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            free.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        free.append((cursor, end))
    return free


def _static_stretches(
    timeline: TimelineFacts, primary: tuple[ClipFacts, ...], profile: QualityProfile
) -> list[QualityIssue]:
    fps = float(timeline.settings.fps)
    busy = [
        (c.start_frame, c.end_frame)
        for c in timeline.clips
        if c.kind in ("cover", "graphic", "vfx")
    ]
    runs: list[list[ClipFacts]] = []
    for clip in primary:
        if runs and clip.start_frame - runs[-1][-1].end_frame <= 1:
            runs[-1].append(clip)
        else:
            runs.append([clip])
    issues: list[QualityIssue] = []
    for run in runs:
        for a, b in _uncovered(run[0].start_frame, run[-1].end_frame, busy):
            if (b - a) / fps > profile.variation_window_seconds:
                inside = tuple(c for c in run if c.start_frame < b and c.end_frame > a)
                issues.append(
                    make_issue(
                        timeline,
                        "editing.static_stretch",
                        f"{(b - a) / fps:.0f} s of the main picture with no cover footage, graphic "
                        "or effect on top.",
                        confidence=0.35,
                        start_frame=a,
                        end_frame=b,
                        clips=inside[:1],
                        key=str(a),
                    )
                )
    return issues
