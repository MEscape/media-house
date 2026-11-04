"""User-facing knobs describe themselves with ``dataclasses.field`` metadata (Layer 4 convention).

Keys: ``label`` (required for a visible knob), ``help``, ``unit``, ``min``, ``max``, ``step``,
``choices``, ``kind`` and ``advanced``. The range checks of the knobs added by this module are
DERIVED from that metadata (``range_problems``), so the description a settings UI shows and the
check that refuses a value cannot drift apart. The metadata never enters a cache key
(``dataclasses.asdict`` ignores it).
"""

import math
from dataclasses import fields
from typing import Any

from media_house.shared.errors import InvariantViolation


def number(
    label: str,
    low: float,
    high: float,
    step: float,
    unit: str,
    help_text: str = "",
    *,
    advanced: bool = False,
) -> dict[str, Any]:
    return {
        "label": label,
        "help": help_text,
        "unit": unit,
        "min": low,
        "max": high,
        "step": step,
        "advanced": advanced,
    }


def toggle(label: str, help_text: str = "") -> dict[str, Any]:
    return {"label": label, "help": help_text}


def colour(label: str, help_text: str = "") -> dict[str, Any]:
    return {"label": label, "help": help_text, "kind": "color"}


def range_problems(instance: Any) -> list[str]:
    """Every value of ``instance`` outside the ``min``/``max``/``choices`` its metadata declares."""
    problems: list[str] = []
    for item in fields(instance):
        value = getattr(instance, item.name)
        meta = item.metadata
        numeric = isinstance(value, int | float) and not isinstance(value, bool)
        if (
            "min" in meta
            and numeric
            and (not math.isfinite(value) or not meta["min"] <= value <= meta["max"])
        ):
            problems.append(f"{item.name} must be between {meta['min']:g} and {meta['max']:g}")
        if "choices" in meta and value not in meta["choices"]:
            problems.append(f"{item.name} must be one of {', '.join(map(str, meta['choices']))}")
    return problems


def require_valid(instance: Any) -> None:
    """Raise ``InvariantViolation`` when ``instance.problems()`` reports anything."""
    problems: list[str] = instance.problems()
    if problems:
        name = type(instance).__name__
        raise InvariantViolation(
            f"{name}: {'; '.join(problems)}",
            user_message=f"Invalid {name.lower()} settings: {'; '.join(problems)}.",
        )
