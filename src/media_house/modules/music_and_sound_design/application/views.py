"""Anti-corruption: upstream results (audio timeline, story plan, pacing) -> this module's DTO.

Upstream times are on a recording's own clock; they reach the timeline only through a clip of
that recording (``TimelineClip.to_timeline``). A word or event no clip shows is not on the
timeline and is dropped. Speech words are joined into spans (gaps under ``_WORD_GAP``) so the
DTO stays small.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from fractions import Fraction

from media_house.modules.audio_intelligence.application.contracts import (
    ENERGY_SPIKE,
    AudioIntelligenceTimeline,
)
from media_house.modules.music_and_sound_design.domain.spans import merge
from media_house.modules.music_and_sound_design.domain.timeline import (
    OriginalSoundEvent,
    PacingCue,
    SoundtrackTimeline,
    SpeechSpan,
    TimelineClip,
    TimelineSection,
    rational,
)
from media_house.modules.music_and_sound_design.domain.values import ClipAudio
from media_house.modules.pacing_intelligence.application.contracts import (
    PacingAnalysis,
    RecommendationType,
)
from media_house.modules.story_architecture.application.contracts import StoryPlan

_WORD_GAP = Fraction(3, 10)
#: Loud original moments: level spikes and, where audio_intelligence detects them, impulses
#: (bangs). Named by value so an older audio timeline without impulses still reads.
_LOUD_KINDS = frozenset({ENERGY_SPIKE, "impulse"})
_PACING_KINDS = {
    RecommendationType.HOLD_REVEAL: "hold_reveal",
    RecommendationType.ADD_SECTION_BREATH: "add_section_breath",
}


def _exact(seconds: float) -> Fraction:
    return Fraction(seconds).limit_denominator(1_000_000)


def _on_timeline(
    clips: Iterable[TimelineClip], start: Fraction, end: Fraction
) -> list[tuple[TimelineClip, Fraction, Fraction]]:
    """Every timeline span showing ``[start, end)`` of the clips' recording (clipped to them)."""
    out = []
    for clip in clips:
        lo = max(start, Fraction(clip.source_start.numerator, clip.source_start.denominator))
        hi = min(end, Fraction(clip.source_end.numerator, clip.source_end.denominator))
        if hi <= lo:
            continue
        a, b = clip.to_timeline(lo), clip.to_timeline(hi)
        if a is not None and b is not None:
            out.append((clip, a, b))
    return out


def _audible(timeline: SoundtrackTimeline, asset_id: str) -> list[TimelineClip]:
    return [c for c in timeline.clips if c.asset_id == asset_id and c.audio is not ClipAudio.MUTED]


def speech_spans(
    timeline: SoundtrackTimeline, audio: Mapping[str, AudioIntelligenceTimeline]
) -> tuple[SpeechSpan, ...]:
    spans: list[tuple[Fraction, Fraction]] = []
    counts: list[tuple[Fraction, Fraction]] = []
    for asset_id, result in audio.items():
        clips = _audible(timeline, asset_id)
        for word in result.transcript.words:
            for _, a, b in _on_timeline(clips, _exact(word.start), _exact(word.end)):
                spans.append((a, b))
                counts.append((a, b))
    merged = merge(spans, _WORD_GAP)
    return tuple(
        SpeechSpan(
            timeline_start=rational(a),
            timeline_end=rational(b),
            words=sum(1 for c in counts if a <= c[0] < b),
        )
        for a, b in merged
    )


def original_events(
    timeline: SoundtrackTimeline, audio: Mapping[str, AudioIntelligenceTimeline]
) -> tuple[OriginalSoundEvent, ...]:
    out = []
    for asset_id, result in audio.items():
        clips = [c for c in _audible(timeline, asset_id) if c.audio is ClipAudio.SOURCE_SYNC]
        for event in result.events:
            if event.kind not in _LOUD_KINDS:
                continue
            for clip, a, b in _on_timeline(clips, _exact(event.start), _exact(event.end)):
                out.append(
                    OriginalSoundEvent(
                        clip_id=clip.clip_id,
                        kind=event.kind,
                        timeline_start=rational(a),
                        timeline_end=rational(b),
                        strength=float(event.strength),
                    )
                )
    return tuple(
        sorted(
            out,
            key=lambda e: (
                Fraction(e.timeline_start.numerator, e.timeline_start.denominator),
                e.clip_id,
            ),
        )
    )


def with_story(timeline: SoundtrackTimeline, plan: StoryPlan) -> SoundtrackTimeline:
    """The story format, and beat functions for sections the pipeline gave without them."""
    functions: dict[str, list[str]] = {}
    for beat in plan.beats:
        functions.setdefault(beat.section_id, []).append(beat.function.value)
    sections: list[TimelineSection] = [
        s if s.beat_functions or s.section_id not in functions
        else replace(s, beat_functions=tuple(functions[s.section_id]))
        for s in timeline.sections
    ]  # fmt: skip
    return replace(
        timeline,
        story_format=timeline.story_format or plan.target_format,
        sections=tuple(sections),
    )


def pacing_cues(timeline: SoundtrackTimeline, pacing: PacingAnalysis) -> tuple[PacingCue, ...]:
    out = []
    for rec in pacing.recommendations:
        kind = _PACING_KINDS.get(rec.type)
        if kind is None:
            continue
        target = rec.target
        clips = [c for c in timeline.clips if c.asset_id == target.asset_id]
        start = Fraction(target.start.numerator, target.start.denominator)
        end = Fraction(target.end.numerator, target.end.denominator)
        for _, a, b in _on_timeline(clips, start, max(end, start)) or (
            [] if end > start else _point(clips, start)
        ):
            out.append(
                PacingCue(
                    cue_id=rec.recommendation_id,
                    kind=kind,
                    timeline_start=rational(a),
                    timeline_end=rational(b),
                )
            )
            break
    return tuple(out)


def _point(
    clips: list[TimelineClip], at: Fraction
) -> list[tuple[TimelineClip, Fraction, Fraction]]:
    for clip in clips:
        mapped = clip.to_timeline(at)
        if mapped is not None:
            return [(clip, mapped, mapped)]
    return []
