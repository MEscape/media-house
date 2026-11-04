"""Expression: which shown words are highlighted, and where a (rare) emoji is earned.

Emphasis is bounded: at most ``max_per_caption`` atoms per caption, at most ``max_share`` of
all shown words, never two highlights closer than ``min_word_gap`` words; strongest first
(price/rank > spec/warning > product/claims > delivery). A whole atom lights up ("19,99 €").

Emoji come from the profile's small controlled vocabulary only, never on a negated word
("nicht krass"), only warning emojis inside a caveat sentence, at most ``max_per_minute``,
``min_spacing`` apart, the same emoji not again within ``repeat_spacing`` and never twice in a
row. Formats where emoji would be wrong (serious, technical, sponsored tests) switch them off.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.caption_engine.domain.cues import WordCues
from media_house.modules.caption_engine.domain.hearing import HeardWord
from media_house.modules.caption_engine.domain.profile import CaptionProfile, EmojiRule
from media_house.modules.caption_engine.domain.text import NEGATIONS, STOPWORDS, bare
from media_house.modules.caption_engine.domain.values import LEVEL_RANK, EmphasisLevel, FactKind


@dataclass(frozen=True, slots=True, kw_only=True)
class GroupView:
    """A caption group as expression sees it."""

    group_id: str
    words: tuple[HeardWord, ...]
    profile: CaptionProfile
    start: Fraction
    end: Fraction
    no_emphasis: bool
    no_emoji: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class EmojiPick:
    group_id: str
    rule: EmojiRule
    word: HeardWord


def emphasise(
    groups: Sequence[GroupView], cues: Mapping[int, WordCues]
) -> dict[int, EmphasisLevel]:
    """Highlighted word keys -> level (every other shown word stays ``normal``)."""
    order = [w.key for g in groups for w in g.words]
    position = {k: i for i, k in enumerate(order)}
    out: dict[int, EmphasisLevel] = {}
    if not order:
        return out
    picked: list[int] = []
    per_group: dict[str, int] = {}
    candidates: list[tuple[float, int, GroupView, int]] = []
    for g in groups:
        rules = g.profile.emphasis
        if not rules.enabled or g.no_emphasis or rules.max_per_caption == 0:
            continue
        floor = LEVEL_RANK[EmphasisLevel(rules.shown_from)]
        seen: set[int] = set()
        for w in g.words:
            c = cues[w.key]
            if c.atom in seen or LEVEL_RANK[c.level] < floor or w.clipped:
                continue
            seen.add(c.atom)
            rank = LEVEL_RANK[c.level]
            candidates.append((-(rank + c.score), position[w.key], g, c.atom))
    budget = {g.group_id: g.profile.emphasis.max_per_caption for g in groups}
    share = max((g.profile.emphasis.max_share for g in groups), default=0.0)
    total_cap = max(1, math.floor(share * len(order))) if share > 0 else 0
    for _, _pos, g, atom in sorted(candidates):
        members = [w for w in g.words if cues[w.key].atom == atom]
        # A fact lights up without its function words ("AUF PLATZ 3" -> "PLATZ 3").
        lit = [w for w in members if bare(w.text) not in STOPWORDS] or members
        if picked and len(picked) + len(lit) > total_cap:
            continue
        if per_group.get(g.group_id, 0) >= budget[g.group_id]:
            continue
        gap = g.profile.emphasis.min_word_gap
        if any(abs(position[w.key] - p) <= gap for w in lit for p in picked):
            continue
        per_group[g.group_id] = per_group.get(g.group_id, 0) + 1
        for w in lit:
            out[w.key] = cues[w.key].level
            picked.append(position[w.key])
    return out


def _rule_for(word: HeardWord, cue: WordCues, vocabulary: Sequence[EmojiRule]) -> EmojiRule | None:
    triggers = []
    if cue.fact is not None and cue.fact.kind is FactKind.PRICE:
        triggers.append("fact:price")
    if cue.fact is not None and cue.fact.kind is FactKind.RANK and cue.fact.rank == 1:
        triggers.append("fact:rank1")
    stem = bare(word.text)
    for rule in vocabulary:
        if rule.trigger in triggers or (
            not rule.trigger.startswith("fact:") and stem.startswith(rule.trigger)
        ):
            return rule
    return None


def choose_emojis(
    groups: Sequence[GroupView],
    cues: Mapping[int, WordCues],
    caveat: Callable[[HeardWord], bool],
    duration: Fraction,
) -> list[EmojiPick]:
    picks: list[EmojiPick] = []
    last_at: Fraction | None = None
    last_seen: dict[str, Fraction] = {}
    minutes = float(duration) / 60.0
    for g in groups:
        rules = g.profile.emoji
        if not rules.enabled or g.no_emoji or rules.max_per_minute <= 0:
            continue
        allowed = max(1, math.floor(rules.max_per_minute * minutes))
        if len(picks) >= allowed:
            continue
        for i, w in enumerate(g.words):
            rule = _rule_for(w, cues[w.key], rules.vocabulary)
            if rule is None:
                continue
            before = {bare(x.text) for x in g.words[max(0, i - 2) : i]}
            if before & NEGATIONS:
                continue  # "nicht krass": an emoji would invert the meaning
            if caveat(w) and rule.meaning != "warning":
                continue
            at = w.start
            if last_at is not None and at - last_at < Fraction(rules.min_spacing):
                continue
            seen = last_seen.get(rule.emoji)
            if seen is not None and at - seen < Fraction(rules.repeat_spacing):
                continue
            if picks and picks[-1].rule.emoji == rule.emoji:
                continue
            picks.append(EmojiPick(group_id=g.group_id, rule=rule, word=w))
            last_at, last_seen[rule.emoji] = at, at
            break  # one emoji per caption at most
    return picks
