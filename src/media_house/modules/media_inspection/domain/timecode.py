"""SMPTE timecode: parsing, validity against a frame rate, and conversion to a frame number.

``HH:MM:SS:FF`` is non-drop-frame; ``HH:MM:SS;FF`` (or ``.FF``) is drop-frame, which skips frame
numbers (never frames) to keep the clock close to real time at 29.97 / 59.94 fps.
"""

import re
from dataclasses import dataclass

from media_house.modules.media_inspection.domain.values import Rational
from media_house.shared.errors import InvariantViolation

_PATTERN = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})([:;.])(\d{2,3})")
_DROP_PER_MINUTE = {30: 2, 60: 4}


@dataclass(frozen=True, slots=True)
class Timecode:
    hours: int
    minutes: int
    seconds: int
    frames: int
    drop_frame: bool = False

    def __post_init__(self) -> None:
        if not (
            0 <= self.hours < 24
            and 0 <= self.minutes < 60
            and 0 <= self.seconds < 60
            and 0 <= self.frames < 1000
        ):
            raise InvariantViolation("Timecode field out of range", details={"timecode": str(self)})

    @classmethod
    def parse(cls, text: object) -> "Timecode | None":
        """The timecode in ``text``, or ``None`` if it is not one (never a guess)."""
        if not isinstance(text, str):
            return None
        match = _PATTERN.fullmatch(text.strip())
        if match is None:
            return None
        hours, minutes, seconds, separator, frames = match.groups()
        try:
            return cls(int(hours), int(minutes), int(seconds), int(frames), separator != ":")
        except InvariantViolation:
            return None

    def __str__(self) -> str:
        separator = ";" if self.drop_frame else ":"
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}{separator}{self.frames:02d}"

    def problem_for(self, frame_rate: Rational) -> str | None:
        """Why this timecode cannot exist at ``frame_rate``, or ``None`` if it can."""
        nominal = round(frame_rate.value)
        if self.frames >= nominal:
            return f"frame {self.frames} does not exist at {nominal} frames per second"
        if self.drop_frame:
            drop = _DROP_PER_MINUTE.get(nominal)
            if drop is None or frame_rate.denominator == 1:
                return "drop-frame timecode is only defined for 29.97 and 59.94 fps"
            if self.seconds == 0 and self.minutes % 10 != 0 and self.frames < drop:
                return f"frames 0-{drop - 1} are skipped at the start of this minute"
        return None

    def frame_number(self, frame_rate: Rational) -> int:
        """Frames elapsed since 00:00:00:00 at ``frame_rate``."""
        nominal = round(frame_rate.value)
        total = ((self.hours * 60 + self.minutes) * 60 + self.seconds) * nominal + self.frames
        if self.drop_frame and (drop := _DROP_PER_MINUTE.get(nominal)) is not None:
            total_minutes = self.hours * 60 + self.minutes
            total -= drop * (total_minutes - total_minutes // 10)
        return total

    def seconds_at(self, frame_rate: Rational) -> float:
        """Position of this timecode on the wall clock of a recording at ``frame_rate``."""
        return self.frame_number(frame_rate) / frame_rate.value
