"""Frame-exact timing: frames at the edge, handle math, fitting, and speech-safe audio fades.

Frames: ``frame = round(t * fps)`` on exact values (half to even); a duration in frames is a
difference of frames. A transition of ``D`` frames on a cut at frame ``c``:

* centred: ``before = D // 2`` frames before the cut, ``after = D - before`` after it (an odd
  ``D`` puts the extra frame after the cut);
* starting at the cut: ``before = 0, after = D``; ending at it: ``before = D, after = 0``.

An OVERLAP transition shows both pictures: the outgoing recording keeps playing ``after``
frames past its out point (handle ``required_out = after``) and the incoming one starts
``before`` frames before its in point (``required_in = before``). Available handles are whole
frames of source material: ``floor((source_end_of_recording - source_end) * fps)`` and
``floor((source_start - start_of_recording) * fps)``, minus a safety frame for VFR sources.
"""

import math
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.transition_engine.domain.facts import SourceFacts, WordSpan
from media_house.modules.transition_engine.domain.values import Alignment


def frame_of(t: Fraction, fps: Fraction) -> int:
    return round(t * fps)


def time_of(frame: int, fps: Fraction) -> Fraction:
    return Fraction(frame) / fps


def split(frames: int, alignment: Alignment) -> tuple[int, int]:
    """``(before, after)`` frames of a transition of ``frames`` on the cut."""
    if alignment is Alignment.START:
        return 0, frames
    if alignment is Alignment.END:
        return frames, 0
    before = frames // 2
    return before, frames - before


def available_after(
    source: SourceFacts | None, source_end: Fraction, fps: Fraction, vfr_safety: int
) -> int | None:
    """Whole frames of the recording after ``source_end`` (``None`` = recording length unknown).
    A VFR recording loses ``vfr_safety`` frames: its frame times are not on the grid."""
    if source is None or source.end is None:
        return None
    frames = math.floor((source.end - source_end) * fps)
    return max(0, frames - (vfr_safety if source.variable_frame_rate else 0))


def available_before(
    source: SourceFacts | None, source_start: Fraction, fps: Fraction, vfr_safety: int
) -> int | None:
    if source is None or source.duration is None:
        return None
    frames = math.floor((source_start - source.start) * fps)
    return max(0, frames - (vfr_safety if source.variable_frame_rate else 0))


@dataclass(frozen=True, slots=True, kw_only=True)
class Fit:
    frames: int
    alignment: Alignment
    before: int
    after: int
    shortened: bool
    realigned: bool


def fit(
    *,
    wanted: int,
    minimum: int,
    alignment: Alignment,
    overlap: bool,
    handle_out: int | None,
    handle_in: int | None,
    room_out: int,
    room_in: int,
) -> Fit | None:
    """The longest duration (at most ``wanted``, at least ``minimum`` frames) that fits.

    ``room_out``/``room_in``: frames of the outgoing/incoming CLIP the transition may cover
    (a clip is never covered by two transitions at once). An overlap also needs the handles.
    The preferred alignment is tried first, then the others; ``None`` when nothing fits."""
    order = [
        alignment,
        *(a for a in (Alignment.CENTER, Alignment.START, Alignment.END) if a is not alignment),
    ]
    for frames in range(wanted, minimum - 1, -1):
        for option in order:
            before, after = split(frames, option)
            if before > room_out or after > room_in:
                continue
            if overlap and (
                (after and (handle_out is None or handle_out < after))
                or (before and (handle_in is None or handle_in < before))
            ):
                continue
            return Fit(
                frames=frames,
                alignment=option,
                before=before,
                after=after,
                shortened=frames < wanted,
                realigned=option is not alignment,
            )
    return None


# --- audio ------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, kw_only=True)
class Fade:
    before: Fraction
    after: Fraction
    protected: tuple[str, ...]


def speech_safe_fade(
    *,
    before: Fraction,
    after: Fraction,
    out_point: Fraction | None,
    out_words: tuple[WordSpan, ...],
    in_point: Fraction | None,
    in_words: tuple[WordSpan, ...],
    guard: Fraction,
    minimum: Fraction,
) -> Fade:
    """Shorten a crossfade around an audio edit so no word is faded or leaked.

    ``out_point``: the outgoing recording's SOURCE time at the audio edit; the fade-out runs
    over ``[out_point - before, out_point + after]`` (the part after the point is handle audio
    that would leak). ``in_point``: the incoming recording's source time; the fade-in runs over
    ``[in_point - before, in_point + after]`` (the part before it would leak). A word keeps
    ``guard`` seconds of distance; a fade never gets shorter than ``minimum`` on a side (a click
    guard of a few ms cannot hurt a word). ``None`` points (no recording on that side) are
    unconstrained."""
    protected: list[str] = []

    def limit(current: Fraction, room: Fraction, label: str) -> Fraction:
        if room < current:
            protected.append(label)
            return min(current, max(minimum, room))
        return current

    if out_point is not None:
        for w in out_words:
            if w.end <= out_point and w.end > out_point - before - guard:
                before = limit(before, out_point - w.end - guard, f"out:{float(w.end):.3f}")
            elif w.start >= out_point and w.start < out_point + after + guard:
                after = limit(after, w.start - out_point - guard, f"out:{float(w.start):.3f}")
            elif w.start < out_point < w.end:
                before = limit(before, Fraction(0), f"out:{float(w.start):.3f}")
                after = limit(after, Fraction(0), f"out:{float(w.end):.3f}")
    if in_point is not None:
        for w in in_words:
            if w.start >= in_point and w.start < in_point + after + guard:
                after = limit(after, w.start - in_point - guard, f"in:{float(w.start):.3f}")
            elif w.end <= in_point and w.end > in_point - before - guard:
                before = limit(before, in_point - w.end - guard, f"in:{float(w.end):.3f}")
            elif w.start < in_point < w.end:
                before = limit(before, Fraction(0), f"in:{float(w.start):.3f}")
                after = limit(after, Fraction(0), f"in:{float(w.end):.3f}")
    return Fade(before=before, after=after, protected=tuple(protected))


def words_near(words: tuple[WordSpan, ...], at: Fraction, reach: Fraction) -> bool:
    """Is a word within ``reach`` seconds of ``at`` (speech running across an edit point)?"""
    return any(w.start - reach <= at <= w.end + reach for w in words)


def samples(seconds: Fraction, rate: int) -> int:
    return round(seconds * rate)
