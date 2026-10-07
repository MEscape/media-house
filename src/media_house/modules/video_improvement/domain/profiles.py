"""Processing profiles and the resolution ``default -> profile -> user overrides``.

A profile only states how it DIFFERS from the base, so all profiles stay consistent with each
other. Callers (a future settings UI, calibration) override single values by dotted path:
``{"color.saturation_max": 0.38, "denoise.enabled": False}``; every override is validated by the
setting it lands in. Source-specific values (the colour encoding of the camera) are overridden
the same way through the ``source.`` prefix, e.g. ``{"source.input_color.transfer": "slog3"}``.
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from typing import Any

from media_house.modules.video_improvement.domain.errors import InvalidProfile
from media_house.modules.video_improvement.domain.settings import (
    ColorSettings,
    DenoiseSettings,
    ProcessingProfile,
    SharpenSettings,
)
from media_house.modules.video_improvement.domain.source import SourceProfile
from media_house.modules.video_improvement.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation

DEFAULT_PROCESSING_PROFILE = "natural"
CUSTOM_PROFILE = "custom"

_BASE = ProcessingProfile()

_BUILTIN: dict[str, ProcessingProfile] = {
    # Minimal intervention: correct clear errors, leave everything else as shot.
    "natural": _BASE,
    # Controlled studio footage: accurate neutral colour, clean signal, almost no processing.
    "studio": replace(
        _BASE,
        name="studio",
        color=ColorSettings(
            white_balance_strength=0.8,
            white_balance_deadband=0.02,
            rolloff_absorb=1.5,
            min_gamma=0.78,
            saturation_min=0.14,
            saturation_max=0.45,
        ),
        denoise=DenoiseSettings(trigger_sigma=0.009),
        sharpen=SharpenSettings(soft_below=0.06),
    ),
    # Harsh light, action-camera colour: strong highlight roll-off, tamed saturation.
    "outdoor": replace(
        _BASE,
        name="outdoor",
        color=ColorSettings(
            max_exposure_stops=2.0,
            rolloff_absorb=2.0,
            min_gamma=0.70,
            saturation_strength=0.85,
            max_saturation_change=0.35,
            highlight_knee=0.72,
            rolloff_trigger=0.80,
            max_white_balance_shift=0.06,
            saturation_min=0.18,
            saturation_max=0.48,
        ),
        denoise=DenoiseSettings(trigger_sigma=0.005),
    ),
    # Short-form, phone-screen viewing: livelier colour and contrast.
    "social": replace(
        _BASE,
        name="social",
        color=ColorSettings(
            contrast=1.06,
            rolloff_absorb=1.5,
            min_gamma=0.72,
            saturation_min=0.25,
            saturation_max=0.58,
            saturation_strength=0.8,
        ),
        sharpen=SharpenSettings(soft_below=0.10, max_amount=0.9),
    ),
}


def processing_profile_names() -> tuple[str, ...]:
    return (*sorted(_BUILTIN), CUSTOM_PROFILE)


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    """The source and processing profile with every override applied."""

    source: SourceProfile
    processing: ProcessingProfile


def resolve_processing_profile(name: str) -> ProcessingProfile:
    if name == CUSTOM_PROFILE:
        return replace(_BASE, name=CUSTOM_PROFILE)
    try:
        return _BUILTIN[name]
    except KeyError:
        raise InvalidProfile(
            f"unknown processing profile {name!r}; choose one of "
            f"{', '.join(processing_profile_names())}"
        ) from None


def apply_overrides(
    source: SourceProfile,
    processing: ProcessingProfile,
    overrides: Mapping[str, JsonValue] | None,
) -> ResolvedConfiguration:
    """``source`` and ``processing`` with dotted-path ``overrides`` applied (validated)."""
    config = ResolvedConfiguration(source, processing)
    for path, value in sorted((overrides or {}).items()):
        parts = path.split(".")
        if parts[0] == "source":
            config = replace(config, source=_override(config.source, parts[1:], value, path))
            continue
        if parts[0] == "processing":
            parts = parts[1:]
        config = replace(config, processing=_override(config.processing, parts, value, path))
    return config


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    if not parts:
        raise InvalidProfile(f"incomplete setting {path!r}")
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidProfile(f"unknown setting {path!r}")
    if head in {"name", "version"}:
        raise InvalidProfile(f"{path}: profile names and versions cannot be overridden")
    current = getattr(target, head)
    new = _override(current, rest, value, path) if rest else _coerce(current, value, path)
    instance: Any = target
    try:
        return replace(instance, **{head: new})
    except InvariantViolation as exc:
        raise InvalidProfile(f"{path}: {exc}") from exc


def _coerce(current: object, value: JsonValue, path: str) -> object:
    """``value`` as the type of the setting it replaces (int is accepted for float settings)."""
    if is_dataclass(current):
        raise InvalidProfile(f"{path!r} is a group of settings, not a single value")
    if isinstance(current, Enum):
        try:
            return type(current)(value)
        except ValueError:
            raise InvalidProfile(f"{path}: {value!r} is not a valid choice") from None
    if isinstance(current, bool):
        ok = isinstance(value, bool)
    elif isinstance(current, float):
        ok = isinstance(value, int | float) and not isinstance(value, bool)
    elif isinstance(current, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(current))
    if not ok:
        raise InvalidProfile(f"{path}: expected {type(current).__name__}, got {value!r}")
    return float(value) if isinstance(current, float) else value  # type: ignore[arg-type]
