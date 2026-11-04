"""Selection: which heard words are burned in, by the mode of their section.

* ``full_captions``: every reliable word.
* ``selective_captions``: whole sentences worth showing (facts, content key items, story
  beats, warnings, strong claims, delivery), strongest first, until ``selective_share`` of the
  speech time is captioned.
* ``emphasis_only``: only strong phrases (a price, a rank, a spec, a warning) on their own.
* ``key_information``: spoken facts (price, rank, spec, product, retailer) with
  ``key_context_words`` before them; a fact a planned graphic already shows at that time is
  not repeated.
* ``disabled``: nothing.

Captions are never generated merely because speech exists: every shown run names its reasons.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.caption_engine.domain.cues import KEY_KINDS, WordCues
from media_house.modules.caption_engine.domain.facts import CaptionInputs, CoveredSpan
from media_house.modules.caption_engine.domain.hearing import HeardWord
from media_house.modules.caption_engine.domain.profile import CaptionProfile
from media_house.modules.caption_engine.domain.text import bare
from media_house.modules.caption_engine.domain.values import (
    LEVEL_RANK,
    CaptionMode,
    Cue,
    EmphasisLevel,
    FactKind,
)

_FACT_CUES = frozenset({Cue.PRICE, Cue.RANK, Cue.SPEC, Cue.PRODUCT, Cue.RETAILER})
_CUE_FACT = {
    Cue.PRICE: FactKind.PRICE,
    Cue.RANK: FactKind.RANK,
    Cue.SPEC: FactKind.SPEC,
    Cue.PRODUCT: FactKind.PRODUCT,
    Cue.RETAILER: FactKind.RETAILER,
}
_STRONG = frozenset({Cue.PRICE, Cue.RANK, Cue.SPEC, Cue.WARNING})


@dataclass(frozen=True, slots=True, kw_only=True)
class Run:
    words: tuple[HeardWord, ...]
    mode: CaptionMode
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Suppressed:
    words: tuple[HeardWord, ...]
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Selected:
    runs: tuple[Run, ...]
    suppressed: tuple[Suppressed, ...]


def sentences(words: Sequence[HeardWord]) -> list[list[HeardWord]]:
    """Consecutive words of one clip and one sentence (a cut always ends a unit)."""
    out: list[list[HeardWord]] = []
    for w in words:
        last = out[-1][-1] if out else None
        if (
            last is not None
            and last.clip.clip_id == w.clip.clip_id
            and last.word.sentence_id == w.word.sentence_id
            and last.word.index < w.word.index
        ):
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _reasons(words: Sequence[HeardWord], cues: dict[int, WordCues]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(c.value for w in words for c in cues[w.key].cues)) or ("speech",)


def sentence_score(
    words: Sequence[HeardWord],
    cues: dict[int, WordCues],
    key_kinds: tuple[str, ...],
    profile: CaptionProfile,
) -> float:
    score = max((cues[w.key].score for w in words), default=0.0)
    if KEY_KINDS & set(key_kinds):
        score = max(score, 0.7)
    if any(w.clip.beat_function in profile.selection.beat_functions for w in words):
        score = max(score, 0.6)
    return min(1.0, score)


def _phrases(
    unit: Sequence[HeardWord], picks: set[int], cues: dict[int, WordCues], before: int
) -> list[list[HeardWord]]:
    """Picked atoms of a sentence, each with ``before`` context words, merged when touching."""
    atoms = {cues[unit[i].key].atom for i in picks}
    keep: set[int] = set()
    for i, w in enumerate(unit):
        if cues[w.key].atom in atoms:
            keep.add(i)
            added, j = 0, i - 1
            while added < before and j >= 0:
                if bare(unit[j].text):
                    keep.add(j)
                    added += 1
                j -= 1
    out: list[list[HeardWord]] = []
    for i in sorted(keep):
        if out and out[-1][-1] is unit[i - 1]:
            out[-1].append(unit[i])
        else:
            out.append([unit[i]])
    return out


def select(
    words: Sequence[HeardWord],
    cues: dict[int, WordCues],
    inputs: CaptionInputs,
    profile_for: Callable[[str | None], CaptionProfile],
) -> Selected:
    kinds = {
        (s.asset_id, s.sentence_id): s.key_kinds for t in inputs.transcripts for s in t.sentences
    }
    runs: list[Run] = []
    suppressed: list[Suppressed] = []
    selective: list[tuple[float, int, list[HeardWord]]] = []
    speech = sum((w.end - w.start for w in words), Fraction(0))
    for order, unit in enumerate(sentences(words)):
        profile = profile_for(unit[0].clip.section_id)
        mode = profile.mode
        if mode is CaptionMode.DISABLED:
            continue
        if mode is CaptionMode.FULL_CAPTIONS:
            prev = runs[-1] if runs else None
            if (
                prev is not None
                and prev.mode is CaptionMode.FULL_CAPTIONS
                and prev.words[-1].clip.clip_id == unit[0].clip.clip_id
                and prev.words[-1].word.index < unit[0].word.index
            ):
                runs[-1] = Run(words=(*prev.words, *unit), mode=mode, reasons=("full",))
            else:
                runs.append(Run(words=tuple(unit), mode=mode, reasons=("full",)))
            continue
        if mode is CaptionMode.SELECTIVE_CAPTIONS:
            key = (unit[0].word.asset_id, unit[0].word.sentence_id)
            score = sentence_score(unit, cues, kinds.get(key, ()), profile)
            if score >= profile.selection.selective_min_score:
                selective.append((score, order, unit))
            continue
        if mode is CaptionMode.EMPHASIS_ONLY:
            picks = {
                i
                for i, w in enumerate(unit)
                if _STRONG & set(cues[w.key].cues)
                or LEVEL_RANK[cues[w.key].level] >= LEVEL_RANK[EmphasisLevel.HIGH]
            }
            for phrase in _phrases(unit, picks, cues, 0):
                runs.append(Run(words=tuple(phrase), mode=mode, reasons=_reasons(phrase, cues)))
            continue
        picks = {i for i, w in enumerate(unit) if _FACT_CUES & set(cues[w.key].cues)}
        for phrase in _phrases(unit, picks, cues, profile.selection.key_context_words):
            hit = _covered(phrase, cues, inputs.covered)
            if hit is not None:
                suppressed.append(
                    Suppressed(
                        words=tuple(phrase),
                        reason=f"a planned {hit.kind.value} graphic already shows {hit.text!r}",
                    )
                )
                continue
            runs.append(Run(words=tuple(phrase), mode=mode, reasons=_reasons(phrase, cues)))
    if selective:
        share = profile_for(None).selection.selective_share
        budget = speech * Fraction(share).limit_denominator(100)
        used = Fraction(0)
        chosen: list[tuple[int, list[HeardWord], float]] = []
        for score, order, unit in sorted(selective, key=lambda s: (-s[0], s[1])):
            length = unit[-1].end - unit[0].start
            if chosen and used + length > budget:
                continue
            used += length
            chosen.append((order, unit, score))
        for _, unit, _ in sorted(chosen, key=lambda c: c[0]):
            runs.append(
                Run(
                    words=tuple(unit),
                    mode=CaptionMode.SELECTIVE_CAPTIONS,
                    reasons=_reasons(unit, cues),
                )
            )
    runs.sort(key=lambda r: (r.words[0].start, r.words[0].key))
    return Selected(runs=tuple(runs), suppressed=tuple(suppressed))


def _covered(
    phrase: Sequence[HeardWord], cues: dict[int, WordCues], covered: Sequence[CoveredSpan]
) -> CoveredSpan | None:
    kinds = {_CUE_FACT[c] for w in phrase for c in cues[w.key].cues if c in _CUE_FACT}
    start, end = phrase[0].start, phrase[-1].end
    for span in covered:
        if span.kind in kinds and span.timeline_start < end and start < span.timeline_end:
            return span
    return None
