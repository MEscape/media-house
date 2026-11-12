"""Which recorded moments make a good reaction picture, from STORED per-shot face cues.

The cues are what video intelligence saw (face size, sharpness, open eyes, expression cues); they
are not emotions. A thumbnail reaction wants a big, sharp face with open eyes and a visible
expression, so shots are ranked by exactly that and the person picks (or the best one is taken).
Recordings named as reactions ("reaktion", "reaction") are preferred: the owner filmed them for it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

#: A shot shorter than this is a flash, not a pose.
MIN_SHOT_SECONDS: Final = 0.4
#: Faces smaller than this share of the frame height make a weak thumbnail.
MIN_FACE_HEIGHT: Final = 0.12
_REACTION_WORDS: Final = ("reaktion", "reaction", "react")


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotCue:
    asset_id: str
    asset_name: str
    shot_id: str
    start_seconds: float
    end_seconds: float
    face_frames: int
    #: Mean face height as a share of the frame height (0-1).
    face_height: float | None
    face_sharpness: float | None
    eyes_closed: float | None
    smile: float | None
    #: Mouth movement (a speaking cue); a reaction often has an open mouth too.
    mouth_activity: float | None


@dataclass(frozen=True, slots=True)
class ReactionMoment:
    asset_id: str
    shot_id: str
    seconds: float
    score: float
    reasons: tuple[str, ...]


def _v(value: float | None, default: float = 0.0) -> float:
    return default if value is None else max(0.0, min(1.0, value))


def score(cue: ShotCue) -> ReactionMoment | None:
    if cue.face_frames <= 0 or cue.end_seconds - cue.start_seconds < MIN_SHOT_SECONDS:
        return None
    height = _v(cue.face_height)
    if height < MIN_FACE_HEIGHT:
        return None
    eyes_open = 1.0 - _v(cue.eyes_closed, 0.5)
    expression = max(_v(cue.smile), _v(cue.mouth_activity))
    named = any(w in cue.asset_name.casefold() for w in _REACTION_WORDS)
    value = (
        0.35 * min(1.0, height / 0.5)
        + 0.25 * _v(cue.face_sharpness, 0.5)
        + 0.2 * eyes_open
        + 0.2 * expression
        + (0.15 if named else 0.0)
    )
    reasons = [f"face {height:.0%} of frame", f"eyes open {eyes_open:.0%}"]
    if expression >= 0.3:
        reasons.append("visible expression")
    if named:
        reasons.append("reaction recording")
    return ReactionMoment(
        cue.asset_id,
        cue.shot_id,
        round((cue.start_seconds + cue.end_seconds) / 2, 3),
        round(value, 4),
        tuple(reasons),
    )


def rank(cues: Iterable[ShotCue], limit: int) -> tuple[ReactionMoment, ...]:
    """Best first; ties by asset and time so the order is stable."""
    moments = [m for m in (score(c) for c in cues) if m is not None]
    moments.sort(key=lambda m: (-m.score, m.asset_id, m.seconds))
    return tuple(moments[:limit])
