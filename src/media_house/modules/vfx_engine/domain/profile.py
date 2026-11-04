"""Effect profiles are data: every creative number of the VFX engine lives here.

Each user-facing knob carries ``dataclasses.field`` metadata (contracts sec. 7: ``label``,
``help``, ``unit``, ``min``, ``max``, ``step``, ``choices``, ``kind``, ``advanced``) and
``problems()`` enforces exactly those ranges, so a future settings UI and the checks cannot drift.

``apply`` says when an effect may be used at all:

* ``never``   - not in this profile;
* ``request`` - only where an editorial or personal request asks for it;
* ``auto``    - also where the footage itself gives a reason (a burst for slow motion, a face
  for privacy, every eligible clip for a look), always behind the "do not destroy good media"
  guards of that effect.

Precedence (each step touches only the keys it names): profile defaults -> channel style
(``motion.intensity_ceiling`` caps ``selection.intensity_ceiling``) -> format profile ->
project ``overrides`` -> ``section_overrides`` -> per-effect person decisions.
"""

from collections.abc import Mapping
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass, replace
from typing import Any, Final

from media_house.modules.vfx_engine.domain.errors import InvalidVfxConfig
from media_house.modules.vfx_engine.domain.values import PROFILE_VERSION, Decision

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None

APPLY: Final = ("never", "request", "auto")
INTERPOLATIONS: Final = ("duplicate", "blend", "motion")
RETIME_AUDIO: Final = ("keep_original", "mute", "pitch_preserving_retime")
BLUR_KINDS: Final = ("gaussian", "box", "directional")
CONCEAL_METHODS: Final = ("blur", "pixelate")
PRIVACY_FALLBACKS: Final = ("full_frame", "drop")
UNVERIFIED: Final = ("warn", "full_frame")
STYLIZE_KINDS: Final = ("monochrome", "posterize")
CORNERS: Final = ("top_left", "top_right", "bottom_left", "bottom_right")
BLEND_MODES: Final = ("normal", "screen", "multiply", "overlay", "addition")


def _k(default: Any, label: str, **meta: Any) -> Any:
    return field(default=default, metadata={"label": label, **meta})


def _share(default: float, label: str, hint: str = "", **meta: Any) -> Any:
    return _k(default, label, unit="fraction", min=0.0, max=1.0, step=0.01, help=hint, **meta)


def _apply(default: str, label: str) -> Any:
    return _k(default, label, choices=APPLY, help="never / only on request / also automatically")


@dataclass(frozen=True, slots=True, kw_only=True)
class Selection:
    intensity_ceiling: int = _k(
        2,
        "Effect intensity ceiling",
        min=0,
        max=4,
        step=1,
        help="0 = none ... 4 = maximal; the channel style's ceiling caps it further.",
    )
    honor_requests: bool = _k(True, "Honour editorial requests")
    min_request_priority: float = _share(0.0, "Lowest request priority used")
    emphasis_per_minute: float = _k(
        4.0,
        "Emphasis effects per minute",
        unit="per minute",
        min=0.0,
        max=30.0,
        step=0.5,
        help="Budget for punch-ins, slow motion and freezes together (strongest first).",
    )
    min_gap: float = _k(
        4.0,
        "Gap between repeated effects",
        unit="seconds",
        min=0.0,
        max=60.0,
        step=0.5,
        help="The same emphasis effect is not repeated on the timeline closer than this.",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PunchIn:
    apply: str = _apply("request", "Punch-in")
    scale: float = _k(1.1, "Punch-in scale", unit="x", min=1.0, max=1.5, step=0.01)
    ease: float = _k(
        0.0,
        "Punch-in ease",
        unit="seconds",
        min=0.0,
        max=1.0,
        step=0.05,
        help="0 = a hard punch on the frame; otherwise a smooth push over this time.",
    )
    hold: float = _k(
        1.5,
        "Punch-in hold",
        unit="seconds",
        min=0.3,
        max=10.0,
        step=0.1,
        help="How long the punch lasts when the request names no duration.",
    )
    min_effective_ratio: float = _k(
        0.8,
        "Minimum effective resolution",
        unit="fraction",
        min=0.5,
        max=1.0,
        step=0.05,
        help="Source pixels per output pixel after the zoom; below it the scale is reduced.",
        advanced=True,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Retime:
    apply: str = _apply("never", "Slow motion / freeze")
    slow_rate: float = _k(0.5, "Slow motion speed", unit="x", min=0.1, max=0.95, step=0.05)
    pre_roll: float = _k(
        0.4, "Slow motion before the peak", unit="seconds", min=0.0, max=3.0, step=0.1
    )
    post_roll: float = _k(
        1.2, "Slow motion after the peak", unit="seconds", min=0.0, max=5.0, step=0.1
    )
    freeze: float = _k(
        0.0,
        "Freeze at the peak",
        unit="seconds",
        min=0.0,
        max=3.0,
        step=0.1,
        help="Hold the burst's peak frame; 0 = no freeze.",
    )
    min_flash_strength: float = _share(0.5, "Weakest burst that triggers it")
    min_bang_strength: float = _share(
        0.5,
        "Weakest bang that marks a burst",
        "Stored audio impulses (bangs) mark bursts where the picture analysis found none.",
    )
    peak_before: float = _k(
        1.0,
        "Visual peak search before the bang",
        unit="seconds",
        min=0.0,
        max=3.0,
        step=0.1,
        help="Light arrives before the sound (343 m/s): a burst 300 m away flashes ~0.9 s"
        " before its bang is heard.",
        advanced=True,
    )
    peak_after: float = _k(
        0.5,
        "Visual peak search after the bang",
        unit="seconds",
        min=0.0,
        max=3.0,
        step=0.1,
        help="A burst keeps opening after its break.",
        advanced=True,
    )
    min_luma_rise: float = _k(
        0.02,
        "Brightness rise of a visual peak",
        unit="luma_fraction",
        min=0.0,
        max=0.5,
        step=0.01,
        help="Below this rise of the median brightness the bang time itself is the peak.",
        advanced=True,
    )
    interpolation: str = _k(
        "blend",
        "Frame interpolation",
        choices=INTERPOLATIONS,
        help="duplicate = exact frames; blend = frame blending (12-bit); motion = optical flow "
        "(8-bit only, slow: needs 'Allow 8-bit interpolation').",
    )
    allow_8bit_interpolation: bool = _k(False, "Allow 8-bit interpolation", advanced=True)
    max_interpolated_seconds: float = _k(
        10.0,
        "Interpolation budget",
        unit="seconds",
        min=0.0,
        max=120.0,
        step=1.0,
        help="Longest output that motion interpolation may render (it is slow on the CPU).",
        advanced=True,
    )
    audio: str = _k(
        "keep_original",
        "Audio under retimed picture",
        choices=RETIME_AUDIO,
        help="An intent for the audio stage; nothing is processed here.",
    )
    protect_speech: bool = _k(True, "Never retime over speech")
    reverse_max: float = _k(3.0, "Longest reverse", unit="seconds", min=0.0, max=10.0, step=0.5)


@dataclass(frozen=True, slots=True, kw_only=True)
class Glow:
    apply: str = _apply("never", "Glow / bloom")
    threshold: float = _share(0.82, "Glow threshold", "Brightness from which light blooms.")
    radius: float = _k(0.02, "Glow radius", unit="fraction", min=0.001, max=0.1, step=0.001)
    strength: float = _share(0.3, "Glow strength")
    max_clipped: float = _k(
        0.01,
        "Skip when clipped above",
        unit="fraction",
        min=0.0,
        max=0.2,
        step=0.005,
        help="Footage that already clips (fireworks bursts) gets no extra bloom.",
    )
    dark_luma: float = _share(
        0.1,
        "Night picture below",
        "On a picture this dark any clipping is a local burst or light: no bloom then.",
    )
    tint: str = _k("", "Glow tint", kind="color_token", help="A style palette name, or none.")


@dataclass(frozen=True, slots=True, kw_only=True)
class LightRays:
    apply: str = _apply("never", "Light rays")
    threshold: float = _share(0.85, "Light ray threshold")
    length: float = _k(0.15, "Ray length", unit="fraction", min=0.02, max=0.5, step=0.01)
    strength: float = _share(0.35, "Ray strength")


@dataclass(frozen=True, slots=True, kw_only=True)
class Vignette:
    apply: str = _apply("never", "Vignette")
    strength: float = _k(0.25, "Vignette strength", unit="fraction", min=0.0, max=0.6, step=0.01)
    radius: float = _k(0.55, "Vignette start", unit="fraction", min=0.2, max=1.0, step=0.05)
    min_luma: float = _share(0.12, "Skip when darker than", "Median brightness of the shot.")


@dataclass(frozen=True, slots=True, kw_only=True)
class Grain:
    apply: str = _apply("never", "Film grain")
    amount: float = _k(0.02, "Grain amount", unit="fraction", min=0.0, max=0.08, step=0.005)
    max_noise: float = _k(
        0.02,
        "Skip when noisier than",
        unit="fraction",
        min=0.0,
        max=0.2,
        step=0.005,
        help="Footage that is already noisy (night) gets no extra grain.",
    )
    dark_luma: float = _share(
        0.1,
        "Skip when darker than",
        "Grain on a night sky reads as sensor noise in the blacks.",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class FilmDamage:
    apply: str = _apply("never", "Film damage")
    flicker: float = _k(0.06, "Exposure flicker", unit="stops", min=0.0, max=0.3, step=0.01)
    scratches: float = _k(1.5, "Scratches", unit="per second", min=0.0, max=10.0, step=0.5)


@dataclass(frozen=True, slots=True, kw_only=True)
class Chromatic:
    apply: str = _apply("never", "Chromatic aberration")
    shift: float = _k(0.002, "Channel shift", unit="fraction", min=0.0, max=0.01, step=0.0005)


@dataclass(frozen=True, slots=True, kw_only=True)
class Lens:
    apply: str = _apply("never", "Lens distortion")
    k1: float = _k(-0.08, "Radial k1", min=-0.5, max=0.5, step=0.01)
    k2: float = _k(0.0, "Radial k2", min=-0.5, max=0.5, step=0.01)


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionBlur:
    apply: str = _apply("never", "Motion blur")
    frames: int = _k(3, "Blended frames", min=2, max=8, step=1)
    min_camera_speed: float = _k(
        0.5, "Only above camera speed", unit="frame widths/s", min=0.0, max=5.0, step=0.1
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Blur:
    kind: str = _k("gaussian", "Blur kind", choices=BLUR_KINDS)
    sigma: float = _k(0.01, "Blur size", unit="fraction", min=0.001, max=0.1, step=0.001)
    angle: float = _k(0.0, "Directional angle", unit="degrees", min=0.0, max=360.0, step=1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class Stylize:
    apply: str = _apply("never", "Stylisation")
    kind: str = _k("monochrome", "Stylisation", choices=STYLIZE_KINDS)
    levels: int = _k(6, "Posterize levels", min=2, max=64, step=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class Privacy:
    apply: str = _apply("request", "Privacy concealment")
    faces: bool = _k(True, "Conceal faces")
    persons: bool = _k(False, "Conceal whole persons")
    vehicles: bool = _k(False, "Conceal vehicles (plates)")
    exclude_main_subject: bool = _k(True, "Keep the main subject visible")
    method: str = _k("blur", "Concealment", choices=CONCEAL_METHODS)
    strength: float = _k(
        0.35,
        "Concealment strength",
        unit="fraction",
        min=0.1,
        max=1.0,
        step=0.05,
        help="Blur size / mosaic cell as a share of the concealed box.",
    )
    margin: float = _k(
        0.25, "Margin around the track", unit="fraction", min=0.0, max=1.0, step=0.05
    )
    feather: float = _k(0.01, "Edge feather", unit="fraction", min=0.0, max=0.05, step=0.005)
    pad: float = _k(0.3, "Time padding", unit="seconds", min=0.0, max=2.0, step=0.1)
    max_gap: float = _k(
        0.5,
        "Longest track gap bridged",
        unit="seconds",
        min=0.0,
        max=5.0,
        step=0.1,
        help="Longer gaps cannot be trusted; the fallback applies there.",
    )
    min_confidence: float = _share(0.3, "Lowest trusted track confidence")
    fallback: str = _k(
        "full_frame",
        "When tracking fails",
        choices=PRIVACY_FALLBACKS,
        help="full_frame = conceal the whole picture over the gap; drop = do not use that span.",
    )
    unverified: str = _k(
        "warn",
        "Without a picture analysis",
        choices=UNVERIFIED,
        help="warn = record that privacy was not checked; full_frame = conceal everything.",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Background:
    blur: str = _apply("never", "Background blur (needs a matte)")
    sigma: float = _k(
        0.012, "Background blur size", unit="fraction", min=0.001, max=0.1, step=0.001
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Composite:
    pip_scale: float = _k(
        0.3, "Picture-in-picture size", unit="fraction", min=0.1, max=0.6, step=0.05
    )
    corner: str = _k("top_right", "Picture-in-picture corner", choices=CORNERS)
    margin: float = _k(0.04, "Corner margin", unit="fraction", min=0.0, max=0.2, step=0.01)
    opacity: float = _share(1.0, "Layer opacity")
    blend_mode: str = _k("normal", "Blend mode", choices=BLEND_MODES)


@dataclass(frozen=True, slots=True, kw_only=True)
class Qc:
    spike_ratio: float = _k(
        6.0,
        "Flicker spike ratio",
        min=2.0,
        max=20.0,
        step=0.5,
        advanced=True,
        help="A frame change this many times the clip's median counts as a spike.",
    )
    max_new_spikes: int = _k(0, "Allowed new spikes", min=0, max=50, step=1, advanced=True)
    max_clipping_increase: float = _k(
        0.02,
        "Allowed clipping increase",
        unit="fraction",
        min=0.0,
        max=0.2,
        step=0.005,
        advanced=True,
    )
    min_concealment: float = _k(
        0.6,
        "Required detail removal",
        unit="fraction",
        min=0.3,
        max=1.0,
        step=0.05,
        help="Share of the detail inside a concealed region that must be gone.",
        advanced=True,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxProfile:
    name: str
    description: str
    version: int = PROFILE_VERSION
    selection: Selection = field(default_factory=Selection)
    punch_in: PunchIn = field(default_factory=PunchIn)
    retime: Retime = field(default_factory=Retime)
    glow: Glow = field(default_factory=Glow)
    light_rays: LightRays = field(default_factory=LightRays)
    vignette: Vignette = field(default_factory=Vignette)
    grain: Grain = field(default_factory=Grain)
    film_damage: FilmDamage = field(default_factory=FilmDamage)
    chromatic: Chromatic = field(default_factory=Chromatic)
    lens: Lens = field(default_factory=Lens)
    motion_blur: MotionBlur = field(default_factory=MotionBlur)
    blur: Blur = field(default_factory=Blur)
    stylize: Stylize = field(default_factory=Stylize)
    privacy: Privacy = field(default_factory=Privacy)
    background: Background = field(default_factory=Background)
    composite: Composite = field(default_factory=Composite)
    qc: Qc = field(default_factory=Qc)

    def problems(self) -> list[str]:
        found = knob_problems(self)
        if not self.name:
            found.append("name: empty")
        return found


def knob_problems(group: object, prefix: str = "") -> list[str]:
    """Range and choice checks taken from the field metadata (the one source of truth)."""
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
    return found


def knob_fields(cls: type, prefix: str = "") -> list[tuple[str, Mapping[str, Any], Any]]:
    """Every user-facing knob as ``(dotted path, metadata, default)`` (for a settings UI)."""
    out: list[tuple[str, Mapping[str, Any], Any]] = []
    for f in fields(cls):
        if f.default_factory is not MISSING:
            out += knob_fields(f.default_factory().__class__, f"{prefix}{f.name}.")
        elif "label" in f.metadata:
            out.append((f"{prefix}{f.name}", f.metadata, f.default))
    return out


# --- built-in profiles (channel discovery: authentic camera look, restraint) ------------------
def _p(name: str, description: str, **groups: object) -> VfxProfile:
    return replace(VfxProfile(name=name, description=description), **groups)  # type: ignore[arg-type]


PROFILES: Final[dict[str, VfxProfile]] = {
    p.name: p
    for p in (
        _p(
            "clean_enhancement",
            "Almost nothing: only requested punch-ins. Good footage stays as it is.",
            selection=Selection(intensity_ceiling=1),
        ),
        _p(
            "talking_head",
            "Face-cam explanations: gentle punch-ins on emphasis, background blur when a matte "
            "exists.",
            punch_in=PunchIn(scale=1.15),
            background=Background(blur="auto"),
        ),
        _p(
            "product_detail",
            "Packaging and product close-ups: a smooth push onto the product on emphasis.",
            punch_in=PunchIn(scale=1.2, ease=0.25),
            background=Background(blur="request"),
        ),
        _p(
            "demonstration",
            "Showing how something works: punch-ins on request, compositing only when asked.",
            punch_in=PunchIn(scale=1.2, ease=0.2),
        ),
        _p(
            "fireworks_showcase",
            "Night tests: slow motion around the strongest burst and a short freeze at its peak, "
            "gentle punch-ins. No bloom: the bursts already clip.",
            selection=Selection(intensity_ceiling=2, emphasis_per_minute=3.0, min_gap=6.0),
            punch_in=PunchIn(scale=1.1),
            retime=Retime(apply="auto", slow_rate=0.5, pre_roll=0.4, post_roll=1.2, freeze=0.6),
        ),
        _p(
            "cinematic",
            "Controlled look: subtle vignette, fine grain, restrained glow, motion blur on fast "
            "camera moves.",
            selection=Selection(intensity_ceiling=3),
            vignette=Vignette(apply="auto", strength=0.22),
            grain=Grain(apply="auto", amount=0.012),
            glow=Glow(apply="auto", strength=0.2, threshold=0.88),
            motion_blur=MotionBlur(apply="auto"),
        ),
        _p(
            "vintage",
            "Film texture: grain, vignette, light flicker and scratches.",
            selection=Selection(intensity_ceiling=3),
            vignette=Vignette(apply="auto", strength=0.35, min_luma=0.08),
            grain=Grain(apply="auto", amount=0.035, max_noise=0.05),
            film_damage=FilmDamage(apply="auto"),
        ),
        _p(
            "privacy_protection",
            "Conceal bystanders' faces (tracked); nothing decorative.",
            selection=Selection(intensity_ceiling=0),
            punch_in=PunchIn(apply="never"),
            privacy=Privacy(apply="auto", faces=True, vehicles=True),
        ),
        _p(
            "creative_effects",
            "Expressive treatments, only where a request asks for them.",
            selection=Selection(intensity_ceiling=4),
            glow=Glow(apply="request"),
            light_rays=LightRays(apply="request"),
            chromatic=Chromatic(apply="request"),
            lens=Lens(apply="request"),
            stylize=Stylize(apply="request"),
            film_damage=FilmDamage(apply="request"),
            retime=Retime(apply="request"),
        ),
        _p(
            "shorts_energetic",
            "9:16 Shorts: stronger punch-ins, slow motion and freeze on the best burst.",
            selection=Selection(intensity_ceiling=4, emphasis_per_minute=12.0, min_gap=2.0),
            punch_in=PunchIn(apply="auto", scale=1.2),
            retime=Retime(apply="auto", slow_rate=0.4, pre_roll=0.3, post_roll=0.8, freeze=0.4),
        ),
    )
}
DEFAULT_PROFILE: Final = "clean_enhancement"

#: Story formats and visual-style format profiles -> effect profile (no-speech path included).
FORMAT_PROFILE: Final[dict[str, str]] = {
    "talking_head": "talking_head",
    "interview": "talking_head",
    "podcast": "talking_head",
    "review": "product_detail",
    "unboxing": "product_detail",
    "product_introduction": "product_detail",
    "brochure_review": "clean_enhancement",
    "tutorial": "demonstration",
    "short": "shorts_energetic",
    "shorts_haul": "shorts_energetic",
    "shorts_product_test": "fireworks_showcase",
    "nye_uncut": "clean_enhancement",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionOverride:
    section_id: str
    overrides: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectDecision:
    """A person's verdict on one effect (``effect_key``: ``clip_id:effect_type``)."""

    effect_key: str
    decision: Decision
    note: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class VfxConfig:
    #: A built-in profile name; ``None`` = by the shots' format, else the default.
    profile: str | None = None
    #: A profile built in code wins over ``profile``.
    custom: VfxProfile | None = None
    #: Project overrides by dotted path (``{"punch_in.scale": 1.15}``).
    overrides: Mapping[str, JsonValue] = field(default_factory=dict)
    section_overrides: tuple[SectionOverride, ...] = ()
    decisions: tuple[EffectDecision, ...] = ()

    def resolve(self, format_hint: str | None = None) -> VfxProfile:
        """The profile with the project overrides applied. Raises ``InvalidVfxConfig``."""
        if self.custom is not None:
            base = self.custom
        else:
            name = self.profile or FORMAT_PROFILE.get(format_hint or "", DEFAULT_PROFILE)
            if name not in PROFILES:
                raise InvalidVfxConfig(
                    f"unknown profile {name!r}; choose one of {', '.join(sorted(PROFILES))}"
                )
            base = PROFILES[name]
        profile = apply_overrides(base, self.overrides)
        problems = profile.problems()
        if problems:
            raise InvalidVfxConfig("; ".join(problems))
        return profile

    def for_section(self, profile: VfxProfile, section_id: str | None) -> VfxProfile:
        for section in self.section_overrides:
            if section.section_id == section_id:
                found = apply_overrides(profile, section.overrides)
                problems = found.problems()
                if problems:
                    raise InvalidVfxConfig(f"section {section_id}: " + "; ".join(problems))
                return found
        return profile

    def fingerprint(self, profile: VfxProfile) -> dict[str, object]:
        return {
            "profile": asdict(profile),
            "sections": sorted(
                [s.section_id, sorted((k, repr(v)) for k, v in s.overrides.items())]
                for s in self.section_overrides
            ),
            "decisions": sorted((d.effect_key, d.decision.value, d.note) for d in self.decisions),
        }


def apply_overrides(profile: VfxProfile, overrides: Mapping[str, JsonValue]) -> VfxProfile:
    for path, value in sorted(overrides.items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidVfxConfig(f"unknown setting {path!r}")
    if head in {"name", "version"}:
        raise InvalidVfxConfig(f"{head!r} cannot be overridden")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    return replace(instance, **{head: new})


def _coerce(current: object, value: JsonValue, path: str) -> object:
    """``value`` as the type of the setting it replaces (int is accepted for float settings)."""
    if is_dataclass(current):
        raise InvalidVfxConfig(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidVfxConfig(f"{path}: expected {type(current).__name__}, got {value!r}")
    return float(value) if isinstance(current, float) else value  # type: ignore[arg-type]
