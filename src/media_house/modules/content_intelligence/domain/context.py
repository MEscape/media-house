"""Optional observations from other analyses, reduced to what the semantic analysis uses.

Both are supplied by the application layer's adapters from the public contracts of Audio and
Video Intelligence. Their times are on the same original-media timeline as the transcript; this
module relates them to sentences ONLY by temporal overlap and never claims word-to-frame
synchronisation. Absent context is simply ``None``: nothing in the analysis requires it.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualShot:
    shot_id: str
    start: float
    end: float
    scene_id: str | None = None
    #: A description or classification the visual analysis already produced, if any.
    description: str | None = None
    objects: tuple[str, ...] = ()
    #: On-screen text read in this shot (OCR) and overlays that carry text.
    screen_text: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualContext:
    asset_id: str
    #: Fingerprint of the stored visual analysis (part of this result's identity).
    fingerprint: str
    duration: float | None
    shots: tuple[VisualShot, ...]

    def shots_between(self, start: float, end: float) -> tuple[VisualShot, ...]:
        return tuple(s for s in self.shots if s.end > start and s.start < end)

    def scene_cuts(self) -> tuple[float, ...]:
        """Times at which one scene ends and another begins."""
        return tuple(
            b.start
            for a, b in zip(self.shots, self.shots[1:], strict=False)
            if a.scene_id is not None and b.scene_id is not None and a.scene_id != b.scene_id
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AudioCues:
    """Measured delivery evidence. It can corroborate a passage; it never creates importance."""

    asset_id: str
    fingerprint: str
    #: ``(start, end, score)`` of words delivered with notable emphasis (score in [0, 1]).
    emphasis: tuple[tuple[float, float, float], ...] = ()
    #: ``(start, end)`` of laughter events.
    laughter: tuple[tuple[float, float], ...] = ()

    def emphasis_between(self, start: float, end: float) -> float | None:
        """Highest emphasis score of a word inside the range, else ``None``."""
        scores = [s for a, b, s in self.emphasis if b > start and a < end]
        return max(scores) if scores else None

    def laughter_between(self, start: float, end: float) -> bool:
        return any(b > start and a < end for a, b in self.laughter)
