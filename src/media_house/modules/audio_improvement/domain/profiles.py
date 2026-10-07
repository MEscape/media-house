"""Built-in profiles and the resolution ``default -> profile -> user overrides``.

A profile only states how it DIFFERS from the default; everything else stays shared, so the
profiles remain consistent with each other. Users (a future settings UI) override single values
by dotted path, e.g. ``{"mastering.target_lufs": -15.0}``; each override is validated by the
settings it lands in.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from typing import Any

from media_house.modules.audio_improvement.domain.errors import InvalidProfile
from media_house.modules.audio_improvement.domain.settings import (
    AudioProfile,
    DeEsserSettings,
    DynamicsSettings,
    EqSettings,
    MasteringSettings,
)
from media_house.modules.audio_improvement.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation

DEFAULT_PROFILE = "youtube"
CUSTOM_PROFILE = "custom"

_BASE = AudioProfile()

_BUILTIN: dict[str, AudioProfile] = {
    # Loudness of the major streaming platforms; speech kept natural.
    "youtube": _BASE,
    # Short, loud, phone-speaker content: tighter dynamics, firmer sibilance control.
    "social_video": replace(
        _BASE,
        name="social_video",
        dynamics=DynamicsSettings(dynamics_trigger_db=32.0, ratio=3.0),
        de_ess=DeEsserSettings(trigger_db=-11.0),
        mastering=MasteringSettings(target_lufs=-14.0, true_peak_ceiling_dbtp=-1.0),
    ),
    # Spoken-word listening: quieter, most natural dynamics, gentle EQ.
    "podcast": replace(
        _BASE,
        name="podcast",
        eq=EqSettings(correction_ratio=0.4),
        dynamics=DynamicsSettings(dynamics_trigger_db=40.0, ratio=1.8),
        mastering=MasteringSettings(target_lufs=-16.0, true_peak_ceiling_dbtp=-1.0),
    ),
    # Wide dynamics and headroom: compression only for very spiky speech.
    "cinematic": replace(
        _BASE,
        name="cinematic",
        dynamics=DynamicsSettings(dynamics_trigger_db=46.0, ratio=1.5),
        mastering=MasteringSettings(target_lufs=-23.0, true_peak_ceiling_dbtp=-2.0),
    ),
}


def profile_names() -> tuple[str, ...]:
    return (*sorted(_BUILTIN), CUSTOM_PROFILE)


def resolve_profile(
    name: str = DEFAULT_PROFILE,
    overrides: Mapping[str, JsonValue] | None = None,
) -> AudioProfile:
    """The validated configuration for ``name`` with ``overrides`` applied.

    Raises ``InvalidProfile`` for an unknown profile, an unknown setting, a value of the wrong
    type or a value the setting rejects.
    """
    if name == CUSTOM_PROFILE:
        profile = replace(_BASE, name=CUSTOM_PROFILE)
    elif name in _BUILTIN:
        profile = _BUILTIN[name]
    else:
        raise InvalidProfile(
            f"unknown profile {name!r}; choose one of {', '.join(profile_names())}"
        )
    for path, value in sorted((overrides or {}).items()):
        profile = _override(profile, path.split("."), value, path)
    return profile


def _override(target: Any, parts: list[str], value: JsonValue, path: str) -> Any:
    head, rest = parts[0], parts[1:]
    if not is_dataclass(target) or head not in {f.name for f in fields(target)}:
        raise InvalidProfile(f"unknown setting {path!r}")
    if head == "name":
        raise InvalidProfile("the profile name cannot be overridden")
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
