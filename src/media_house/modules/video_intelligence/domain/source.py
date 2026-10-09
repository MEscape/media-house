"""What the analysis knows about the footage before looking at a pixel.

``SourceInfo`` is a copy of the technical facts ``media_inspection`` owns, in the shape this module
needs. ``ProcessingHistory`` says which version of the footage was analysed (original or improved)
and what was done to it, as published by the module that did it.
"""

from dataclasses import dataclass, field

from media_house.core.domain import Rational
from media_house.modules.video_intelligence.domain.values import (
    InputSource,
    MeasuredOn,
    Stabilization,
)
from media_house.shared.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Display geometry, timing and colour facts of the analysed video stream."""

    #: Display size in pixels: pixel shape applied, rotation applied.
    width: int
    height: int
    frame_rate: Rational | None
    duration_seconds: float | None
    declared_frame_count: int | None
    rotation: int
    variable_frame_rate: bool | None
    color_transfer: str | None
    color_range: str | None
    camera_make: str | None = None
    camera_model: str | None = None
    timecode: str | None = None
    reel_id: str | None = None
    clip_id: str | None = None

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvariantViolation("The display size must be positive")


@dataclass(frozen=True, slots=True)
class ProcessingHistory:
    """What happened to the analysed version since the original was captured."""

    measured_on: MeasuredOn
    stabilized: Stabilization
    #: Names of the operations applied by the upstream module (empty when unknown or none).
    operations: tuple[str, ...] = ()
    #: The asset this version was derived from, when it is derived.
    derived_from_asset_id: str | None = None


@dataclass(frozen=True, slots=True)
class InputUse:
    """One upstream input and how it was obtained (the proof that nothing was done twice)."""

    name: str
    source: InputSource
    asset_id: str | None = None
    version: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedInputs:
    """Everything the analysis takes from upstream modules, with the record of how."""

    source: SourceInfo
    history: ProcessingHistory
    used: tuple[InputUse, ...]
    warnings: tuple[str, ...] = field(default=())
