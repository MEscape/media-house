import math
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest

from media_house.core.application.ports import (
    FrameComparison,
    FrameSample,
    LoudnessReading,
    MediaAnalyzer,
    TimeSpan,
)
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.short_iterate import next_attempt
from media_house.modules.media_pipeline.application.short_review import (
    PlanCounts,
    ShortSources,
    measure_short,
)
from media_house.modules.media_pipeline.domain.short_gate import (
    Remedy,
    ShortFacts,
    judge_short,
    longest_still,
)
from media_house.shared.concurrency import CancellationToken


def _facts(**kw: object) -> ShortFacts:
    base: dict[str, object] = {
        "duration_s": 45.0,
        "changes": tuple((t * 0.5, 0.5 if t % 4 == 0 else 0.99) for t in range(1, 90)),
        "letterbox": (0.0,) * 10,
        "integrated_lufs": -14.0,
        "true_peak_dbtp": -1.5,
        "dialogue_lufs": -24.0,
        "music_under_speech_lufs": -40.0,
        "sfx_lufs": (-30.0,) * 10,
        "speech_s": 30.0,
        "captioned_s": 28.5,
        "emoji": 8,
        "required_parts": ("hook", "item 1", "item 2", "payoff"),
        "present_parts": ("hook", "item 1", "item 2", "payoff"),
    }
    base.update(kw)
    return ShortFacts(**base)  # type: ignore[arg-type]


def test_a_complete_lively_short_passes() -> None:
    verdict = judge_short(_facts())

    assert verdict.passed, verdict.findings


def test_the_measured_short_fails_where_the_owner_saw_it_fail() -> None:
    verdict = judge_short(
        _facts(
            changes=((5.0, 0.5), (16.0, 0.5)),
            music_under_speech_lufs=-51.0,
            sfx_lufs=(-30.0, -40.0),
            present_parts=("item 2", "payoff"),
            emoji=3,
        )
    )

    assert set(verdict.remedies) == {
        Remedy.CONDENSE,
        Remedy.MORE_MOTION,
        Remedy.MUSIC_UP,
        Remedy.MORE_SFX,
        Remedy.SFX_LEVEL,
        Remedy.MORE_EMOJI,
    }


def test_an_effect_far_under_the_voice_asks_for_louder_effects() -> None:
    verdict = judge_short(_facts(sfx_lufs=(-45.0,) * 10))

    assert verdict.remedies == (Remedy.SFX_UP,)


def test_music_over_the_voice_asks_for_less_music() -> None:
    verdict = judge_short(_facts(music_under_speech_lufs=-28.0))

    assert verdict.remedies == (Remedy.MUSIC_DOWN,)


def test_letterboxed_frames_ask_for_a_reframe() -> None:
    verdict = judge_short(_facts(letterbox=(0.0, 0.2, 0.2)))

    assert verdict.remedies == (Remedy.REFRAME,)


def test_the_longest_still_counts_the_start_and_the_end() -> None:
    assert longest_still(((2.0, 0.5), (3.0, 0.99)), 10.0, 0.93) == 8.0


class _Analyzer:
    """Pictures alternate between two looks every two samples; loudness by file and span."""

    def sample_frames(
        self, path: Path, times: Sequence[Fraction], **_: object
    ) -> tuple[FrameSample, ...]:
        return tuple(
            FrameSample(
                t,
                Path(f"{int(t * 2) // 2 % 2}.png"),
                108,
                192,
                0,
                0,
                0,
                0,
                0,
                (0, 0, 0),
                0,
                0,
                (0.1 if t < 2 else 0.0, 0, 0, 0),
                0,
            )
            for t in times
        )

    def compare_frames(self, a: Path, b: Path) -> FrameComparison:
        return FrameComparison(1.0 if a == b else 0.5, 1.0, 0.0)

    def loudness(self, path: Path, *, span: TimeSpan | None, **_: object) -> LoudnessReading:
        level = {"mix.mp4": -14.0, "d.wav": -24.0, "m.wav": -40.0, "s.wav": -30.0}[path.name]
        if path.name == "m.wav" and span is not None and span.start >= 5:
            level = -50.0
        return LoudnessReading(level, 3.0, -2.0, -3.0, None, None, None, "pcm")


def test_the_short_is_measured_from_the_file_and_the_stems() -> None:
    speech = (TimeSpan(Fraction(0), Fraction(5)), TimeSpan(Fraction(5), Fraction(10)))
    sources = ShortSources(
        Path("mix.mp4"),
        Path("d.wav"),
        Path("m.wav"),
        Path("s.wav"),
        speech,
        (TimeSpan(Fraction(3), Fraction(31, 10)),),
    )
    counts = PlanCounts(10, 9, 2, ("hook",), ("hook",))

    facts = measure_short(
        cast("MediaAnalyzer", _Analyzer()),
        sources,
        counts,
        duration=Fraction(10),
        evidence=Path(),
        cancellation=CancellationToken(),
    )

    assert facts.dialogue_lufs == pytest.approx(-24.0)
    assert facts.music_under_speech_lufs == pytest.approx(10 * math.log10((1e-4 + 1e-5) / 2))
    assert facts.sfx_lufs == (-30.0,)
    assert facts.letterbox[:3] == (0.1, 0.1, 0.1) and facts.letterbox[-1] == 0.0
    assert [t for t, s in facts.changes if s < 0.93] == [
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
        6.0,
        7.0,
        8.0,
        9.0,
    ]


def test_each_remedy_turns_its_knob_one_bounded_step() -> None:
    base = StageSettings(edit_excerpt=(35.0, 55.0))

    nxt = next_attempt(base, ("music_up", "more_motion", "more_sfx", "condense", "reframe"))

    assert nxt is not None
    assert nxt.mix_bus_gain_db == (("music", 3.0),)
    assert nxt.camera_beat == 1.8
    assert dict(nxt.sound_overrides)["sfx.min_spacing_seconds"] == 0.8
    assert nxt.edit_excerpt == (35.0, 60.0)
    assert next_attempt(base, ("shorten",)).edit_excerpt == (35.0, 50.0)  # type: ignore[union-attr]


def test_no_knob_left_means_no_next_attempt() -> None:
    worn = StageSettings(mix_bus_gain_db=(("music", 9.0),), camera_beat=1.4)

    assert next_attempt(worn, ("music_up", "more_motion", "reframe", "more_emoji")) is None
