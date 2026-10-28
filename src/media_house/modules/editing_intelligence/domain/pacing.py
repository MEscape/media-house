"""Baseline pacing notes from what the edit contains. Not a pacing engine.

Pacing is judged at two levels, from evidence the edit already has: how much speech each section
carries per minute (a dense section is a candidate for a pause or a visual break; a very slow one
for tightening) and whether the clip lengths of the whole edit vary. There is no fixed maximum
shot length: rhythm is reported, never imposed. A specialised pacing analysis can replace or add
to these notes through ``Hooks.pacing``.
"""

import statistics
from collections.abc import Callable, Sequence

from media_house.modules.editing_intelligence.domain.assemble import Piece
from media_house.modules.editing_intelligence.domain.facts import StoryFacts
from media_house.modules.editing_intelligence.domain.hooks import Hooks, SectionPace
from media_house.modules.editing_intelligence.domain.model import PacingNote

_DENSE_WPM = 190.0
_SLOW_WPM = 90.0
_MIN_WORDS = 25
_UNIFORM_CV = 0.2
_MIN_CLIPS = 8


def plan_pacing(
    story: StoryFacts,
    pieces: Sequence[Piece],
    hooks: Hooks,
    note_id: Callable[[], str],
) -> list[PacingNote]:
    notes: list[PacingNote] = []
    paces: list[SectionPace] = []
    seconds_of: dict[str, float] = {}
    words_of: dict[str, int] = {}
    for piece in pieces:
        for unit in piece.units:
            share = float(piece.duration) / len(piece.units)
            seconds_of[unit.section_id] = seconds_of.get(unit.section_id, 0.0) + share
            words_of[unit.section_id] = words_of.get(unit.section_id, 0) + unit.unit.words
    for section in story.ordered_sections():
        seconds = seconds_of.get(section.section_id, 0.0)
        words = words_of.get(section.section_id, 0)
        if seconds <= 0:
            continue
        paces.append(
            SectionPace(
                section_id=section.section_id,
                label=section.label,
                seconds=round(seconds, 2),
                words=words,
            )
        )
        if words < _MIN_WORDS:
            continue
        wpm = words / seconds * 60
        if wpm >= _DENSE_WPM:
            detail = (
                f"dense: {wpm:.0f} words per minute over {seconds:.1f} s; "
                "a pause or a visual break would help"
            )
        elif wpm <= _SLOW_WPM:
            detail = f"slow: {wpm:.0f} words per minute over {seconds:.1f} s; tightening may help"
        else:
            continue
        notes.append(
            PacingNote(
                note_id=note_id(),
                level="section",
                subject=section.section_id,
                detail=detail,
                seconds=round(seconds, 2),
            )
        )
    lengths = [float(p.duration) for p in pieces]
    if len(lengths) >= _MIN_CLIPS:
        mean = statistics.fmean(lengths)
        variation = statistics.pstdev(lengths) / mean if mean > 0 else 0.0
        if variation < _UNIFORM_CV:
            notes.append(
                PacingNote(
                    note_id=note_id(),
                    level="edit",
                    subject="rhythm",
                    detail=(
                        f"clip lengths are nearly uniform (variation {variation:.2f}): "
                        "the rhythm may feel mechanical"
                    ),
                )
            )
    if hooks.pacing is not None:
        for advice in hooks.pacing(paces) or ():
            notes.append(
                PacingNote(
                    note_id=note_id(),
                    level="section",
                    subject=advice.section_id,
                    detail=advice.detail,
                )
            )
    return notes
