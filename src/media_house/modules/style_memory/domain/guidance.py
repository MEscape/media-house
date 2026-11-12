"""What a generation step is told about the creator's style, and how it reads as prompt text."""

import hashlib
from dataclasses import dataclass

from media_house.modules.style_memory.domain.model import Exemplar, StyleProfile, StyleRule
from media_house.modules.style_memory.domain.values import Origin, Strength

_LABEL = {
    Strength.MANDATORY: "MUSS",
    Strength.DEFAULT: "Standard",
    Strength.SUGGESTION: "Vorschlag",
}


@dataclass(frozen=True, slots=True)
class Guidance:
    profile: StyleProfile
    rules: tuple[StyleRule, ...]
    exemplars: tuple[Exemplar, ...]
    format: str
    #: False when the caller asked for a comparison without anything that was learned.
    learned_included: bool

    @property
    def applied(self) -> tuple[str, ...]:
        """The ids of the rules and exemplars this guidance used (shown as "Angewendet: n")."""
        return (*(r.rule_id for r in self.rules), *(e.exemplar_id for e in self.exemplars))

    @property
    def learned_rule_count(self) -> int:
        return sum(1 for r in self.rules if r.origin is Origin.LEARNED)

    @property
    def fingerprint(self) -> str:
        """Same style inputs, same fingerprint (stored with what was generated from them)."""
        basis = "|".join(
            [
                str(self.profile.version),
                self.format,
                *(f"{r.rule_id}@{r.updated_at.isoformat()}" for r in self.rules),
                *(e.exemplar_id for e in self.exemplars),
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]

    def render(self) -> str:
        """The style as a German prompt section: rules by strength, then the format, then
        examples. A binding rule is marked so the model cannot read it as taste."""
        profile = self.profile
        lines = [
            f"Kanal: {profile.channel}. Sprache: Deutsch. {profile.address}",
            f"Zielgruppe: {profile.audience}",
            f"Begrüßung: „{profile.greeting}“. Catchphrase: "
            + "; ".join(f"„{c}“" for c in profile.catchphrases),
            "Nie: " + "; ".join(profile.never),
        ]
        fmt = profile.format(self.format)
        if fmt is not None:
            lines.append(f"Format {fmt.title}: " + " -> ".join(fmt.structure))
        norm = profile.pacing_of(self.format)
        if norm is not None:
            lines.append(
                f"Tempo: {norm.cuts_per_minute_min:g}-{norm.cuts_per_minute_max:g} Schnitte/min, "
                f"höchstens {norm.overlays_per_minute_max:g} Overlays/min."
            )
        if self.rules:
            lines.append("Regeln:")
            lines.extend(f"- [{_LABEL[r.strength]}] {r.text}" for r in self.rules)
        for exemplar in self.exemplars:
            lines.append(f"Beispiel „{exemplar.title}“ (Warum es gut ist):")
            lines.extend(f"- „{a.quote}“: {a.why}" for a in exemplar.annotations)
        return "\n".join(lines)
