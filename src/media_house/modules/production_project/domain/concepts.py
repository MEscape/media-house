"""Story concepts: three distinct ideas for one brief, each tied to evidence from the creator.

A concept that cites nothing the creator really did is generic and is refused. The checks here
are what a person would check by hand: evidence, retention mechanics, titles, distinctness.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.numbers import fold

MAX_TITLE = 100
_STOP = frozenset(
    {"der", "die", "das", "und", "oder", "ein", "eine", "mit", "von", "zu", "im", "in"}
    | {"am", "an", "auf", "für", "ist", "sind", "wie"}
)


@dataclass(frozen=True, slots=True)
class StoryConcept:
    concept_id: str
    logline: str
    hook: str
    red_thread: str
    #: Two or three retention mechanics (counter, ladder, curiosity gap ...), each in a sentence.
    retention: tuple[str, ...]
    callback: str
    #: Why it is specific to this creator, in a sentence.
    why_you: str
    #: Ids of the evidence ``why_you`` rests on (``video:...``, ``rule:...``, ``ritual:...``).
    evidence: tuple[str, ...]
    titles: tuple[str, ...]


def problems(concept: StoryConcept, known_evidence: Collection[str]) -> tuple[str, ...]:
    """What is wrong with a concept, in German ("" list = fine)."""
    found: list[str] = []
    for label, value in (
        ("Logline", concept.logline),
        ("Hook", concept.hook),
        ("Roter Faden", concept.red_thread),
        ("Callback", concept.callback),
        ("Warum du", concept.why_you),
    ):
        if not value.strip():
            found.append(f"{label} fehlt.")
    if not 2 <= len(concept.retention) <= 3:
        found.append("Es braucht zwei oder drei Mechaniken, die Zuschauer halten.")
    if len(concept.titles) < 3:
        found.append("Es braucht drei Titel.")
    found.extend(
        f"Der Titel ist länger als {MAX_TITLE} Zeichen: {t[:30]}…"
        for t in concept.titles
        if len(t) > MAX_TITLE
    )
    if not concept.evidence:
        found.append("Kein Beleg aus deinem Kanal: das Konzept ist zu allgemein.")
    else:
        unknown = [e for e in concept.evidence if e not in known_evidence]
        if unknown:
            found.append(f"Unbekannter Beleg: {', '.join(unknown)}.")
    return tuple(found)


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in fold(text).split() if w not in _STOP and len(w) > 2)


def distinct(concepts: Sequence[StoryConcept], threshold: float = 0.6) -> bool:
    """No two concepts say nearly the same (their logline and hook words overlap too much)."""
    bags = [_tokens(f"{c.logline} {c.hook}") for c in concepts]
    for i, a in enumerate(bags):
        for b in bags[i + 1 :]:
            if a and b and 2 * len(a & b) / (len(a) + len(b)) >= threshold:
                return False
    return True


def mix(parts: Sequence[tuple[StoryConcept, str]], concept_id: str) -> StoryConcept:
    """A concept built from pieces of others: ``(source concept, field)`` for hook, red_thread,
    callback, titles ... The evidence of every used source is kept."""
    chosen: dict[str, object] = {}
    evidence: list[str] = []
    for source, name in parts:
        chosen[name] = getattr(source, name)
        evidence.extend(source.evidence)
    base = parts[0][0]
    return StoryConcept(
        concept_id=concept_id,
        logline=str(chosen.get("logline", base.logline)),
        hook=str(chosen.get("hook", base.hook)),
        red_thread=str(chosen.get("red_thread", base.red_thread)),
        retention=tuple(chosen.get("retention", base.retention)),  # type: ignore[arg-type]
        callback=str(chosen.get("callback", base.callback)),
        why_you=str(chosen.get("why_you", base.why_you)),
        evidence=tuple(dict.fromkeys(evidence)),
        titles=tuple(chosen.get("titles", base.titles)),  # type: ignore[arg-type]
    )
