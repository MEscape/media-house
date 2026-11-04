"""QC of a sound design plan (Layer 4 convention: CRITICAL / ERROR / WARNING / INFO).

``quality_checks`` reports what a listener would notice: speech masking risk, music continuity,
effect overlap and density, clipping risk and licence state. ``validate_plan`` checks the plan's
own consistency; a CRITICAL finding means the planner is wrong and nothing is stored.
"""

import itertools
from fractions import Fraction

from media_house.modules.music_and_sound_design.domain.library import SoundIndex
from media_house.modules.music_and_sound_design.domain.model import (
    Credit,
    MissingAsset,
    MusicCue,
    QcIssue,
    SfxEvent,
    SoundDesignPlan,
)
from media_house.modules.music_and_sound_design.domain.profile import SoundProfile
from media_house.modules.music_and_sound_design.domain.spans import Span, merge, overlap
from media_house.modules.music_and_sound_design.domain.timeline import (
    TimelineClip,
    fraction,
    rational,
)
from media_house.modules.music_and_sound_design.domain.values import (
    ClipAudio,
    LicenceState,
    MusicMode,
    QcCategory,
    Severity,
)

#: Broadcast guidance: music under dialogue at least about 10-12 LU below it stays intelligible.
_MASKING_LU = 12.0
#: A music pause shorter than this sounds like a dropout rather than a decision.
_SHORT_PAUSE = Fraction(3, 2)
_TOLERANCE = Fraction(1, 1000)


def _issue(
    code: str,
    severity: Severity,
    message: str,
    *,
    item: str | None = None,
    span: Span | None = None,
    evidence: str = "",
    category: QcCategory = QcCategory.TECHNICAL,
) -> QcIssue:
    return QcIssue(
        code=code,
        severity=severity,
        category=category,
        message=message,
        item_id=item,
        timeline_start=None if span is None else rational(max(Fraction(0), span[0])),
        timeline_end=None if span is None else rational(max(Fraction(0), span[1])),
        evidence=evidence,
    )


def quality_checks(
    *,
    music: tuple[MusicCue, ...],
    sfx: tuple[SfxEvent, ...],
    speech: list[Span],
    clips: tuple[TimelineClip, ...],
    profile: SoundProfile,
    index: SoundIndex,
    timeline_end: Fraction,
    frame_rate_assumed: bool,
    missing: tuple[MissingAsset, ...],
    attributions: tuple[Credit, ...],
) -> tuple[QcIssue, ...]:
    out: list[QcIssue] = []
    mix = profile.mix
    playing = [c for c in music if c.segments]
    music_spans = merge(
        (fraction(s.timeline_start), fraction(s.timeline_end)) for c in playing for s in c.segments
    )
    if playing and speech and mix.bed_below_speech_lu < _MASKING_LU:
        out.append(
            _issue(
                "speech_masking_risk",
                Severity.WARNING,
                f"the music bed sits only {mix.bed_below_speech_lu:g} LU below speech",
                evidence=f"guidance: at least {_MASKING_LU:g} LU",
                category=QcCategory.CREATIVE,
            )
        )
    for event in sfx:
        span = (fraction(event.timeline_start), fraction(event.timeline_end))
        if any(overlap(span, s) for s in speech) and "soft" not in " ".join(event.conflicts):
            out.append(
                _issue(
                    "sfx_over_speech",
                    Severity.WARNING,
                    f"{event.purpose} plays under speech",
                    item=event.item_id,
                    span=span,
                )
            )
    for left, right in itertools.pairwise(music_spans):
        if right[0] - left[1] < _SHORT_PAUSE:
            out.append(
                _issue(
                    "music_dropout",
                    Severity.WARNING,
                    f"music pauses for only {float(right[0] - left[1]):.2f} s",
                    span=(left[1], right[0]),
                    category=QcCategory.CREATIVE,
                )
            )
    for cue in playing:
        for conflict in cue.conflicts:
            if "covers only" in conflict:
                out.append(_issue("music_too_short", Severity.WARNING, conflict, item=cue.cue_id))
        sound = index.get(cue.asset.asset_id) if cue.asset else None
        peak = (
            sound.music.measurement.loudness.true_peak_dbtp
            if sound is not None and sound.music is not None
            else None
        )
        gain = cue.segments[0].gain_db
        if peak is not None and peak + gain > mix.headroom_dbfs:
            out.append(
                _issue(
                    "clipping_risk",
                    Severity.WARNING,
                    f"music predicted at {peak + gain:+.1f} dBTP before ducking",
                    item=cue.cue_id,
                    evidence=f"true peak {peak:+.1f} dBTP, gain {gain:+.1f} dB",
                )
            )
    ordered = sorted(sfx, key=lambda e: (fraction(e.timeline_start), e.item_id))
    for first, second in itertools.pairwise(ordered):
        a, b = first, second
        if fraction(b.timeline_start) < fraction(a.timeline_end):
            out.append(
                _issue(
                    "sfx_overlap",
                    Severity.INFO,
                    f"{b.purpose} starts under the tail of {a.purpose}",
                    item=b.item_id,
                    span=(fraction(b.timeline_start), fraction(a.timeline_end)),
                )
            )
    for event in sfx:
        for conflict in event.conflicts:
            if "clipping" in conflict:
                out.append(_issue("clipping_risk", Severity.WARNING, conflict, item=event.item_id))
    for asset_id, kind in [(c.asset.asset_id, "music") for c in playing if c.asset] + [
        (e.asset.asset_id, "sfx") for e in sfx
    ]:
        sound = index.get(asset_id)
        if sound is not None and sound.licence.state is not LicenceState.CLEARED:
            out.append(
                _issue(
                    "licence_unconfirmed",
                    Severity.WARNING,
                    f"{kind} '{sound.title}' is {sound.licence.state.value}: the owner must"
                    " confirm before publishing",
                    item=asset_id,
                    evidence=sound.licence.note or sound.licence.name,
                )
            )
    out += _dead_air(music_spans, speech, clips, timeline_end, profile)
    for need in missing:
        out.append(
            _issue(
                "missing_asset",
                Severity.INFO,
                f"{need.priority.value}: {need.required_asset}",
                item=need.recommendation_id,
                category=QcCategory.CREATIVE,
            )
        )
    if frame_rate_assumed:
        out.append(
            _issue(
                "frame_rate_assumed",
                Severity.INFO,
                "the frame rate is assumed: seconds are authoritative, frames are approximate",
            )
        )
    attributed = {c.asset_id for c in attributions}
    for cue in playing:
        if cue.asset and cue.asset.attribution_required and cue.asset.asset_id not in attributed:
            out.append(
                _issue(
                    "credit_missing",
                    Severity.CRITICAL,
                    f"'{cue.asset.title}' needs a credit but none is planned",
                    item=cue.cue_id,
                )
            )
    return tuple(out)


def _dead_air(
    music: list[Span],
    speech: list[Span],
    clips: tuple[TimelineClip, ...],
    end: Fraction,
    profile: SoundProfile,
) -> list[QcIssue]:
    sounding = merge(
        [
            *music,
            *speech,
            *(
                (fraction(c.timeline_start), fraction(c.timeline_end))
                for c in clips
                if c.audio is not ClipAudio.MUTED
            ),
        ]
    )
    limit = Fraction(round(profile.silence.max_dead_air_seconds * 1000), 1000)
    cursor = Fraction(0)
    out = []
    for start, stop in [*sounding, (end, end)]:
        if start - cursor > limit:
            out.append(
                _issue(
                    "dead_air",
                    Severity.WARNING,
                    f"{float(start - cursor):.1f} s without speech, music or clip sound: room"
                    " tone belongs to audio_improvement",
                    span=(cursor, start),
                )
            )
        cursor = max(cursor, stop)
    return out


def validate_plan(plan: SoundDesignPlan) -> list[QcIssue]:
    """Structural consistency; CRITICAL = the plan is wrong and must never be stored."""
    out = [c for c in plan.checks if c.severity is Severity.CRITICAL]
    end = fraction(plan.timeline_duration)
    for cue in plan.music:
        if cue.mode is MusicMode.MUSIC_ENABLED and (cue.asset is None or not cue.segments):
            out.append(
                _issue(
                    "cue_without_audio",
                    Severity.CRITICAL,
                    "enabled cue plays nothing",
                    item=cue.cue_id,
                )
            )
        for seg in cue.segments:
            src = (fraction(seg.source_start), fraction(seg.source_end))
            tl = (fraction(seg.timeline_start), fraction(seg.timeline_end))
            if src[1] <= src[0] or tl[1] <= tl[0] or abs((src[1] - src[0]) - (tl[1] - tl[0])) > 0:
                out.append(
                    _issue(
                        "segment_times",
                        Severity.CRITICAL,
                        "segment source and timeline lengths differ",
                        item=seg.segment_id,
                    )
                )
            if tl[1] > end + _TOLERANCE:
                out.append(
                    _issue(
                        "segment_past_end",
                        Severity.CRITICAL,
                        "segment runs past the edit",
                        item=seg.segment_id,
                    )
                )
    for event in plan.sfx:
        if fraction(event.timeline_end) <= fraction(event.timeline_start):
            out.append(
                _issue("sfx_times", Severity.CRITICAL, "effect has no length", item=event.item_id)
            )
    ids = [c.cue_id for c in plan.music] + [e.item_id for e in plan.sfx]
    if len(ids) != len(set(ids)):
        out.append(_issue("duplicate_ids", Severity.CRITICAL, "two plan items share an id"))
    return out
