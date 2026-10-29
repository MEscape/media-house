"""Reframing profiles and configuration: data, not code.

A profile says how a kind of video wants to be framed (where the subject sits, how much movement is
welcome, how much enlargement is tolerated). It changes the recommendation, never the measurement:
the boxes, margins and scale ratios of a crop are the same under every profile. Every threshold is
a named design choice, not a calibrated value.

Safe areas are part of the CONFIGURATION, never of a profile: a platform's interface margins are
not assumed here. A caller that knows the layout of captions, titles or a platform's interface
lists the reserved rectangles of the OUTPUT picture (0-1, top-left origin); with none listed, no
safe-area constraint exists and the score says ``None``.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import Final

from media_house.modules.reframing_intelligence.domain.errors import InvalidReframingConfig
from media_house.modules.reframing_intelligence.domain.values import PROFILE_VERSION


@dataclass(frozen=True, slots=True)
class Composition:
    """Where the subject sits. The numbers are preferences, not rules of beauty."""

    #: Where the subject's centre sits across the output (0.5 centred, 1/3 = a third line).
    anchor_x: float = 0.5
    #: Gap above the subject (face, else person) as a share of the output height.
    headroom: float = 0.08
    #: How far the headroom may differ from the target before the composition score drops to 0.
    headroom_tolerance: float = 0.12
    #: Give a subject that looks or travels one way more room on that side.
    lead_room: bool = False
    lead_room_shift: float = 0.08
    #: Smallest gap kept between a required element and a crop edge (share of the crop).
    margin: float = 0.03


@dataclass(frozen=True, slots=True)
class Motion:
    """How the window may move. A shot becomes one still crop unless a subject really leaves it."""

    #: Allow a moving window at all (False = one still crop per shot, or an explicit problem).
    follow: bool = True
    #: The window waits until the subject leaves this share of the crop width.
    dead_zone: float = 0.12
    #: Largest speed of the window centre, in crop widths per second.
    max_speed: float = 0.35
    #: Largest change of zoom per second (zoom 1 = the widest window).
    max_zoom_rate: float = 0.08
    #: A framing change is not followed by another within this many seconds (unless a required
    #: element would be lost).
    min_dwell: float = 1.5
    #: Track points further apart than this are a gap: nothing is interpolated across it.
    max_gap: float = 0.6
    #: Noisy positions are averaged over this many seconds each side, and the window eases toward
    #: where the subject is with this time constant (0 = no smoothing, straight to the goal).
    smoothing: float = 0.5
    #: Track points below this confidence are not trusted.
    min_confidence: float = 0.3
    #: Keyframes are dropped while the straight line between their neighbours is within this
    #: distance of the path (share of the crop width).
    key_tolerance: float = 0.01
    #: A framing change smaller than this (crop widths) is not worth a keyframe.
    min_change: float = 0.02


@dataclass(frozen=True, slots=True)
class Emphasis:
    """When a slow push-in is welcome."""

    push_in: bool = False
    #: Beat functions of the story plan that earn it.
    functions: tuple[str, ...] = ("claim", "turning_point", "payoff", "hook")
    #: How tight the end of the push-in is (zoom 1 = widest window).
    zoom: float = 0.85
    min_seconds: float = 2.5


@dataclass(frozen=True, slots=True)
class Quality:
    """What enlargement and softness a crop may carry."""

    #: Up to this enlargement (output pixels per stored sample) nothing is said.
    upscale_ok: float = 1.25
    #: Beyond this the crop is ``low_quality``.
    upscale_max: float = 2.0
    #: An enlarged picture whose usability score is below this is ``low_quality``.
    min_usability: float = 0.35


@dataclass(frozen=True, slots=True)
class Participants:
    """Who must stay in the picture."""

    #: People who share the shot with the main subject and matter to it are required elements.
    required: bool = True
    #: A person counts as a participant when at least this tall as the main subject ...
    height_ratio: float = 0.5
    #: ... and present for at least this share of the shot.
    coverage: float = 0.5


@dataclass(frozen=True, slots=True)
class Adjacency:
    """When two neighbouring crops read as a jump."""

    #: Subject size ratio between neighbouring shots above this is a visible step.
    scale_ratio: float = 1.4
    #: Subject place difference across the output above this is a visible step.
    shift: float = 0.2


@dataclass(frozen=True, slots=True)
class ReframingProfile:
    name: str
    description: str
    version: int = PROFILE_VERSION
    composition: Composition = field(default_factory=Composition)
    motion: Motion = field(default_factory=Motion)
    emphasis: Emphasis = field(default_factory=Emphasis)
    quality: Quality = field(default_factory=Quality)
    participants: Participants = field(default_factory=Participants)
    adjacency: Adjacency = field(default_factory=Adjacency)
    #: Propose "the original frame with a fill" when no crop can keep what is required. The
    #: treatment needs a capability no stage here provides, so it stays a proposal.
    allow_fill: bool = False

    def problems(self) -> list[str]:
        c, m, e, q = self.composition, self.motion, self.emphasis, self.quality
        found: list[str] = []
        if not 0.0 < c.anchor_x < 1.0:
            found.append("composition.anchor_x must lie within (0, 1)")
        if not 0.0 <= c.margin < 0.3:
            found.append("composition.margin must lie within [0, 0.3)")
        if not 0.0 <= c.headroom < 0.5 or c.headroom_tolerance <= 0:
            found.append("composition.headroom must lie within [0, 0.5) with a positive tolerance")
        if m.max_speed <= 0 or m.max_zoom_rate <= 0 or m.min_dwell < 0 or m.max_gap <= 0:
            found.append("motion limits must be positive")
        if m.smoothing < 0:
            found.append("motion.smoothing must not be negative")
        if not 0.0 <= m.dead_zone < 1.0 or not 0.0 <= m.min_confidence <= 1.0:
            found.append("motion.dead_zone and min_confidence must lie within [0, 1)")
        if not 0.2 <= e.zoom <= 1.0:
            found.append("emphasis.zoom must lie within [0.2, 1]")
        if q.upscale_ok < 1.0 or q.upscale_max <= q.upscale_ok:
            found.append("quality: 1 <= upscale_ok < upscale_max is required")
        return found


def _p(name: str, description: str, **parts: object) -> ReframingProfile:
    return ReframingProfile(name, description, **parts)  # type: ignore[arg-type]


PROFILES: Final[dict[str, ReframingProfile]] = {
    p.name: p
    for p in (
        _p(
            "generic",
            "Neutral: keep what matters, centred, move only when a subject leaves the window.",
        ),
        _p(
            "talking_head",
            "One speaker: centred, calm window, a gentle push-in only on a strong statement.",
            motion=Motion(dead_zone=0.18, max_speed=0.25, min_dwell=2.5, smoothing=0.8),
            emphasis=Emphasis(push_in=True),
        ),
        _p(
            "interview_podcast",
            "Several speakers stay in the picture; room is left in the direction they look.",
            composition=Composition(lead_room=True),
            motion=Motion(dead_zone=0.2, max_speed=0.2, min_dwell=3.0),
            participants=Participants(required=True, height_ratio=0.4, coverage=0.4),
            allow_fill=True,
        ),
        _p(
            "product_review",
            "The presenter, the product and the action stay together; no decorative movement.",
            composition=Composition(margin=0.05),
            motion=Motion(dead_zone=0.15, max_speed=0.3),
        ),
        _p(
            "tutorial",
            "The demonstration surface and the hands stay in the picture; calm window.",
            composition=Composition(margin=0.05),
            motion=Motion(dead_zone=0.2, max_speed=0.25, min_dwell=3.0),
        ),
        _p(
            "shorts_reels",
            "Vertical and lively: tight on the subject, a push-in on every strong beat.",
            composition=Composition(headroom=0.1, margin=0.02),
            motion=Motion(dead_zone=0.08, max_speed=0.5, max_zoom_rate=0.15, min_dwell=1.0),
            emphasis=Emphasis(push_in=True, zoom=0.78, min_seconds=1.5),
            quality=Quality(upscale_ok=1.5, upscale_max=2.4),
        ),
        _p(
            "cinematic",
            "Deliberate composition: thirds, room to look into, a still window.",
            composition=Composition(anchor_x=1 / 3, lead_room=True, lead_room_shift=0.1),
            motion=Motion(follow=False),
        ),
    )
}
DEFAULT_PROFILE: Final = "generic"

STORY_FORMAT_PROFILE: Final[dict[str, str]] = {
    "top_n": "shorts_reels",
    "review": "product_review",
    "product_introduction": "product_review",
    "unboxing": "product_review",
    "talking_head": "talking_head",
    "tutorial": "tutorial",
    "interview": "interview_podcast",
    "podcast": "interview_podcast",
    "documentary": "cinematic",
    "short": "shorts_reels",
}


@dataclass(frozen=True, slots=True)
class SafeArea:
    """A reserved rectangle of the OUTPUT picture (captions, a title, an interface margin)."""

    name: str
    x0: float
    y0: float
    x1: float
    y1: float

    def problem(self) -> str | None:
        if not (0 <= self.x0 < self.x1 <= 1 and 0 <= self.y0 < self.y1 <= 1):
            return f"safe area {self.name!r} must be a rectangle inside the 0-1 output"
        return None


@dataclass(frozen=True, slots=True)
class ReframingConfig:
    profile: str | None = None
    custom: ReframingProfile | None = None
    safe_areas: tuple[SafeArea, ...] = ()

    def resolve(self, story_format: str | None = None) -> ReframingProfile:
        if self.custom is not None:
            profile = self.custom
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            found = PROFILES.get(name)
            if found is None:
                raise InvalidReframingConfig(
                    f"unknown profile {name!r}; known: {', '.join(sorted(PROFILES))}"
                )
            profile = found
        problems = profile.problems()
        problems += [p for a in self.safe_areas if (p := a.problem())]
        if problems:
            raise InvalidReframingConfig("; ".join(problems))
        return profile

    def fingerprint(self, profile: ReframingProfile) -> dict[str, object]:
        return {
            "profile": dataclasses.asdict(profile),
            "safe_areas": [dataclasses.asdict(a) for a in self.safe_areas],
        }
