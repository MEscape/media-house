"""The configurable knobs of the motion graphics engine (format profiles are data).

Every user-facing knob carries ``dataclasses.field`` metadata for a future settings UI
(``label``, ``help``, ``unit``, ``min``, ``max``, ``step``, ``choices``, ``kind``, ``advanced``);
``problems()`` is derived from that metadata, so UI ranges and engine checks cannot drift.

Durations here are FACTORS of the style's motion tokens or reading speeds, never a look of
their own: colours, fonts, easings, zones and animation durations all come from the resolved
visual style. Precedence: profile defaults -> format profile -> project ``overrides`` ->
``section_overrides`` -> per-graphic person ``decisions``.
"""

from collections.abc import Mapping
from dataclasses import MISSING, Field, asdict, dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from typing import Any, Final

from media_house.modules.motion_graphics_engine.domain.errors import InvalidGraphicsConfig

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def _k[T](default: T, label: str, **meta: object) -> T:
    found: T = field(default=default, metadata={"label": label, **meta})
    return found


def _num[T: (int, float)](
    default: T,
    label: str,
    lo: T,
    hi: T,
    step: T,
    unit: str = "",
    help: str = "",  # noqa: A002
    advanced: bool = False,
) -> T:
    return _k(default, label, min=lo, max=hi, step=step, unit=unit, help=help, advanced=advanced)


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """How long graphics stay up, as multiples of the style's ``motion.min_hold``."""

    hold_factor: float = _num(
        2.0, "Hold", 1.0, 6.0, 0.25, "x min hold", "Shortest hold of a text graphic."
    )
    reading_cps: float = _num(
        14.0, "Reading speed", 6.0, 30.0, 1.0, "chars/s", "Longer texts stay up longer."
    )
    rank_card_factor: float = _num(2.5, "Rank card hold", 1.0, 6.0, 0.25, "x min hold")
    card_factor: float = _num(
        3.0, "Card hold", 1.0, 8.0, 0.25, "x min hold", "Spec cards, charts, plugs."
    )
    teaser_factor: float = _num(2.0, "Teaser hold", 1.0, 6.0, 0.25, "x min hold")
    #: The teaser of the next rank ends this many min-holds before the next rank starts.
    teaser_lead_factor: float = _num(0.5, "Teaser lead", 0.0, 4.0, 0.25, "x min hold")
    on_screen_window: float = _num(
        1.5, "On-screen text window", 0.0, 5.0, 0.25, "s",
        "A card is not drawn when the picture shows the same text this close to it (video OCR).",
    )  # fmt: skip


@dataclass(frozen=True, slots=True, kw_only=True)
class Bookends:
    """Brand items the format mandates (added by motion, recorded with their reason)."""

    end_screen: bool = _k(True, "End screen", help="Fire end screen with ABONNIEREN.")
    sponsor_label: bool = _k(
        True, "Sponsor label", help="'Werbung' for sponsored parts (mandatory rule)."
    )
    sponsor_text: str = _k("Werbung", "Sponsor label text")
    membership_plug: bool = _k(False, "Membership plug", help="Only when the edit asks.")


@dataclass(frozen=True, slots=True, kw_only=True)
class Templates:
    """Format templates built from the brief's structure (not editorial choices)."""

    rank_cards: bool = _k(False, "Rank cards", help="'PLATZ N' at every ranked section.")
    rank_label: str = _k("Platz", "Rank word")
    rank_teaser: bool = _k(
        False, "Next-rank teaser", help="Black silhouette of the next rank's product."
    )
    price_tags: bool = _k(
        False, "Price tags", help="A price tag at every spoken price (once per product)."
    )


class ActiveWord(StrEnum):
    HIGHLIGHT = "highlight"
    NONE = "none"


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptionExecution:
    """How caption groups are animated (caption_engine decides the words)."""

    active_word: str = _k(
        "highlight",
        "Active word",
        choices=tuple(a.value for a in ActiveWord),
        help="highlight = the spoken word takes the highlight colour.",
    )
    word_reveal: bool = _k(True, "Word-by-word reveal")
    batch_seconds: float = _num(
        60.0, "Caption batch", 0.0, 600.0, 5.0, "s",
        "Caption groups within this span render as one file (one Chrome start); 0 = one file"
        " per group.", advanced=True,
    )  # fmt: skip


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderSettings:
    quality: str = _k("final", "Quality", choices=("final", "preview"))
    preview_scale: float = _num(0.5, "Preview size", 0.25, 1.0, 0.25, "x output")
    workers: int = _num(
        4, "Render workers", 1, 8, 1, help="Parallel Chrome processes.", advanced=True
    )
    timeout_seconds: int = _num(900, "Render timeout", 60, 7200, 60, "seconds", advanced=True)
    software_gl: bool = _k(
        True, "Software rendering", help="Same pixels on every machine.", advanced=True
    )
    alpha_samples: int = _num(3, "Alpha check frames", 1, 9, 1, advanced=True)


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionGraphicsProfile:
    name: str
    description: str
    version: int = 1
    timing: Timing = field(default_factory=Timing)
    bookends: Bookends = field(default_factory=Bookends)
    templates: Templates = field(default_factory=Templates)
    captions: CaptionExecution = field(default_factory=CaptionExecution)
    render: RenderSettings = field(default_factory=RenderSettings)

    def problems(self) -> list[str]:
        return knob_problems(self)


def knob_problems(group: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    for f in fields(group):  # type: ignore[arg-type]
        value = getattr(group, f.name)
        path = f"{prefix}{f.name}"
        if is_dataclass(value):
            found += knob_problems(value, f"{path}.")
            continue
        meta = f.metadata
        if isinstance(value, int | float) and not isinstance(value, bool):
            if "min" in meta and value < meta["min"]:
                found.append(f"{path}: {value} is below {meta['min']}")
            if "max" in meta and value > meta["max"]:
                found.append(f"{path}: {value} is above {meta['max']}")
        if "choices" in meta and value not in meta["choices"]:
            found.append(f"{path}: {value!r} is not one of {', '.join(meta['choices'])}")
        if isinstance(value, str) and not value.strip():
            found.append(f"{path}: must not be empty")
    return found


def knob_fields(group: type[Any], prefix: str = "") -> list[tuple[str, Field[Any]]]:
    """Every leaf knob as (dotted path, field): what a settings UI lists."""
    found: list[tuple[str, Field[Any]]] = []
    for f in fields(group):
        if f.name in ("name", "description", "version"):
            continue
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default):
            found += knob_fields(type(default), f"{prefix}{f.name}.")
        else:
            found.append((f"{prefix}{f.name}", f))
    return found


_BASE: Final = MotionGraphicsProfile(
    name="generic", description="Long-form default: end screen, sponsor label when sponsored."
)

PROFILES: Final[dict[str, MotionGraphicsProfile]] = {
    p.name: p
    for p in (
        _BASE,
        replace(
            _BASE,
            name="ranking",
            description="Top-N countdown: PLATZ N card per rank and the next-rank silhouette.",
            templates=Templates(rank_cards=True, rank_teaser=True, price_tags=True),
        ),
        replace(
            _BASE,
            name="shorts",
            description="9:16 Shorts: no end screen (the platform shows its own).",
            bookends=Bookends(end_screen=False),
            templates=Templates(price_tags=True),
            timing=Timing(hold_factor=1.5),
        ),
        replace(
            _BASE,
            name="calm",
            description="Uncut / brochure formats: longer holds, nothing extra.",
            timing=Timing(hold_factor=3.0, reading_cps=11.0),
        ),
    )
}
DEFAULT_PROFILE: Final = "generic"
#: Story format / editing profile -> motion profile.
STORY_FORMAT_PROFILE: Final[dict[str, str]] = {
    "top_n": "ranking",
    "ranking": "ranking",
    "short": "shorts",
    "shorts_meme": "shorts",
    "shorts_haul": "shorts",
    "shorts_product_test": "shorts",
    "shorts_versus": "shorts",
    "review": "calm",
    "brochure_review": "calm",
    "nye_uncut": "calm",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionOverride:
    section_id: str
    overrides: Mapping[str, JsonValue]


class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphicDecision:
    """A person's decision about one planned graphic (survives replans: ids are stable)."""

    graphic_id: str
    action: DecisionAction
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionGraphicsConfig:
    profile: str | MotionGraphicsProfile | None = None
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)
    section_overrides: tuple[SectionOverride, ...] = ()
    decisions: tuple[GraphicDecision, ...] = ()

    def resolve(self, story_format: str | None = None) -> MotionGraphicsProfile:
        if isinstance(self.profile, MotionGraphicsProfile):
            profile = self.profile
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            if name not in PROFILES:
                raise InvalidGraphicsConfig(
                    f"unknown profile {name!r}; choose one of {', '.join(sorted(PROFILES))}"
                )
            profile = PROFILES[name]
        profile = apply_overrides(profile, self.overrides)
        problems = profile.problems()
        for section in self.section_overrides:
            problems += apply_overrides(profile, section.overrides).problems()
        if problems:
            raise InvalidGraphicsConfig("; ".join(problems))
        return profile

    def for_section(
        self, profile: MotionGraphicsProfile, section_id: str | None
    ) -> MotionGraphicsProfile:
        for section in self.section_overrides:
            if section.section_id == section_id:
                return apply_overrides(profile, section.overrides)
        return profile

    def fingerprint(self, profile: MotionGraphicsProfile) -> dict[str, object]:
        return {
            "profile": asdict(profile),
            "sections": [
                {"section_id": s.section_id, "overrides": dict(sorted(s.overrides.items()))}
                for s in self.section_overrides
            ],
            "decisions": sorted((d.graphic_id, d.action.value) for d in self.decisions),
        }


def apply_overrides(
    profile: MotionGraphicsProfile, overrides: Mapping[str, JsonValue]
) -> MotionGraphicsProfile:
    """``profile`` with dotted-path overrides applied (``{"timing.hold_factor": 3.0}``)."""
    for path, value in sorted(overrides.items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidGraphicsConfig(f"unknown setting {path!r}")
    if head in ("name", "version"):
        raise InvalidGraphicsConfig(f"{path!r} cannot be overridden")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: JsonValue, path: str) -> object:
    if is_dataclass(current):
        raise InvalidGraphicsConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        if ok:
            return float(value)  # type: ignore[arg-type]
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidGraphicsConfig(f"{path!r} expects {type(current).__name__}, got {value!r}")
    return value
