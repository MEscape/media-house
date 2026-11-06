"""The compiler's knobs. It makes no creative decision, so there are only technical ones.

``CompilerConfig.fingerprint()`` is part of the cache key and of the specification's
``config_hash``: the same inputs and the same config always give the same specification.
"""

from dataclasses import dataclass, field
from typing import Any

from media_house.core.domain import Rational
from media_house.modules.edit_plan_compiler.domain.errors import InvalidCompilerConfig
from media_house.modules.edit_plan_compiler.domain.timing import fr
from media_house.modules.edit_plan_compiler.domain.values import (
    DEFAULT_SAMPLE_RATE,
    PROFILE_VERSION,
    ColorProfile,
    DerivativePolicy,
)


@dataclass(frozen=True, slots=True)
class CompilerConfig:
    timeline_frame_rate: Rational | None = field(
        default=None,
        metadata={
            "label": "Timeline frame rate",
            "help": "None keeps the edit's own frame rate. A different rate re-snaps every "
            "frame count from the exact times.",
            "unit": "fps",
            "kind": "rational",
        },
    )
    sample_rate: int = field(
        default=DEFAULT_SAMPLE_RATE,
        metadata={
            "label": "Audio sample rate",
            "help": "Sample positions of audio items are stated at this rate.",
            "unit": "Hz",
            "min": 8_000,
            "max": 192_000,
            "kind": "int",
        },
    )
    derivative_policy: DerivativePolicy = field(
        default=DerivativePolicy.ORIGINAL,
        metadata={
            "label": "Recording version",
            "help": "original: clips point at the recording. prefer_improved: at the stored "
            "improved audio / video where one exists (the edit plan never chooses).",
            "choices": [p.value for p in DerivativePolicy],
            "kind": "choice",
        },
    )
    color_profile: ColorProfile = field(
        default=ColorProfile.REC709,
        metadata={
            "label": "Timeline colour",
            "help": "The working colour the timeline is set to. Sources tagged otherwise are "
            "reported; nothing is converted here.",
            "choices": [p.value for p in ColorProfile],
            "kind": "choice",
        },
    )
    allow_style_mismatch: bool = field(
        default=False,
        metadata={
            "label": "Allow plans made with another style",
            "help": "Off: a Layer 4 plan whose style fingerprint differs from the resolved "
            "style blocks the timeline (the look would break channel consistency).",
            "kind": "bool",
            "advanced": True,
        },
    )
    require_rendered_overlays: bool = field(
        default=False,
        metadata={
            "label": "Require rendered graphics and captions",
            "help": "On: a graphic or caption group without a rendered file blocks the "
            "timeline. Off: it is reported and left out (captions stay in the subtitle file).",
            "kind": "bool",
        },
    )
    drop_identity_violations: bool = field(
        default=False,
        metadata={
            "label": "Leave out items that break a locked channel rule",
            "help": "Off: such an item stays in the timeline and is reported as a creative "
            "conflict. On: it is left out and the omission is reported.",
            "kind": "bool",
            "advanced": True,
        },
    )
    gap_tolerance_frames: int = field(
        default=0,
        metadata={
            "label": "Tolerated gap on the main picture",
            "unit": "frames",
            "min": 0,
            "max": 30,
            "kind": "int",
            "advanced": True,
        },
    )
    handle_tolerance_frames: int = field(
        default=0,
        metadata={
            "label": "Tolerated handle shortfall",
            "help": "Frames a source may be short of a transition handle before the "
            "transition falls back to a hard cut.",
            "unit": "frames",
            "min": 0,
            "max": 30,
            "kind": "int",
            "advanced": True,
        },
    )
    version: int = PROFILE_VERSION

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.timeline_frame_rate is not None and fr(self.timeline_frame_rate) <= 0:
            found.append("timeline_frame_rate must be positive")
        if not 8_000 <= self.sample_rate <= 192_000:
            found.append("sample_rate must be between 8000 and 192000")
        if not 0 <= self.gap_tolerance_frames <= 30:
            found.append("gap_tolerance_frames must be between 0 and 30")
        if not 0 <= self.handle_tolerance_frames <= 30:
            found.append("handle_tolerance_frames must be between 0 and 30")
        return found

    def check(self) -> None:
        found = self.problems()
        if found:
            raise InvalidCompilerConfig("; ".join(found))

    def fingerprint(self) -> dict[str, Any]:
        rate = self.timeline_frame_rate
        return {
            "timeline_frame_rate": None if rate is None else str(rate),
            "sample_rate": self.sample_rate,
            "derivative_policy": self.derivative_policy.value,
            "color_profile": self.color_profile.value,
            "allow_style_mismatch": self.allow_style_mismatch,
            "require_rendered_overlays": self.require_rendered_overlays,
            "drop_identity_violations": self.drop_identity_violations,
            "gap_tolerance_frames": self.gap_tolerance_frames,
            "handle_tolerance_frames": self.handle_tolerance_frames,
            "version": self.version,
        }
