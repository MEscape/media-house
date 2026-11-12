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
    jump_cut_zoom: float = field(
        default=1.0,
        metadata={
            "label": "Camera moves on the talking picture",
            "help": "Tightest framing of the camera moves (eased push-ins, fast punch-ins with a "
            "slow release, pulls and holds) that hide jump cuts. 1.0 = off; creators use "
            "1.08-1.15.",
            "min": 1.0,
            "max": 1.5,
            "kind": "float",
        },
    )
    #: Seconds between framing changes of a Short's talking picture; ``None`` = by the output
    #: format (portrait: ``SHORT_BEAT``, landscape: the calm cycle of ``jump_cut_zoom``).
    camera_beat: float | None = field(
        default=None,
        metadata={
            "label": "Camera beat (Shorts)",
            "help": "A new framing (fast punch in/out) every this many seconds, and a zoom "
            "through every cut between shots. Empty = automatic (9:16: 2.2 s, 16:9: off); "
            "0 = off.",
            "unit": "s",
            "min": 0.0,
            "max": 10.0,
            "kind": "float",
        },
    )
    beat_zoom: float = field(
        default=1.3,
        metadata={
            "label": "Camera beat tightest framing",
            "help": "Tightest framing of the camera beats (the widest is the full frame).",
            "min": 1.05,
            "max": 1.45,
            "kind": "float",
        },
    )
    #: Screen recordings shown without their browser bar and task bar: recording asset id ->
    #: the page window (x, y, w, h, normalised, top-left), sorted by asset id.
    screen_windows: tuple[tuple[str, float, float, float, float], ...] = field(
        default=(),
        metadata={
            "label": "Screen recording windows",
            "help": "Per recording the page window shown (x, y, w, h), slowly pushed in.",
            "kind": "windows",
            "advanced": True,
        },
    )
    #: ``crop`` shows only the page window, filling the frame; ``card`` floats the whole page
    #: window as a card over a dark ground (nothing of the page is cut off).
    screen_style: str = field(
        default="crop",
        metadata={
            "label": "Screen recording look",
            "help": "crop = the page fills the frame; card = the page floats as a card",
            "choices": ("crop", "card"),
            "kind": "choice",
        },
    )
    #: Blur of the talking picture while a focus graphic (a chart, list, comparison) is on
    #: screen over it; 0 = off.
    focus_blur: float = field(
        default=0.0,
        metadata={
            "label": "Background blur under focus graphics",
            "help": "Blur size while a chart, list or comparison is on screen; 0 = off.",
            "min": 0.0,
            "max": 30.0,
            "kind": "float",
        },
    )
    #: Footage (asset id) that plays muted under every full-frame graphic; ``None`` = none.
    background_footage: str | None = field(
        default=None,
        metadata={
            "label": "Background footage under full-screen cards",
            "help": "A library asset shown under full-screen graphics (e.g. a fireworks plate).",
            "kind": "asset",
            "advanced": True,
        },
    )
    version: int = PROFILE_VERSION

    def problems(self) -> list[str]:
        found: list[str] = []
        if not 1.0 <= self.jump_cut_zoom <= 1.5:
            found.append("jump_cut_zoom must be between 1.0 and 1.5")
        if self.camera_beat is not None and not 0.0 <= self.camera_beat <= 10.0:
            found.append("camera_beat must be between 0 and 10")
        if not 1.05 <= self.beat_zoom <= 1.45:
            found.append("beat_zoom must be between 1.05 and 1.45")
        if self.screen_style not in ("crop", "card"):
            found.append("screen_style must be crop or card")
        if not 0.0 <= self.focus_blur <= 30.0:
            found.append("focus_blur must be between 0 and 30")
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
            "jump_cut_zoom": self.jump_cut_zoom,
            "camera_beat": self.camera_beat,
            "beat_zoom": self.beat_zoom,
            "screen_windows": [list(w) for w in self.screen_windows],
            "screen_style": self.screen_style,
            "focus_blur": self.focus_blur,
            "background_footage": self.background_footage,
            "version": self.version,
        }
