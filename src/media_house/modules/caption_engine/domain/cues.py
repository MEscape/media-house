"""Why each heard word matters: semantic, acoustic and narrative evidence combined.

Facts (price, rank, spec, retailer, product) come from content_intelligence; key items
(claims, conclusions, caveats) from its sentences; delivery from audio_intelligence; beats from
the story (through the clip). The loudest word is never important by loudness alone: delivery
only adds ``vocal_weight`` to a word that already carries meaning, and alone it reaches at
most ``medium`` on a content word.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.caption_engine.domain.facts import CaptionInputs, SpokenFact
from media_house.modules.caption_engine.domain.hearing import HeardWord
from media_house.modules.caption_engine.domain.profile import CaptionProfile
from media_house.modules.caption_engine.domain.text import STOPWORDS, bare, has_digit, is_unit
from media_house.modules.caption_engine.domain.values import Cue, EmphasisLevel, FactKind

_FACT_CUE = {
    FactKind.PRICE: (Cue.PRICE, EmphasisLevel.CRITICAL, 1.0),
    FactKind.RANK: (Cue.RANK, EmphasisLevel.CRITICAL, 0.95),
    FactKind.SPEC: (Cue.SPEC, EmphasisLevel.HIGH, 0.8),
    FactKind.PRODUCT: (Cue.PRODUCT, EmphasisLevel.MEDIUM, 0.6),
    FactKind.RETAILER: (Cue.RETAILER, EmphasisLevel.MEDIUM, 0.55),
    FactKind.NUMBER: (Cue.NUMBER, EmphasisLevel.MEDIUM, 0.5),
}
_LEXICON_CUE = {
    Cue.WARNING: (EmphasisLevel.HIGH, 0.75),
    Cue.STRONG_CLAIM: (EmphasisLevel.MEDIUM, 0.5),
    Cue.CONTRAST: (EmphasisLevel.MEDIUM, 0.4),
}
#: Content key-item kinds that make a sentence worth showing.
KEY_KINDS = frozenset({"claim", "conclusion", "quotable", "recommendation", "caveat", "fact"})


@dataclass(frozen=True, slots=True, kw_only=True)
class WordCues:
    cues: tuple[Cue, ...]
    level: EmphasisLevel
    score: float
    #: Words with the same atom are never split (a fact, a number with its unit).
    atom: int
    fact: SpokenFact | None = None


def _facts_by_word(inputs: CaptionInputs) -> dict[tuple[str, int], SpokenFact]:
    out: dict[tuple[str, int], SpokenFact] = {}
    for transcript in inputs.transcripts:
        for fact in transcript.facts:
            for index in range(fact.first, fact.last + 1):
                out.setdefault((fact.asset_id, index), fact)
    return out


def _key_kinds(inputs: CaptionInputs) -> dict[tuple[str, str], tuple[str, ...]]:
    return {
        (s.asset_id, s.sentence_id): s.key_kinds for t in inputs.transcripts for s in t.sentences
    }


def analyse(
    words: Sequence[HeardWord], inputs: CaptionInputs, profile: CaptionProfile
) -> dict[int, WordCues]:
    """Cues of every word (``words`` in timeline order)."""
    facts = _facts_by_word(inputs)
    kinds = _key_kinds(inputs)
    lexicon = profile.emphasis.lexicon
    beats = frozenset(profile.selection.beat_functions)
    weight = profile.emphasis.vocal_weight
    out: dict[int, WordCues] = {}
    atom_of: dict[int, int] = {}
    for i, w in enumerate(words):
        fact = facts.get((w.clip.asset_id, w.word.index))
        prev = words[i - 1] if i else None
        same_run = (
            prev is not None
            and prev.clip.clip_id == w.clip.clip_id
            and prev.word.index == w.word.index - 1
        )
        prev_fact = facts.get((prev.clip.asset_id, prev.word.index)) if prev else None
        if (same_run and prev is not None and fact is not None and fact is prev_fact) or (
            same_run and prev is not None and has_digit(prev.text) and is_unit(w.text)
        ):
            atom_of[w.key] = atom_of[prev.key]
        else:
            atom_of[w.key] = w.key
        found: list[tuple[Cue, EmphasisLevel, float]] = []
        if fact is not None:
            found.append(_FACT_CUE[fact.kind])
        elif has_digit(w.text) or (prev is not None and atom_of[w.key] == atom_of[prev.key]):
            found.append(_FACT_CUE[FactKind.NUMBER])
        stem = bare(w.text)
        for entry in lexicon:
            if stem.startswith(entry.stem):
                level, score = _LEXICON_CUE.get(entry.cue, (EmphasisLevel.MEDIUM, 0.4))
                found.append((entry.cue, level, score))
                break
        sentence_kinds = kinds.get((w.word.asset_id, w.word.sentence_id), ())
        content_word = stem not in STOPWORDS and len(stem) >= 4
        if KEY_KINDS & set(sentence_kinds) and content_word:
            found.append((Cue.KEY_ITEM, EmphasisLevel.NORMAL, 0.3))
        if w.clip.beat_function in beats and content_word:
            found.append((Cue.BEAT, EmphasisLevel.NORMAL, 0.2))
        vocal = w.word.vocal or 0.0
        if vocal >= 0.8 and content_word:
            found.append((Cue.VOCAL, EmphasisLevel.NORMAL, 0.2))
        if not found:
            out[w.key] = WordCues(
                cues=(), level=EmphasisLevel.NORMAL, score=0.0, atom=atom_of[w.key]
            )
            continue
        level = max((f[1] for f in found), key=_rank)
        semantic = max(f[2] for f in found)
        meaningful = any(f[0] not in (Cue.VOCAL,) for f in found)
        # Delivery promotes a meaningful word one step, never a word on its own.
        if meaningful and vocal >= 0.6 and level is EmphasisLevel.NORMAL and content_word:
            level = EmphasisLevel.MEDIUM
        if not meaningful and content_word and vocal >= 0.9:
            level = EmphasisLevel.MEDIUM
        out[w.key] = WordCues(
            cues=tuple(dict.fromkeys(f[0] for f in found)),
            level=level,
            score=min(1.0, semantic + weight * vocal),
            atom=atom_of[w.key],
            fact=fact,
        )
    return out


def _rank(level: EmphasisLevel) -> int:
    return list(EmphasisLevel).index(level)
