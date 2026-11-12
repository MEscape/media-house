"""Measure a delivered Short for its quality gate (``domain.short_gate``).

The picture is sampled evenly (consecutive samples compared: a visible change or not; the black
border of each sample: letterboxing); the sound is read from the mix's own stems over the spans the
timeline says are speech and sound effects, so music under the voice and every effect's level are
measured, not guessed. Plan facts (words, emoji, story parts) come in from the caller.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

from media_house.core.application.ports import MediaAnalyzer, TimeSpan
from media_house.core.domain import Rational
from media_house.modules.media_pipeline.domain.short_gate import ShortFacts
from media_house.modules.motion_graphics_engine.application.contracts import GraphicsPlan
from media_house.shared.concurrency import CancellationToken

#: Samples per second of the picture (a change shorter than this can be missed: fine for a gate).
_RATE = 2
#: A loudness reading needs at least one 400 ms block.
_MIN_SPAN = Fraction(1, 2)


@dataclass(frozen=True, slots=True)
class ShortSources:
    video: Path
    dialogue: Path | None
    music: Path | None
    sfx: Path | None
    speech: tuple[TimeSpan, ...]
    effects: tuple[TimeSpan, ...]


@dataclass(frozen=True, slots=True)
class PlanCounts:
    speech_s: float
    captioned_s: float
    emoji: int
    required_parts: tuple[str, ...]
    present_parts: tuple[str, ...]


def measure_short(
    analyzer: MediaAnalyzer,
    sources: ShortSources,
    counts: PlanCounts,
    *,
    duration: Fraction,
    evidence: Path,
    cancellation: CancellationToken,
) -> ShortFacts:
    times = [Fraction(i, _RATE) for i in range(1, int(duration * _RATE))]
    samples = analyzer.sample_frames(sources.video, times, into=evidence, cancellation=cancellation)
    changes: list[tuple[float, float]] = []
    for before, after in pairwise(samples):
        if before.path is not None and after.path is not None:
            similarity = analyzer.compare_frames(before.path, after.path).similarity
            changes.append((float(after.requested), similarity))
    whole = analyzer.loudness(sources.video, span=None, cancellation=cancellation)
    return ShortFacts(
        duration_s=float(duration),
        changes=tuple(changes),
        letterbox=tuple(max(s.border_black[0], s.border_black[1]) for s in samples),
        integrated_lufs=whole.integrated_lufs,
        true_peak_dbtp=whole.true_peak_dbtp,
        dialogue_lufs=_over(analyzer, sources.dialogue, sources.speech, cancellation),
        music_under_speech_lufs=_over(analyzer, sources.music, sources.speech, cancellation),
        sfx_lufs=tuple(
            level
            for span in sources.effects
            if (level := _over(analyzer, sources.sfx, (span,), cancellation)) is not None
        ),
        speech_s=counts.speech_s,
        captioned_s=counts.captioned_s,
        emoji=counts.emoji,
        required_parts=counts.required_parts,
        present_parts=counts.present_parts,
    )


def _over(
    analyzer: MediaAnalyzer,
    path: Path | None,
    spans: Sequence[TimeSpan],
    cancellation: CancellationToken,
) -> float | None:
    """Loudness of ``path`` over ``spans`` (energy-weighted by length); ``None``: not measurable."""
    if path is None:
        return None
    energy, total = 0.0, Fraction(0)
    for span in spans:
        end = span.end if span.end is not None else span.start + _MIN_SPAN
        length = max(end - span.start, _MIN_SPAN)
        reading = analyzer.loudness(
            path, span=TimeSpan(span.start, span.start + length), cancellation=cancellation
        )
        if reading.integrated_lufs is None or math.isinf(reading.integrated_lufs):
            continue
        energy += float(length) * 10 ** (reading.integrated_lufs / 10)
        total += length
    return 10 * math.log10(energy / float(total)) if total and energy > 0 else None


def fraction(value: Rational) -> Fraction:
    return Fraction(value.numerator, value.denominator)


def full_screen(plan: GraphicsPlan) -> list[tuple[Fraction, Fraction]]:
    """When a planned full-screen card fills the picture (timeline seconds): no caption there."""
    out: list[tuple[Fraction, Fraction]] = []
    for g in plan.graphics:
        frames = g.frames
        if g.composition is None or not g.composition.full_frame or frames is None:
            continue
        if frames.timeline_start is None:
            continue
        start = fraction(frames.timeline_start)
        out.append((start, start + fraction(frames.duration)))
    return out


def seconds_outside(spans: Sequence[TimeSpan], cards: Sequence[tuple[Fraction, Fraction]]) -> float:
    """Total length of ``spans`` that no card covers."""
    total = Fraction(0)
    for span in spans:
        if span.end is None:
            continue
        total += span.end - span.start
        for a, b in cards:
            total -= max(Fraction(0), min(span.end, b) - max(span.start, a))
    return float(total)
