"""Facts for the quality checks: the real built timeline of the builder's fixtures (a talking head
of two 4 s shots at 25 fps with cover footage, a lower third, captions, a dissolve, music and a
sound effect), read the way the application reads it, and small ways to break it."""

from dataclasses import replace
from fractions import Fraction
from typing import Any

from media_house.modules.quality_control.application.views import timeline_facts
from media_house.modules.quality_control.domain import facts as f
from media_house.modules.quality_control.domain.measurements import (
    ClipLoudness,
    FrameStats,
    LoudnessFacts,
    RenderMeasurements,
    Span,
    StreamFacts,
)
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.profile import PROFILES, QualityProfile
from tests.unit.timeline_builder import fixtures as bf

GENERIC: QualityProfile = PROFILES["generic"]
TALKING: QualityProfile = PROFILES["talking_head"]
PA, PB = "v:pA", "v:pB"


def clean() -> f.TimelineFacts:
    return timeline_facts(bf.built())


def with_clip(facts: f.TimelineFacts, clip_id: str, **changes: Any) -> f.TimelineFacts:
    return replace(
        facts,
        clips=tuple(replace(c, **changes) if c.clip_id == clip_id else c for c in facts.clips),
    )


def with_sound(facts: f.TimelineFacts, clip_id: str, **changes: Any) -> f.TimelineFacts:
    return replace(
        facts,
        sounds=tuple(replace(s, **changes) if s.clip_id == clip_id else s for s in facts.sounds),
    )


def codes(issues: list[QualityIssue]) -> list[str]:
    return [i.code for i in issues]


def one(issues: list[QualityIssue], code: str) -> QualityIssue:
    found = [i for i in issues if i.code == code]
    assert len(found) == 1, f"expected one {code}, found {codes(issues)}"
    return found[0]


def streams(**changes: Any) -> StreamFacts:
    base = StreamFacts(
        has_video=True,
        has_audio=True,
        width=1920,
        height=1080,
        fps=Fraction(25),
        duration=Fraction(8),
        audio_duration=Fraction(8),
        video_codec="h264",
        audio_sample_rate=48000,
        av_offset=0.0,
    )
    return replace(base, **changes)


def good_loudness() -> LoudnessFacts:
    return LoudnessFacts(-14.0, 6.0, -2.0, -3.0)


def measurements(**changes: Any) -> RenderMeasurements:
    """A preview that matches the timeline and has nothing wrong with it."""
    base = RenderMeasurements(
        asset_id="preview1",
        analyzer="ffmpeg test",
        streams=streams(),
        decode=None,
        black=(),
        frozen=(),
        silent=(),
        loudness=good_loudness(),
        end_peak_dbfs=-60.0,
        dialogue_levels=(ClipLoudness("a:aV", -16.0), ClipLoudness("a:aW", -16.5)),
        frames=(),
    )
    return replace(base, **changes)


def frame(
    seconds: float, tag: str = "clip:v:pA", clip: str | None = PA, **changes: Any
) -> FrameStats:
    base = FrameStats(
        at=Fraction(seconds).limit_denominator(1000),
        tag=tag,
        clip_id=clip,
        luma_mean=110.0,
        crushed_fraction=0.02,
        clipped_fraction=0.01,
        mean_rgb=(120.0, 110.0, 100.0),
        sharpness=400.0,
        border_black=(0.0, 0.0, 0.0, 0.0),
        black_fraction=0.01,
    )
    return replace(base, **changes)


def span(start: float, end: float | None) -> Span:
    return Span(
        Fraction(start).limit_denominator(1000),
        None if end is None else Fraction(end).limit_denominator(1000),
    )
