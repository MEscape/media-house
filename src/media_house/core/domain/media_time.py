"""The canonical position in a video: frame index, presentation timestamp and time base together.

Float seconds alone cannot address a frame exactly (29.97 fps, variable frame rate, editing
timelines), so every module that talks about "when" in a video uses ``FrameTime``:

* ``frame``: index in presentation order, counted from the first presented frame (0).
* ``pts``: presentation timestamp in ticks of ``timebase`` (may be negative for pre-roll).
* ``timebase``: seconds per tick as an exact fraction (for example ``1/30000``).

Frame index and pts are both kept because with variable frame rate neither derives from the other.
"""

from dataclasses import dataclass

from media_house.core.domain.rational import Rational
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True, order=False)
class FrameTime:
    frame: int
    pts: int
    timebase: Rational

    def __post_init__(self) -> None:
        if self.frame < 0:
            raise InvariantViolation(
                "A frame index must not be negative", details={"frame": self.frame}
            )

    @property
    def seconds(self) -> float:
        return self.pts * self.timebase.value

    def as_seconds_text(self) -> str:
        """Seconds with microsecond precision (a stable text form for documents and logs)."""
        return f"{self.seconds:.6f}"


@dataclass(frozen=True, slots=True)
class TimeRange:
    """A half-open span of frames: ``start`` is the first frame, ``end`` the first frame after it.

    For the last range of a video ``end.frame`` equals the frame count and ``end.pts`` is the
    end of the last frame.
    """

    start: FrameTime
    end: FrameTime

    def __post_init__(self) -> None:
        if self.end.frame < self.start.frame or self.end.pts < self.start.pts:
            raise InvariantViolation(
                "A time range must not end before it starts",
                details={"start": self.start.frame, "end": self.end.frame},
            )
        if self.end.timebase != self.start.timebase:
            raise InvariantViolation("Both ends of a time range need the same time base")

    @property
    def frames(self) -> int:
        return self.end.frame - self.start.frame

    @property
    def seconds(self) -> float:
        return self.end.seconds - self.start.seconds

    def contains_frame(self, frame: int) -> bool:
        return self.start.frame <= frame < self.end.frame
