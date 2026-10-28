"""Cut points: where a sentence may start and stop without clipping a word.

A boundary is chosen inside a SAFE WINDOW: after the previous word has finished, before the next
one begins, with a guard on both sides that depends on how well the words were aligned. Inside
the window the preferred place is a little room around the speech (``lead_in`` / ``tail_out``), a
shot boundary when one falls inside the window (a picture cut and a sound cut that coincide hide
each other), and a frame boundary of the recording when its constant frame rate is known.

``CutPoint.confidence`` is evidence that the boundary is SAFE: alignment quality, whether the gap
is confirmed silence, how much room there is. It is not frame accuracy; ``precision`` says where
the time comes from. Where only a sentence time exists, the confidence is low and says so.
"""

from fractions import Fraction
from typing import TYPE_CHECKING

from media_house.modules.editing_intelligence.domain.facts import (
    SourceFacts,
    VideoFacts,
    WordFact,
)
from media_house.modules.editing_intelligence.domain.model import CutAlternative, CutPoint
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.speech import AudioIndex, Extent
from media_house.modules.editing_intelligence.domain.timing import (
    exact,
    snap_into,
    to_rational,
)
from media_house.modules.editing_intelligence.domain.values import Precision
from media_house.shared.errors import InvariantViolation

if TYPE_CHECKING:
    from media_house.core.domain import Rational

_FALLBACK_CONFIDENCE = {
    Precision.SENTENCE_SPAN: 0.35,
    Precision.ESTIMATED: 0.2,
    Precision.SEGMENT_ALIGNED: 0.35,
    Precision.WORD_ALIGNED: 0.35,
    Precision.FRAME_EXACT: 0.35,
}
_BREATH = frozenset({"breath"})
_ZERO = Fraction(0)
_SHOT_SEARCH = Fraction(1, 2)
#: A cut stays within this distance of the speech it belongs to, however long the silence is.
_REACH = Fraction(1)


class CutContext:
    """What a boundary decision may consult for one recording."""

    def __init__(
        self,
        source: SourceFacts,
        index: AudioIndex | None,
        video: VideoFacts | None,
        profile: EditingProfile,
    ) -> None:
        self.source = source
        self.index = index
        self.video = video
        self.profile = profile
        self.rate: Rational | None = source.constant_frame_rate
        self.duration = exact(source.duration) if source.duration is not None else None

    @property
    def audio(self) -> AudioIndex:
        """The audio index; only called where words exist (callers check ``index``)."""
        if self.index is None:
            raise InvariantViolation("This boundary needs audio analysis")
        return self.index

    def shot_starts(self, low: Fraction, high: Fraction) -> list[Fraction]:
        if self.video is None:
            return []
        return [
            exact(s.start)
            for s in self.video.shots
            if s.index > 0 and low <= exact(s.start) <= high
        ]


def cut_in(ctx: CutContext, extent: Extent) -> CutPoint:
    """The start of a run beginning with this sentence."""
    if not extent.words or ctx.index is None:
        return _fallback(extent.start, extent.precision, "start")
    first = extent.words[0]
    previous_end = exact(ctx.audio.previous_end(first.start))
    gap = max(_ZERO, extent.start - previous_end)
    guard = _guard(ctx)
    low = max(previous_end + min(Fraction(3, 100), gap / 4), extent.start - _REACH)
    high = extent.start - min(guard, gap / 2)
    wanted = _clamp(extent.start - exact(ctx.profile.lead_in), low, high)
    breath = ctx.audio.events_between(float(low) - 0.3, float(extent.start), _BREATH)
    kept_breath = ""
    if breath:
        wanted = _clamp(exact(breath[-1].start) - Fraction(1, 50), low, high)
        kept_breath = "; keeps the breath before the sentence"
    return _place(
        ctx,
        extent.precision,
        "start",
        low,
        high,
        wanted,
        previous_end,
        extent.start,
        first,
        kept_breath,
    )


def cut_out(ctx: CutContext, extent: Extent, hold: float = 0.0) -> CutPoint:
    """The end of a run finishing with this sentence. ``hold`` asks for extra tail (a payoff)."""
    if not extent.words or ctx.index is None:
        return _fallback(extent.end, extent.precision, "end")
    last = extent.words[-1]
    next_start = exact(ctx.audio.next_start(last.end))
    gap = max(_ZERO, next_start - extent.end)
    guard = _guard(ctx)
    low = extent.end + min(guard, gap / 2)
    high = min(next_start - min(Fraction(3, 100), gap / 4), extent.end + _REACH)
    wanted = _clamp(extent.end + exact(ctx.profile.tail_out + hold), low, high)
    return _place(ctx, extent.precision, "end", low, high, wanted, extent.end, next_start, last, "")


def cut_pair(
    ctx: CutContext,
    left: WordFact,
    right: WordFact,
    keep: float,
    precision: Precision,
) -> tuple[CutPoint, CutPoint]:
    """Shorten the silence between two words to about ``keep`` seconds: where the left side stops
    and where the right side resumes. Each end has its own safe window and confidence."""
    left_end, right_start = exact(left.end), exact(right.start)
    gap = max(_ZERO, right_start - left_end)
    guard = _guard(ctx)
    half = exact(keep) / 2
    middle = left_end + gap / 2
    out_low, out_high = left_end + min(guard, gap / 2), min(middle, left_end + _REACH)
    in_low, in_high = max(middle, right_start - _REACH), right_start - min(guard, gap / 2)
    out = _place(
        ctx,
        precision,
        "end",
        out_low,
        out_high,
        _clamp(left_end + half, out_low, out_high),
        left_end,
        right_start,
        left,
        "",
    )
    resume = _place(
        ctx,
        precision,
        "start",
        in_low,
        in_high,
        _clamp(right_start - half, in_low, in_high),
        left_end,
        right_start,
        right,
        "",
    )
    return out, resume


def _guard(ctx: CutContext) -> Fraction:
    """How close to a word a cut may come: tighter when the words were measured, not estimated."""
    return Fraction(4, 100) if ctx.audio.audio.word_level else Fraction(10, 100)


def _clamp(value: Fraction, low: Fraction, high: Fraction) -> Fraction:
    return max(low, min(high, value))


def _fallback(moment: Fraction, precision: Precision, side: str) -> CutPoint:
    return CutPoint(
        time=to_rational(moment),
        precision=precision,
        confidence=_FALLBACK_CONFIDENCE[precision],
        reason=f"no word timing for this sentence: its {side} is the sentence time "
        f"({precision.value})",
    )


def _place(
    ctx: CutContext,
    precision: Precision,
    side: str,
    low: Fraction,
    high: Fraction,
    wanted: Fraction,
    gap_start: Fraction,
    gap_end: Fraction,
    edge_word: WordFact,
    extra: str,
) -> CutPoint:
    time = wanted
    reason = f"inside the gap next to the sentence {side}"
    shots = ctx.shot_starts(low, high)
    alternatives: list[CutAlternative] = []
    if shots:
        time = min(shots, key=lambda s: abs(s - wanted))
        precision = Precision.FRAME_EXACT
        reason = "on a shot boundary inside the gap (picture and sound change together)"
        alternatives.append(
            CutAlternative(
                time=to_rational(wanted),
                kind="speech_room",
                reason="room around the speech, without a picture cut",
            )
        )
    elif ctx.rate is not None:
        snapped = snap_into(low, high, wanted, ctx.rate)
        if snapped is not None:
            time = snapped[0]
    if gap_end > gap_start:
        middle = gap_start + (gap_end - gap_start) / 2
        if middle != time:
            alternatives.append(
                CutAlternative(
                    time=to_rational(middle),
                    kind="pause_midpoint",
                    reason="middle of the silence between the words",
                )
            )
    near = [
        s
        for s in ctx.shot_starts(low - _SHOT_SEARCH, high + _SHOT_SEARCH)
        if not low <= s <= high and s != time
    ]
    if near:
        nearest = min(near, key=lambda s: abs(s - wanted))
        alternatives.append(
            CutAlternative(
                time=to_rational(nearest),
                kind="shot_boundary",
                reason="nearest picture cut, outside the safe window (may clip speech)",
            )
        )
    confidence, why = _confidence(ctx, low, high, gap_start, gap_end, edge_word)
    return CutPoint(
        time=to_rational(time),
        precision=precision,
        confidence=confidence,
        reason=f"{reason}; {why}{extra}",
        alternatives=tuple(alternatives[:3]),
    )


def _confidence(
    ctx: CutContext,
    low: Fraction,
    high: Fraction,
    gap_start: Fraction,
    gap_end: Fraction,
    edge_word: WordFact,
) -> tuple[float, str]:
    word_level = ctx.audio.audio.word_level
    base = 0.9 if word_level else 0.5
    parts = ["words forced-aligned" if word_level else "word times estimated within segments"]
    pause = ctx.audio.pause_between(float(gap_start), float(gap_end))
    confirm = ctx.profile.pause_confirm
    ends = exact(ctx.audio.audio.duration)
    if gap_end - gap_start <= Fraction(1, 10) and (gap_start == 0 or gap_end >= ends):
        # Speech touches the very start or end of the recording: there is nothing beyond to clip.
        value = base * (
            0.7 + 0.3 * (edge_word.confidence if edge_word.confidence is not None else 1.0)
        )
        return round(value, 3), "speech at the edge of the recording; nothing beyond it to clip"
    if pause is None or pause.silence_ratio is None:
        silence = 0.8
        parts.append("silence in the gap not measured")
    else:
        silence = 0.6 + 0.4 * min(1.0, pause.silence_ratio / confirm)
        parts.append(f"gap silent for {pause.silence_ratio:.0%}")
    width = float(min(Fraction(1), (high - low) / Fraction(12, 100)))
    parts.append(f"{float(high - low) * 1000:.0f} ms of room")
    value = base * silence * (0.5 + 0.5 * width)
    if edge_word.confidence is not None:
        value *= 0.7 + 0.3 * edge_word.confidence
        parts.append(f"edge word confidence {edge_word.confidence:.2f}")
    return round(value, 3), ", ".join(parts)
