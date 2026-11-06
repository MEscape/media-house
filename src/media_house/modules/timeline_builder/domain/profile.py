"""The builder's knobs. It makes no creative decision, so there are only technical and policy ones.

``BuilderConfig.fingerprint()`` is part of the cache key: the same specification and the same
config always give the same timeline. ``Capabilities`` is what a later timeline application
declares it can carry out; the default is the builder's own vocabulary.
"""

from dataclasses import dataclass, field
from typing import Any

from media_house.modules.timeline_builder.domain.errors import InvalidBuilderConfig
from media_house.modules.timeline_builder.domain.values import (
    PROFILE_VERSION,
    ConflictPolicy,
    FitMode,
)

#: Transition kinds the model builds natively: a cut family, dissolves, fades and dips.
CUT_KINDS = frozenset({"hard_cut", "match_cut", "action_cut", "jump_cut"})
DISSOLVE_KINDS = frozenset({"cross_dissolve"})
FADE_KINDS = frozenset({"fade_in", "fade_out"})
DIP_KINDS = frozenset({"dip_to_black", "dip_to_white"})
NATIVE_TRANSITIONS = CUT_KINDS | DISSOLVE_KINDS | FADE_KINDS | DIP_KINDS

AUDIO_CHANNELS = ("mono", "stereo")


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What the timeline application that will carry the timeline out can do. ``None`` means
    "no limit beyond the model" (the default: the builder is application independent)."""

    transitions: frozenset[str] | None = None
    effects: frozenset[str] | None = None
    reverse: bool = True
    speed_ramps: bool = True
    max_video_tracks: int | None = None
    max_audio_tracks: int | None = None

    def allows_transition(self, kind: str) -> bool:
        return kind in NATIVE_TRANSITIONS and (self.transitions is None or kind in self.transitions)

    def allows_effect(self, effect_type: str) -> bool:
        return self.effects is None or effect_type in self.effects


@dataclass(frozen=True, slots=True)
class BuilderConfig:
    fit_mode: FitMode = field(
        default=FitMode.CONTAIN,
        metadata={
            "label": "Fit to the frame",
            "help": "How a source whose shape differs from the timeline's meets the frame. "
            "contain keeps all of the picture; the choice is reported on every clip it affects.",
            "choices": [m.value for m in FitMode],
            "kind": "choice",
        },
    )
    audio_channels: str = field(
        default="stereo",
        metadata={
            "label": "Timeline audio channels",
            "help": "The channel layout of the timeline's audio tracks.",
            "choices": list(AUDIO_CHANNELS),
            "kind": "choice",
        },
    )
    conflict_policy: ConflictPolicy = field(
        default=ConflictPolicy.REVIEW,
        metadata={
            "label": "When the specification changes what a person changed",
            "help": "preserve: keep the person's version. replace: use the specification's. "
            "review: keep the person's version, hold the new one and ask. Nothing is overwritten "
            "silently.",
            "choices": [p.value for p in ConflictPolicy],
            "kind": "choice",
        },
    )
    block_on_unsupported: bool = field(
        default=False,
        metadata={
            "label": "Block on unsupported operations",
            "help": "Off: an instruction the model cannot carry out is kept and reported. On: it "
            "stops the build.",
            "kind": "bool",
        },
    )
    block_on_unresolved: bool = field(
        default=False,
        metadata={
            "label": "Block on unresolved dependencies",
            "help": "Off: a graphic or caption whose file is missing is kept as an unresolved "
            "clip and reported. On: it stops the build. Missing footage and audio always stop it.",
            "kind": "bool",
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
    section_markers: bool = field(
        default=True,
        metadata={"label": "Section markers", "kind": "bool"},
    )
    review_markers: bool = field(
        default=True,
        metadata={
            "label": "Markers for findings a person should look at",
            "kind": "bool",
        },
    )
    version: int = PROFILE_VERSION

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.audio_channels not in AUDIO_CHANNELS:
            found.append(f"audio_channels must be one of {', '.join(AUDIO_CHANNELS)}")
        if not 0 <= self.gap_tolerance_frames <= 30:
            found.append("gap_tolerance_frames must be between 0 and 30")
        return found

    def check(self) -> None:
        found = self.problems()
        if found:
            raise InvalidBuilderConfig("; ".join(found))

    def fingerprint(self) -> dict[str, Any]:
        return {
            "fit_mode": self.fit_mode.value,
            "audio_channels": self.audio_channels,
            "conflict_policy": self.conflict_policy.value,
            "block_on_unsupported": self.block_on_unsupported,
            "block_on_unresolved": self.block_on_unresolved,
            "gap_tolerance_frames": self.gap_tolerance_frames,
            "section_markers": self.section_markers,
            "review_markers": self.review_markers,
            "version": self.version,
        }
