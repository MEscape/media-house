"""Upstream results reach the timeline only through clips of the same recording."""

from types import SimpleNamespace
from typing import Any, cast

from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.application.contracts import ENERGY_SPIKE
from media_house.modules.music_and_sound_design.application.views import (
    original_events,
    pacing_cues,
    speech_spans,
    with_story,
)
from media_house.modules.music_and_sound_design.domain.timeline import TimelineClip
from media_house.modules.music_and_sound_design.domain.values import ClipAudio
from media_house.modules.pacing_intelligence.application.contracts import RecommendationType
from tests.support.sound_fakes import r, section, timeline


def _clip(clip_id: str, src: tuple[float, float], at: float, **kw: Any) -> TimelineClip:
    return TimelineClip(
        clip_id=clip_id,
        asset_id="rec",
        source_start=r(src[0]),
        source_end=r(src[1]),
        timeline_start=r(at),
        **kw,
    )


def _seconds(value: Rational) -> float:
    return value.numerator / value.denominator


def _audio(words: list[tuple[float, float]], events: list[tuple[float, float]]) -> Any:
    return SimpleNamespace(
        transcript=SimpleNamespace(words=[SimpleNamespace(start=a, end=b) for a, b in words]),
        events=[
            SimpleNamespace(kind=kind, start=a, end=b, strength=0.9)
            for kind, (a, b) in zip((ENERGY_SPIKE, "impulse", "pitch_rise"), events, strict=False)
        ],
    )


def test_words_and_bangs_are_mapped_through_the_clips_that_show_them() -> None:
    soundtrack = timeline(
        duration=20.0,
        clips=(
            _clip("late", (30.0, 40.0), 0.0),  # recording 30-40 s plays first
            _clip("early", (0.0, 10.0), 10.0),
            _clip("quiet", (50.0, 60.0), 20.0, audio=ClipAudio.MUTED),
        ),
    )
    audio = _audio(
        words=[(31.0, 31.4), (31.5, 32.0), (5.0, 5.5), (45.0, 46.0), (55.0, 56.0)],
        events=[(35.0, 35.2), (5.0, 5.1), (36.0, 36.1)],
    )

    spans = speech_spans(soundtrack, {"rec": cast("Any", audio)})
    bangs = original_events(soundtrack, {"rec": cast("Any", audio)})

    assert [(_seconds(s.timeline_start), _seconds(s.timeline_end), s.words) for s in spans] == [
        (1.0, 2.0, 2),  # two words 0.1 s apart join into one span
        (15.0, 15.5, 1),
    ]  # 45 s is cut out of the edit, 55 s is in a muted clip
    assert [(b.clip_id, b.kind, _seconds(b.timeline_start)) for b in bangs] == [
        ("late", ENERGY_SPIKE, 5.0),
        ("early", "impulse", 15.0),
    ]  # a pitch rise is not a loud moment


def test_story_fills_missing_beat_functions_and_the_format() -> None:
    soundtrack = timeline(sections=(section("s1", 0, 10), section("s2", 10, 20, "payoff")))
    story = SimpleNamespace(
        target_format="top_n",
        beats=[
            SimpleNamespace(section_id="s1", function=SimpleNamespace(value="hook")),
            SimpleNamespace(section_id="s2", function=SimpleNamespace(value="recap")),
        ],
    )

    enriched = with_story(soundtrack, cast("Any", story))

    assert enriched.story_format == "top_n"
    assert [s.beat_functions for s in enriched.sections] == [("hook",), ("payoff",)]


def test_held_reveals_from_pacing_land_on_the_timeline() -> None:
    soundtrack = timeline(duration=20.0, clips=(_clip("c", (100.0, 120.0), 0.0),))
    target = SimpleNamespace(asset_id="rec", start=r(110), end=r(112))
    pacing = SimpleNamespace(
        recommendations=[
            SimpleNamespace(recommendation_id=rec_id, type=kind, target=target)
            for rec_id, kind in (
                ("p1", RecommendationType.HOLD_REVEAL),
                ("p2", RecommendationType.TIGHTEN_PAUSE),
            )
        ]
    )  # fmt: skip

    cues = pacing_cues(soundtrack, cast("Any", pacing))

    assert [(c.cue_id, c.kind, _seconds(c.timeline_start)) for c in cues] == [
        ("p1", "hold_reveal", 10.0)
    ]
