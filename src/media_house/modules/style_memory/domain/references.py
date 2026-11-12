"""Another creator's style, translated into the channel's allowed vocabulary.

A reference card holds what was measured (pacing) and what the reference does in its own words.
Nothing is copied: every technique is looked up in a small vocabulary and checked against the
profile. A technique the profile forbids is shown as a conflict with the closest allowed
translation. Nothing is silently picked.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.style_memory.domain.model import (
    PacingNorm,
    ReferenceCard,
    StyleProfile,
)


class Verdict(StrEnum):
    ALLOWED = "allowed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Technique:
    kind: str
    aliases: tuple[str, ...]
    #: What the channel can do instead (its allowed vocabulary), most similar first.
    instead: tuple[str, ...]


#: What the channel can do to get the same effect without the forbidden device.
_INSTEAD_TRANSITION = ("Tempo", "Zoom auf das Detail", "Text-Pop-in", "Sound-Hit", "harter Schnitt")

VOCABULARY: tuple[Technique, ...] = (
    Technique("glitch", ("glitch", "datamosh", "rgb split", "tv glitch"), _INSTEAD_TRANSITION),
    Technique("shape", ("shape transition", "shape wipe", "formen"), _INSTEAD_TRANSITION),
    Technique("mask_reveal", ("mask reveal", "mask transition", "maske"), _INSTEAD_TRANSITION),
    Technique("wipe", ("wipe", "star wipe", "iris"), _INSTEAD_TRANSITION),
    Technique("jump_cut", ("jump cut", "jumpcut", "jump-cut"), ("jump_cut",)),
    Technique("hard_cut", ("hard cut", "harter schnitt", "straight cut"), ("hard_cut",)),
    Technique("whip_pan", ("whip pan", "whip", "swish"), ("whip_pan",)),
    Technique("zoom_tunnel", ("zoom tunnel", "radial zoom", "zoom blur"), ("zoom_tunnel",)),
    Technique("pixel_dissolve", ("pixel dissolve", "mosaic", "pixelate"), ("pixel_dissolve",)),
    Technique("flash", ("flash", "light leak"), ("flash",)),
    Technique("slide", ("slide", "carousel"), ("slide",)),
    Technique("cross_dissolve", ("cross dissolve", "crossfade", "cross fade"), ("cross_dissolve",)),
)


@dataclass(frozen=True, slots=True)
class TechniqueTranslation:
    technique: str
    kind: str
    verdict: Verdict
    explanation: str
    instead: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PacingTranslation:
    metric: str
    reference: float
    profile_range: str
    adopted: float
    verdict: Verdict
    explanation: str


@dataclass(frozen=True, slots=True)
class StyleTranslation:
    card_id: str
    techniques: tuple[TechniqueTranslation, ...]
    pacing: tuple[PacingTranslation, ...]

    @property
    def conflicts(self) -> tuple[TechniqueTranslation | PacingTranslation, ...]:
        both: tuple[TechniqueTranslation | PacingTranslation, ...] = (
            *self.techniques,
            *self.pacing,
        )
        return tuple(t for t in both if t.verdict is Verdict.CONFLICT)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def classify_technique(text: str) -> Technique | None:
    folded = _fold(text)
    for technique in VOCABULARY:
        if any(_fold(alias) in folded for alias in technique.aliases):
            return technique
    return None


def translate(
    card: ReferenceCard,
    profile: StyleProfile,
    *,
    format: str,  # noqa: A002
) -> StyleTranslation:
    """Check a reference against the profile: techniques first, then pacing."""
    forbidden = {_fold(k) for k in profile.forbidden_transitions}
    allowed = {_fold(k) for k in profile.allowed_transitions}
    techniques: list[TechniqueTranslation] = []
    for raw in card.techniques:
        known = classify_technique(raw)
        if known is None:
            techniques.append(
                TechniqueTranslation(
                    raw,
                    "unknown",
                    Verdict.UNKNOWN,
                    "Unbekannte Technik: bitte kurz beschreiben, was sie bewirkt.",
                    (),
                )
            )
        elif _fold(known.kind) in forbidden:
            techniques.append(
                TechniqueTranslation(
                    raw,
                    known.kind,
                    Verdict.CONFLICT,
                    f"Dein Profil verbietet '{known.kind}'. "
                    "Der Effekt lässt sich anders erreichen.",
                    known.instead,
                )
            )
        else:
            fits = _fold(known.kind) in allowed
            techniques.append(
                TechniqueTranslation(
                    raw,
                    known.kind,
                    Verdict.ALLOWED,
                    "Passt zu deinem Profil."
                    if fits
                    else "Nicht verboten, aber bisher nicht Teil deiner Videos.",
                    (known.kind,),
                )
            )
    return StyleTranslation(card.card_id, tuple(techniques), _pacing(card, profile, format))


def _pacing(
    card: ReferenceCard,
    profile: StyleProfile,
    format_name: str,
) -> tuple[PacingTranslation, ...]:
    norm = profile.pacing_of(format_name)
    if norm is None:
        return ()
    return (
        _bounded(
            "Schnitte pro Minute",
            card.stats.cuts_per_minute,
            norm.cuts_per_minute_min,
            norm.cuts_per_minute_max,
        ),
        _bounded(
            "Overlays pro Minute", card.stats.overlays_per_minute, 0.0, norm.overlays_per_minute_max
        ),
    )


def _bounded(metric: str, value: float, low: float, high: float) -> PacingTranslation:
    adopted = min(max(value, low), high)
    inside = adopted == value
    return PacingTranslation(
        metric,
        value,
        f"{low:g} bis {high:g}",
        adopted,
        Verdict.ALLOWED if inside else Verdict.CONFLICT,
        "Liegt in deinem Rahmen."
        if inside
        else f"Liegt außerhalb deines Rahmens; ich übernehme {adopted:g} statt {value:g}.",
    )


def norm_summary(norm: PacingNorm) -> str:
    return (
        f"{norm.cuts_per_minute_min:g}-{norm.cuts_per_minute_max:g} Schnitte/min, "
        f"höchstens {norm.overlays_per_minute_max:g} Overlays/min, "
        f"{norm.words_per_minute:g} Wörter/min"
    )
