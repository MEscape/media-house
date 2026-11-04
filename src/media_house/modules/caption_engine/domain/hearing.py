"""Which transcript words are heard in the edit, and when (source -> timeline via clips only).

A word is heard when a clip whose sound is on (``audio``) covers it. A word the edit cuts out
is not heard. A word a cut SPLITS is shown only when at least ``cut_word_min_share`` of it is
inside the clip (its times are then clamped to the clip and it is marked ``clipped``);
otherwise it is recorded as ``split_by_cut``. Suspect words (a bang the recogniser may have
turned into a word) and words under the confidence floor are never burned in; a transcript in
another language than the channel's is not burned in either (subtitles keep it, labelled).
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.caption_engine.domain.facts import (
    CaptionClip,
    CaptionInputs,
    SpokenWord,
    frac,
)
from media_house.modules.caption_engine.domain.profile import Transcript
from media_house.modules.caption_engine.domain.values import WordFate

#: A word without duration still needs a visible instant (one hundredth of a second).
_MIN_WORD = Fraction(1, 100)


@dataclass(frozen=True, slots=True, kw_only=True)
class HeardWord:
    key: int
    word_id: str
    word: SpokenWord
    clip: CaptionClip
    text: str
    corrected: bool
    source_start: Fraction
    source_end: Fraction
    start: Fraction
    end: Fraction
    clipped: bool
    #: ``None`` = may be burned in; otherwise why not (subtitles may still use it).
    fate: WordFate | None


@dataclass(slots=True)
class Heard:
    words: list[HeardWord] = field(default_factory=list)
    counts: dict[str, Counter[WordFate]] = field(default_factory=dict)

    @property
    def burnable(self) -> list[HeardWord]:
        return [w for w in self.words if w.fate is None]

    @property
    def subtitle_words(self) -> list[HeardWord]:
        """Every heard word except invented ones (suspect) and those a cut removed."""
        keep = (None, WordFate.LOW_CONFIDENCE, WordFate.WRONG_LANGUAGE)
        return [w for w in self.words if w.fate in keep]


def language_matches(found: str | None, wanted: str) -> bool:
    if not found:
        return True  # unknown language: the transcript is trusted
    return found.split("-")[0].casefold() == wanted.split("-")[0].casefold()


def hear(
    inputs: CaptionInputs,
    rules: Transcript,
    corrections: Mapping[str, str],
) -> Heard:
    heard = Heard()
    min_share = Fraction(rules.cut_word_min_share).limit_denominator(1000)
    key = 0
    for clip in inputs.timeline.clips:
        if not clip.audio:
            continue
        transcript = inputs.transcript(clip.asset_id)
        if transcript is None:
            continue
        counts = heard.counts.setdefault(clip.asset_id, Counter())
        wrong_language = not language_matches(transcript.language, rules.language)
        lo, hi = frac(clip.source_start), frac(clip.source_end)
        for word in transcript.words:
            ws, we = word.start, max(word.end, word.start + _MIN_WORD)
            a, b = max(ws, lo), min(we, hi)
            if b <= a:
                continue  # cut out of the edit
            clipped = a > ws or b < we
            fate: WordFate | None = None
            if word.suspect:
                fate = WordFate.SUSPECT
            elif clipped and (b - a) / (we - ws) < min_share:
                fate = WordFate.SPLIT_BY_CUT
            elif word.confidence is not None and word.confidence < rules.min_confidence:
                fate = WordFate.LOW_CONFIDENCE
            elif wrong_language:
                fate = WordFate.WRONG_LANGUAGE
            if fate is not None:
                counts[fate] += 1
            base = f"{clip.asset_id}:{word.index}"
            word_id = f"{base}@{clip.clip_id}"
            fixed = corrections.get(word_id, corrections.get(base))
            heard.words.append(
                HeardWord(
                    key=key,
                    word_id=word_id,
                    word=word,
                    clip=clip,
                    text=fixed.strip() if fixed and fixed.strip() else word.text,
                    corrected=bool(fixed and fixed.strip()),
                    source_start=a,
                    source_end=b,
                    start=clip.to_timeline(a),
                    end=clip.to_timeline(b),
                    clipped=clipped,
                    fate=fate,
                )
            )
            key += 1
    heard.words.sort(key=lambda w: (w.start, w.key))
    return heard
