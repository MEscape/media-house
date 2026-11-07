"""Checks of the rendered preview: the picture, the sound, and the stream against the timeline.

The intent comes from the timeline (a dip to black, a freeze, a still image, a stretch with no
planned sound are deliberate); what is measured and not explained by it is a finding. Nothing is
decided from a single number: every finding carries the measurement it rests on.
"""

import math
from fractions import Fraction
from statistics import median

from media_house.modules.quality_control.domain.classify import make_issue
from media_house.modules.quality_control.domain.facts import ClipFacts, TimelineFacts
from media_house.modules.quality_control.domain.measurements import (
    FrameStats,
    RenderMeasurements,
    Span,
)
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.profile import QualityProfile
from media_house.modules.quality_control.domain.values import Severity

type Window = tuple[Fraction, Fraction]


# --- what the timeline says is deliberate ----------------------------------------------------


def intentional_black(timeline: TimelineFacts) -> list[Window]:
    """A fade or a dip to black is black on purpose (a little wider than the transition)."""
    pad = Fraction(2) / timeline.settings.fps
    return [
        (timeline.seconds(t.start_frame) - pad, timeline.seconds(t.end_frame) + pad)
        for t in timeline.transitions
        if t.mode in ("fade", "dip")
    ]


def intentional_stills(timeline: TimelineFacts) -> list[Window]:
    """A freeze frame, or a still image on the timeline, does not move on purpose."""
    windows: list[Window] = []
    for clip in timeline.clips:
        source = timeline.source(clip.source_id)
        if source is not None and source.media_type == "image":
            windows.append((timeline.seconds(clip.start_frame), timeline.seconds(clip.end_frame)))
        windows.extend((timeline.seconds(a), timeline.seconds(b)) for a, b in clip.freezes)
    return windows


def _covered(span: tuple[Fraction, Fraction], windows: list[Window], fraction: float = 0.8) -> bool:
    length = span[1] - span[0]
    if length <= 0:
        return True
    inside = sum(max(Fraction(0), min(span[1], b) - max(span[0], a)) for a, b in sorted(windows))
    return float(inside / length) >= fraction


def _planned_sound(timeline: TimelineFacts, span: tuple[Fraction, Fraction]) -> float:
    """The share of a stretch where an enabled sound clip is supposed to play."""
    length = span[1] - span[0]
    if length <= 0:
        return 0.0
    intervals = sorted(
        (max(s.start, span[0]), min(s.end, span[1]))
        for s in timeline.sounds
        if s.enabled
        and not s.unresolved
        and s.role in ("dialogue", "production", "music")
        and s.end > span[0]
        and s.start < span[1]
    )
    covered = Fraction(0)
    cursor = span[0]
    for a, b in intervals:
        a = max(a, cursor)
        if b > a:
            covered += b - a
            cursor = b
    return float(covered / length)


def _resolved(span: Span, duration: Fraction | None) -> tuple[Fraction, Fraction] | None:
    end = span.end if span.end is not None else duration
    return (span.start, end) if end is not None and end > span.start else None


def _clips_at(timeline: TimelineFacts, span: tuple[Fraction, Fraction]) -> tuple[ClipFacts, ...]:
    a, b = timeline.frame_at(span[0]), timeline.frame_at(span[1])
    return tuple(c for c in timeline.primary() if c.start_frame < max(b, a + 1) and c.end_frame > a)


def _frames(timeline: TimelineFacts, span: tuple[Fraction, Fraction]) -> tuple[int, int]:
    return timeline.frame_at(span[0]), timeline.frame_at(span[1])


# --- the stream and the file -----------------------------------------------------------------


def check_stream(
    timeline: TimelineFacts, profile: QualityProfile, m: RenderMeasurements
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    streams, settings = m.streams, timeline.settings
    if m.decode is not None and not m.decode.complete:
        issues.append(
            make_issue(
                timeline,
                "render.decode_errors",
                "The preview does not decode cleanly to the end.",
                confidence=0.95,
                evidence=m.decode.errors[:5],
                analysis_refs=(m.asset_id,),
            )
        )
    if streams is None:
        return issues
    if not streams.has_video:
        issues.append(
            make_issue(
                timeline,
                "video.no_video_stream",
                "The preview has no picture stream.",
                confidence=0.99,
            )
        )
        return issues
    if (
        streams.width
        and streams.height
        and (streams.width, streams.height) != (settings.width, settings.height)
    ):
        issues.append(
            make_issue(
                timeline,
                "render.size_mismatch",
                f"The preview is {streams.width}x{streams.height}; the timeline is "
                f"{settings.width}x{settings.height}.",
                analysis_refs=(m.asset_id,),
            )
        )
    if streams.fps and abs(float(streams.fps) - float(settings.fps)) > 0.01:
        issues.append(
            make_issue(
                timeline,
                "render.rate_mismatch",
                f"The preview runs at {float(streams.fps):.3f} fps; the timeline at "
                f"{float(settings.fps):.3f}.",
                analysis_refs=(m.asset_id,),
            )
        )
    expected = timeline.seconds(settings.duration_frames)
    if streams.duration is not None and abs(float(streams.duration - expected)) > max(
        profile.duration_tolerance_seconds, 2.0 / float(settings.fps)
    ):
        issues.append(
            make_issue(
                timeline,
                "render.duration_mismatch",
                f"The preview is {float(streams.duration):.2f} s; the timeline is "
                f"{float(expected):.2f} s.",
                start_frame=min(settings.duration_frames, timeline.frame_at(streams.duration)),
                end_frame=max(settings.duration_frames, timeline.frame_at(streams.duration)),
                analysis_refs=(m.asset_id,),
                evidence=(f"difference {float(streams.duration - expected):+.2f} s",),
            )
        )
    wants_sound = any(s.enabled and not s.unresolved for s in timeline.sounds)
    if wants_sound and not streams.has_audio:
        issues.append(
            make_issue(
                timeline,
                "audio.missing",
                "The timeline has sound but the preview has no audio stream.",
                confidence=0.95,
                analysis_refs=(m.asset_id,),
            )
        )
    if (
        streams.has_audio
        and streams.audio_duration is not None
        and streams.duration is not None
        and abs(float(streams.audio_duration - streams.duration)) > profile.sync_tolerance_seconds
    ):
        issues.append(
            make_issue(
                timeline,
                "audio.sync_drift",
                f"Sound is {float(streams.audio_duration):.2f} s and picture "
                f"{float(streams.duration):.2f} s long.",
                confidence=0.8,
                analysis_refs=(m.asset_id,),
            )
        )
    elif (
        streams.has_audio
        and streams.av_offset is not None
        and abs(streams.av_offset) > profile.sync_tolerance_seconds
    ):
        issues.append(
            make_issue(
                timeline,
                "audio.sync_drift",
                f"Sound starts {streams.av_offset:+.3f} s from the picture.",
                confidence=0.7,
                analysis_refs=(m.asset_id,),
            )
        )
    return issues


# --- black, frozen, silent -------------------------------------------------------------------


def check_black_and_frozen(
    timeline: TimelineFacts,
    profile: QualityProfile,
    m: RenderMeasurements,
    known_gaps: list[Window],
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    duration = m.streams.duration if m.streams else None
    deliberate_black = intentional_black(timeline)
    stills = intentional_stills(timeline)
    black: list[Window] = []
    for span in m.black or ():
        window = _resolved(span, duration)
        if window is None:
            continue
        black.append(window)
        if _covered(window, deliberate_black) or _covered(window, known_gaps):
            continue
        length = float(window[1] - window[0])
        start, end = _frames(timeline, window)
        edge = float(window[0]) <= 0.1 or (
            duration is not None and float(duration - window[1]) <= 0.1
        )
        if edge and length <= 0.25:
            issues.append(
                make_issue(
                    timeline,
                    "video.black_edge",
                    f"{length:.2f} s of black at the "
                    f"{'start' if float(window[0]) <= 0.1 else 'end'} of the preview.",
                    start_frame=start,
                    end_frame=end,
                    analysis_refs=(m.asset_id,),
                    evidence=(f"black from {float(window[0]):.2f} s to {float(window[1]):.2f} s",),
                )
            )
            continue
        clips = _clips_at(timeline, window)
        issues.append(
            make_issue(
                timeline,
                "video.black_frames",
                f"{length:.2f} s of black picture at {float(window[0]):.2f} s that nothing on the "
                f"timeline explains.",
                severity=Severity.CRITICAL if length >= 0.5 else Severity.HIGH,
                confidence=0.9,
                start_frame=start,
                end_frame=end,
                clips=clips,
                analysis_refs=(m.asset_id,),
                evidence=(f"black from {float(window[0]):.2f} s to {float(window[1]):.2f} s",),
            )
        )
    for span in m.frozen or ():
        window = _resolved(span, duration)
        if (
            window is None
            or _covered(window, stills)
            or _covered(window, black)
            or _covered(window, deliberate_black)
        ):
            continue
        length = float(window[1] - window[0])
        start, end = _frames(timeline, window)
        issues.append(
            make_issue(
                timeline,
                "video.frozen_frames",
                f"The picture does not move for {length:.1f} s at {float(window[0]):.2f} s and no "
                f"freeze frame or still image was planned there.",
                severity=Severity.HIGH if length >= 1.5 else Severity.MEDIUM,
                confidence=0.8,
                start_frame=start,
                end_frame=end,
                clips=_clips_at(timeline, window),
                analysis_refs=(m.asset_id,),
                evidence=(f"frozen from {float(window[0]):.2f} s to {float(window[1]):.2f} s",),
            )
        )
    del profile
    return issues


def check_silence(timeline: TimelineFacts, m: RenderMeasurements) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    duration = m.streams.duration if m.streams else None
    for span in m.silent or ():
        window = _resolved(span, duration)
        if window is None:
            continue
        planned = _planned_sound(timeline, window)
        if planned < 0.5:
            continue  # nothing was meant to play there: silence is on purpose
        start, end = _frames(timeline, window)
        issues.append(
            make_issue(
                timeline,
                "audio.unexpected_silence",
                f"{float(window[1] - window[0]):.1f} s of silence at {float(window[0]):.2f} s "
                f"where sound is planned.",
                confidence=0.75,
                start_frame=start,
                end_frame=end,
                analysis_refs=(m.asset_id,),
                evidence=(f"{planned:.0%} of the stretch has a sound clip on the timeline",),
            )
        )
    return issues


# --- pictures --------------------------------------------------------------------------------


def _primary_frame(timeline: TimelineFacts, frame: FrameStats) -> ClipFacts | None:
    index = timeline.frame_at(frame.at)
    return next((c for c in timeline.primary() if c.start_frame <= index < c.end_frame), None)


def check_pictures(
    timeline: TimelineFacts, profile: QualityProfile, m: RenderMeasurements
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    deliberate = intentional_black(timeline)
    samples = [
        f
        for f in m.frames
        if f.black_fraction < 0.95 and not _covered((f.at, f.at + Fraction(1, 100)), deliberate)
    ]
    sharp = [f.sharpness for f in samples if f.sharpness > 0]
    middle = median(sharp) if len(sharp) >= 3 else None
    for frame in samples:
        clip = _primary_frame(timeline, frame)
        at = timeline.frame_at(frame.at)
        seconds = float(frame.at)

        def flag(
            code: str,
            text: str,
            confidence: float,
            evidence: str = "",
            *,
            frame: FrameStats = frame,
            clip: ClipFacts | None = clip,
            at: int = at,
        ) -> None:
            issues.append(
                make_issue(
                    timeline,
                    code,
                    text,
                    confidence=confidence,
                    start_frame=at,
                    end_frame=at + 1,
                    clips=(clip,) if clip else (),
                    analysis_refs=(m.asset_id,),
                    evidence=(evidence,) if evidence else (),
                    key=frame.tag,
                )
            )

        if frame.crushed_fraction > profile.crushed_fraction_limit:
            flag(
                "visual.crushed_shadows",
                f"{frame.crushed_fraction:.0%} of the picture at {seconds:.1f} s is pure black "
                f"shadow.",
                0.6,
                f"crushed fraction {frame.crushed_fraction:.2f}",
            )
        if frame.clipped_fraction > profile.clipped_fraction_limit:
            flag(
                "visual.clipped_highlights",
                f"{frame.clipped_fraction:.0%} of the picture at {seconds:.1f} s is blown out.",
                0.6,
                f"clipped fraction {frame.clipped_fraction:.2f}",
            )
        if frame.luma_mean < profile.dark_luma_mean:
            flag(
                "visual.underexposed",
                f"The picture at {seconds:.1f} s is very dark (mean luma {frame.luma_mean:.0f} of "
                f"255).",
                0.55,
                f"mean luma {frame.luma_mean:.1f}",
            )
        elif frame.luma_mean > profile.bright_luma_mean:
            flag(
                "visual.overexposed",
                f"The picture at {seconds:.1f} s is very bright (mean luma {frame.luma_mean:.0f} "
                f"of 255).",
                0.55,
                f"mean luma {frame.luma_mean:.1f}",
            )
        if middle and clip is not None and 0 < frame.sharpness < profile.soft_focus_ratio * middle:
            flag(
                "visual.soft_focus",
                f"The picture at {seconds:.1f} s is much softer than the rest of the video.",
                0.35,
                f"sharpness {frame.sharpness:.0f} against a typical {middle:.0f}",
            )
        if timeline.settings.fit_mode in ("cover", "stretch") and clip is not None:
            top, bottom, left, right = frame.border_black
            if top + bottom > profile.border_limit or left + right > profile.border_limit:
                flag(
                    "visual.unexpected_borders",
                    f"Black borders ({top + bottom:.0%} high, {left + right:.0%} wide) at "
                    f"{seconds:.1f} s although the picture should fill the frame.",
                    0.7,
                )
            elif abs(top - bottom) > 0.03 or abs(left - right) > 0.03:
                flag(
                    "visual.bad_crop",
                    f"Unequal black borders at {seconds:.1f} s suggest an off-centre crop.",
                    0.55,
                )
    issues += _colour_jumps(timeline, profile, m, samples)
    return issues


def _ratio(frame: FrameStats) -> float | None:
    red, _green, blue = frame.mean_rgb
    return red / blue if red > 1 and blue > 1 else None


def _colour_jumps(
    timeline: TimelineFacts,
    profile: QualityProfile,
    m: RenderMeasurements,
    samples: list[FrameStats],
) -> list[QualityIssue]:
    before = {f.tag.split(":")[1]: f for f in samples if f.tag.endswith(":before")}
    after = {f.tag.split(":")[1]: f for f in samples if f.tag.endswith(":after")}
    issues: list[QualityIssue] = []
    for cut, a in before.items():
        b = after.get(cut)
        ra, rb = _ratio(a), _ratio(b) if b else None
        if b is None or ra is None or rb is None:
            continue
        shift = abs(math.log(rb / ra))
        if shift > profile.color_shift_limit:
            frame = int(cut)
            issues.append(
                make_issue(
                    timeline,
                    "visual.color_mismatch",
                    f"The colour balance jumps at the cut at {float(timeline.seconds(frame)):.1f} "
                    f"s "
                    f"(red-to-blue ratio {ra:.2f} -> {rb:.2f}).",
                    confidence=0.45,
                    start_frame=frame,
                    end_frame=frame,
                    clips=tuple(
                        c
                        for c in (timeline.clip(a.clip_id or ""), timeline.clip(b.clip_id or ""))
                        if c
                    ),
                    analysis_refs=(m.asset_id,),
                    evidence=(f"colour shift {shift:.2f} (limit {profile.color_shift_limit:g})",),
                    key=cut,
                )
            )
    return issues


# --- sound -----------------------------------------------------------------------------------


def check_sound(
    timeline: TimelineFacts, profile: QualityProfile, m: RenderMeasurements
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    loud = m.loudness
    ref = (m.asset_id,)
    if loud is not None:
        value = loud.integrated_lufs
        if value is not None and abs(value - profile.target_lufs) > profile.tolerance_lu:
            off = value - profile.target_lufs
            issues.append(
                make_issue(
                    timeline,
                    "audio.loudness_off",
                    f"The preview measures {value:.1f} LUFS; the target is {profile.target_lufs:g} "
                    f"(+/- {profile.tolerance_lu:g}).",
                    severity=Severity.HIGH if abs(off) > 2 * profile.tolerance_lu + 2 else None,
                    confidence=0.9,
                    analysis_refs=ref,
                    evidence=(f"{off:+.1f} LU from the target",),
                    measurements=(
                        ("measured_lufs", f"{value:.2f}"),
                        ("target_lufs", f"{profile.target_lufs:g}"),
                        ("offset_lu", f"{off:.2f}"),
                    ),
                )
            )
        if (
            loud.true_peak_dbtp is not None
            and loud.true_peak_dbtp > profile.true_peak_ceiling_dbtp + 0.05
        ):
            issues.append(
                make_issue(
                    timeline,
                    "audio.true_peak",
                    f"True peak {loud.true_peak_dbtp:.1f} dBTP is above the "
                    f"{profile.true_peak_ceiling_dbtp:g} ceiling.",
                    confidence=0.9,
                    analysis_refs=ref,
                    measurements=(
                        ("measured_dbtp", f"{loud.true_peak_dbtp:.2f}"),
                        ("ceiling_dbtp", f"{profile.true_peak_ceiling_dbtp:g}"),
                    ),
                )
            )
        if loud.sample_peak_dbfs is not None and loud.sample_peak_dbfs >= profile.clipping_dbfs:
            issues.append(
                make_issue(
                    timeline,
                    "audio.clipping",
                    f"Samples reach {loud.sample_peak_dbfs:.2f} dBFS: the sound is at or beyond "
                    f"full scale.",
                    confidence=0.85,
                    analysis_refs=ref,
                    measurements=(("sample_peak_dbfs", f"{loud.sample_peak_dbfs:.2f}"),),
                )
            )
        if (
            loud.loudness_range_lu is not None
            and loud.loudness_range_lu > profile.loudness_range_limit_lu
        ):
            issues.append(
                make_issue(
                    timeline,
                    "audio.range_wide",
                    f"Loudness range {loud.loudness_range_lu:.1f} LU (more than "
                    f"{profile.loudness_range_limit_lu:g}).",
                    confidence=0.7,
                    analysis_refs=ref,
                )
            )
    levels = [d.integrated_lufs for d in m.dialogue_levels if d.integrated_lufs is not None]
    if len(levels) >= 3 and max(levels) - min(levels) > profile.dialogue_spread_lu:
        low = min(
            (d for d in m.dialogue_levels if d.integrated_lufs is not None),
            key=lambda d: d.integrated_lufs or 0.0,
        )
        high = max(
            (d for d in m.dialogue_levels if d.integrated_lufs is not None),
            key=lambda d: d.integrated_lufs or 0.0,
        )
        sounds = {s.clip_id: s for s in timeline.sounds}
        start = sounds.get(low.clip_id)
        issues.append(
            make_issue(
                timeline,
                "audio.dialogue_inconsistent",
                f"Dialogue levels differ by {max(levels) - min(levels):.1f} LU between clips "
                f"(limit {profile.dialogue_spread_lu:g}).",
                confidence=0.7,
                start_frame=timeline.frame_at(start.start) if start else None,
                end_frame=timeline.frame_at(start.end) if start else None,
                tracks=tuple(s.track_id for s in (start, sounds.get(high.clip_id)) if s),
                analysis_refs=ref,
                evidence=(
                    f"quietest {low.clip_id}: {low.integrated_lufs:.1f} LUFS; loudest "
                    f"{high.clip_id}: {high.integrated_lufs:.1f} LUFS",
                ),
                measurements=(
                    ("quietest_clip", low.clip_id),
                    ("quietest_lufs", f"{low.integrated_lufs or 0.0:.2f}"),
                    ("loudest_clip", high.clip_id),
                    ("loudest_lufs", f"{high.integrated_lufs or 0.0:.2f}"),
                    ("median_lufs", f"{median(levels):.2f}"),
                ),
            )
        )
    if (
        m.end_peak_dbfs is not None
        and m.end_peak_dbfs > profile.abrupt_end_dbfs
        and not any(s.fade_out > 0 for s in timeline.sounds if s.enabled)
    ):
        end = timeline.settings.duration_frames
        issues.append(
            make_issue(
                timeline,
                "audio.abrupt_end",
                f"The sound is still at {m.end_peak_dbfs:.0f} dBFS in the last quarter second and "
                f"no sound clip fades out.",
                confidence=0.6,
                start_frame=end,
                end_frame=end,
                analysis_refs=ref,
            )
        )
    return issues


def run_render_checks(
    timeline: TimelineFacts,
    profile: QualityProfile,
    m: RenderMeasurements,
    known_gaps: list[Window],
) -> list[QualityIssue]:
    return [
        *check_stream(timeline, profile, m),
        *check_black_and_frozen(timeline, profile, m, known_gaps),
        *check_silence(timeline, m),
        *check_pictures(timeline, profile, m),
        *check_sound(timeline, profile, m),
    ]
