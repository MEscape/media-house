"""Does a statement assert what it names, or negate, doubt or imagine it?

"The phone survived the drop" and "the phone would never survive the drop" name the same thing and
say opposite things. A consumer that puts footage under the second sentence without knowing it
shows a viewer something the narration denies. ``stance_of`` reports the cues it finds so the
consumer can refuse to treat footage as proof (or warn), without understanding the sentence.

Cues are words, not parsing: ``negated`` for denial, ``hypothetical`` for supposition, doubt or
reported claims. English and German. A statement without a cue is NOT thereby proven to assert:
``asserts`` only says no cue was found.
"""

import re
from dataclasses import dataclass
from typing import Final

_NEGATION: Final = re.compile(
    r"\b(?:not|never|no|nothing|nobody|nowhere|without|instead\s+of|unlike|cannot|"
    r"nicht|nie|niemals|kein(?:e|en|er|em)?|nichts|ohne|statt|anstatt)\b|\w+n't\b",
    re.IGNORECASE,
)
_HYPOTHETICAL: Final = re.compile(
    r"\b(?:if|imagine|suppose|supposedly|allegedly|apparently|would|could|might|may|"
    r"claims?\s+that|claimed|rumou?red|wenn|falls|stell\s+dir\s+vor|angeblich|"
    r"würde|würden|könnte|könnten|vielleicht)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Stance:
    negated: bool
    hypothetical: bool
    #: The cue words found, in reading order.
    cues: tuple[str, ...]

    @property
    def asserts(self) -> bool:
        """No negation or supposition cue was found (not a proof that it asserts)."""
        return not (self.negated or self.hypothetical)


def stance_of(text: str) -> Stance:
    negation = [m.group(0).casefold() for m in _NEGATION.finditer(text)]
    supposition = [m.group(0).casefold() for m in _HYPOTHETICAL.finditer(text)]
    cues = sorted(
        {*negation, *supposition},
        key=lambda c: text.casefold().find(c),
    )
    return Stance(negated=bool(negation), hypothetical=bool(supposition), cues=tuple(cues))
