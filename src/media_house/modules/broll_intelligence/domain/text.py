"""Words: the same cheap, deterministic reduction on both sides of a match.

A passage and a shot's observations are compared as bags of reduced words. The reduction (lower
case, a few plural and ending cuts, no filler words) is not linguistics: it only has to treat
"pours", "pouring" and "poured" alike on BOTH sides. Meaning beyond shared words (synonyms,
related scenes) is the embeddings' job, and the result says which of the two found a shot.
"""

import re
from collections.abc import Iterable
from typing import Final

from media_house.modules.broll_intelligence.domain.facts import ShotFact, TargetFact

_WORD = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)
_MIN = 3

_STOP_EN = (
    "the a an and or but if then than that this these those there here it its it's is are was were "
    "be been being am do does did have has had having will would can could should may might must "
    "of in on at to for from by with about into over under out up down off as so not no yes you "
    "your we our they them their he she his her i me my what which who whom whose when where why "
    "how all any both each few more most other some such only own same too very just also still "
    "get got make made let like see look going gonna want need really actually basically little "
    "much many lot lots thing things way one two three first next last now well okay ok"
)
_STOP_DE = (
    "der die das den dem des ein eine einer einem einen und oder aber wenn dann als dass ist sind "
    "war waren sein bin bist wird werden wurde kann können soll muss mit von zu zum zur auf in im "
    "an am aus bei nach vor über unter für durch gegen ohne um ich du er sie es wir ihr mein dein "
    "sein unser auch noch nur schon sehr mehr viel viele so wie was wer wo warum hier da jetzt "
    "dann dies diese dieser dieses nicht kein keine"
)
STOP: Final = frozenset((_STOP_EN + " " + _STOP_DE).split())


def stem(word: str) -> str:
    """A word reduced so related forms meet (``pouring``, ``pours`` -> ``pour``)."""
    w = word.casefold()
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 5 and w.endswith("sses"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    cut = False
    if len(w) > 5 and w.endswith("ing"):
        w, cut = w[:-3], True
    elif len(w) > 4 and w.endswith("ed"):
        w, cut = w[:-2], True
    elif len(w) > 6 and w.endswith("er"):
        w, cut = w[:-2], True  # comparatives: "brighter" -> "bright"
    if cut and len(w) > 3 and w[-1] == w[-2] and w[-1] not in "lsz":
        w = w[:-1]  # "dropped" -> "drop", "running" -> "run"
    if len(w) > 2 and w.endswith("e"):
        w = w[:-1]  # "bake" and "baking" meet at "bak"
    return w


def reduce(text: str) -> tuple[str, ...]:
    """The distinct reduced content words of ``text``, in order of first appearance."""
    seen: dict[str, None] = {}
    for raw in _WORD.findall(text):
        if raw.casefold() in STOP or len(raw) < _MIN:
            continue
        seen.setdefault(stem(raw), None)
    return tuple(seen)


def query_terms(target: TargetFact) -> tuple[tuple[str, float], ...]:
    """The words a shot should show for this passage, each with how much it matters.

    * the story's/content's verbatim ``concept`` and the names the passage carries: weight 1;
    * the rest of the passage: 0.5;
    * the neighbouring statements: 0.25, and only when the passage is thin (no concept) or points
      at something it does not name ("this process"), so a clear passage is not diluted by context.
    """
    weights: dict[str, float] = {}

    def add(words: Iterable[str], weight: float) -> None:
        for word in words:
            weights[word] = max(weights.get(word, 0.0), weight)

    add(reduce(target.concept), 1.0)
    for name in target.names:
        add((*reduce(name.family), name.value.replace(" ", "")), 1.0)
    add(reduce(target.text), 0.5 if weights else 0.8)
    unresolved = any(r.state != "resolved" for r in target.references)
    if unresolved or not target.concept:
        for sentence in target.context:
            add(reduce(sentence), 0.25)
    return tuple(sorted(weights.items()))


def shot_words(shot: ShotFact) -> dict[str, str]:
    """Each reduced word of a shot's observations -> the strongest place it was found.

    ``observed_text`` (read from the picture) > ``entity_label`` (a detected object or label) >
    ``described`` (the scene description, environment) > ``metadata`` (asset name, tags).
    """
    found: dict[str, str] = {}

    def add(words: Iterable[str], place: str) -> None:
        for word in words:
            found.setdefault(word, place)

    for text in shot.texts:
        add(reduce(text), "observed_text")
    for label in (
        *shot.objects,
        *shot.entity_labels,
        *([shot.content_label] if shot.content_label else []),
    ):
        add(reduce(label), "entity_label")
    add(reduce(" ".join(p for p in (shot.description, shot.environment) if p)), "described")
    add(reduce(" ".join((shot.asset_name, *shot.tags))), "metadata")
    for name, _ in shot.names:
        add((name.value.replace(" ", ""), *reduce(name.family)), "metadata")
    return found
