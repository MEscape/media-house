"""Grouping the analysed passages into story sections, and the beats inside them.

A section is a coarse topic of the recording (content intelligence already drew the boundaries);
nothing here re-segments text. A beat is one passage together with the function it serves in the
story, taken from the roles upstream assigned. The mapping from role to function is the only
interpretation added.
"""

import re
from collections import Counter
from dataclasses import dataclass

from media_house.modules.story_architecture.domain.facts import (
    AssetFacts,
    SegmentFacts,
    TopicFacts,
)
from media_house.modules.story_architecture.domain.model import Beat, SourceRef, Statement
from media_house.modules.story_architecture.domain.values import (
    Basis,
    BeatFunction,
    ContentMode,
    Method,
    Origin,
)

EXCERPT_LIMIT = 160
_MAX_BEATS_PER_SEGMENT = 2
_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)

F = BeatFunction

#: Content-intelligence role -> story function. Roles absent here (emphasis, humour, unclassified)
#: describe delivery, not a step in the story, and make no beat of their own.
ROLE_FUNCTION: dict[str, BeatFunction] = {
    "hook": F.HOOK,
    "introduction": F.SETUP,
    "context_setting": F.CONTEXT,
    "main_point": F.CLAIM,
    "explanation": F.EXPLANATION,
    "supporting_evidence": F.EVIDENCE,
    "example": F.EXAMPLE,
    "anecdote": F.EXAMPLE,
    "demonstration": F.DEMONSTRATION,
    "counterargument": F.COUNTERPOINT,
    "caveat": F.CAVEAT,
    "transition": F.TRANSITION,
    "recap": F.RECAP,
    "question": F.QUESTION,
    "answer": F.ANSWER,
    "turning_point": F.TURNING_POINT,
    "resolution": F.PAYOFF,
    "conclusion": F.PAYOFF,
    "call_to_action": F.CALL_TO_ACTION,
}

_MODE: dict[BeatFunction, ContentMode] = {
    F.CLAIM: ContentMode.EXPLAINING,
    F.EXPLANATION: ContentMode.EXPLAINING,
    F.ANSWER: ContentMode.EXPLAINING,
    F.DEMONSTRATION: ContentMode.DEMONSTRATING,
    F.EVIDENCE: ContentMode.EVIDENCING,
    F.COUNTERPOINT: ContentMode.EVIDENCING,
    F.EXAMPLE: ContentMode.EXEMPLIFYING,
    F.HOOK: ContentMode.FRAMING,
    F.SETUP: ContentMode.FRAMING,
    F.CONTEXT: ContentMode.FRAMING,
    F.TRANSITION: ContentMode.FRAMING,
    F.QUESTION: ContentMode.FRAMING,
    F.RECAP: ContentMode.SUMMARISING,
    F.PAYOFF: ContentMode.SUMMARISING,
    F.CALL_TO_ACTION: ContentMode.SUMMARISING,
    F.TURNING_POINT: ContentMode.EXPLAINING,
    F.CAVEAT: ContentMode.EXPLAINING,
}

_PURPOSE: dict[BeatFunction, str] = {
    F.HOOK: "Opens the story with {label}",
    F.SETUP: "Sets up {label}",
    F.CONTEXT: "Gives the context for {label}",
    F.CLAIM: "States the point about {label}",
    F.EXPLANATION: "Explains {label}",
    F.EVIDENCE: "Gives evidence on {label}",
    F.EXAMPLE: "Illustrates {label} with an example",
    F.DEMONSTRATION: "Shows {label}",
    F.COUNTERPOINT: "Weighs a counterpoint on {label}",
    F.CAVEAT: "States the limits of {label}",
    F.TRANSITION: "Moves the story on from {label}",
    F.QUESTION: "Raises the question of {label}",
    F.ANSWER: "Answers the question of {label}",
    F.TURNING_POINT: "Turns the story at {label}",
    F.PAYOFF: "Pays off the story with {label}",
    F.RECAP: "Recaps {label}",
    F.CALL_TO_ACTION: "Asks the viewer to act on {label}",
    F.UNCLASSIFIED: "Covers {label}",
}


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> str:
    """A bounded verbatim excerpt (whitespace collapsed; cut with an ellipsis)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def terms(text: str) -> frozenset[str]:
    """Lower-cased words of four letters or more: crude, language-neutral, deterministic."""
    return frozenset(w.casefold() for w in _WORD.findall(text))


def mean(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 4) if known else None


@dataclass(frozen=True, slots=True)
class Draft:
    """A section before any judgement is added: which passages, in which recording."""

    section_id: str
    asset_index: int
    asset_id: str
    source_index: int
    label: str
    segments: tuple[SegmentFacts, ...]
    summary: Statement | None

    @property
    def first(self) -> SegmentFacts:
        return self.segments[0]


def source_ref(asset_id: str, segments: tuple[SegmentFacts, ...]) -> SourceRef:
    """The passage made of ``segments`` (all of one asset), with the times upstream gave."""
    starts = [s.start for s in segments if s.start is not None]
    ends = [s.end for s in segments if s.end is not None]
    return SourceRef(
        asset_id=asset_id,
        segment_ids=tuple(s.segment_id for s in segments),
        unit_ids=tuple(u for s in segments for u in s.unit_ids),
        start=min(starts) if starts else None,
        end=max(ends) if ends else None,
        exact_time=bool(starts and ends) and all(s.exact_time for s in segments if s.start),
    )


def _statement(topic: TopicFacts | None) -> Statement | None:
    if topic is None or not topic.summary:
        return None
    origin = {o.value: o for o in Origin}.get(topic.summary_origin or "", Origin.INFERENCE)
    return Statement(
        text=topic.summary,
        origin=origin,
        method=Method.OBSERVED,
        unit_ids=topic.summary_unit_ids,
    )


def _coarse_topics(asset: AssetFacts) -> list[TopicFacts]:
    """The coarsest topics that cover the recording: top-level sections, else plain topics."""
    sections = [t for t in asset.topics if t.level == "section" and t.parent_id is None]
    if sections:
        return sections
    topics = [t for t in asset.topics if t.level == "topic" and t.parent_id is None]
    return topics or [t for t in asset.topics if t.level == "topic"]


def build_drafts(assets: tuple[AssetFacts, ...]) -> tuple[Draft, ...]:
    drafts: list[Draft] = []
    for a_index, asset in enumerate(assets):
        order = {s.segment_id: i for i, s in enumerate(asset.segments)}
        owner: dict[str, TopicFacts] = {}
        for coarse in _coarse_topics(asset):
            for sid in coarse.segment_ids:
                if sid in order:
                    owner.setdefault(sid, coarse)
        groups: list[tuple[TopicFacts | None, list[SegmentFacts]]] = []
        by_topic: dict[str, int] = {}
        for segment in asset.segments:
            topic = owner.get(segment.segment_id)
            if topic is None:
                # Passages upstream put in no topic stay with the section before them, so no
                # sentence of the recording is silently dropped from the story.
                if groups:
                    groups[-1][1].append(segment)
                else:
                    groups.append((None, [segment]))
            elif topic.topic_id in by_topic:
                groups[by_topic[topic.topic_id]][1].append(segment)
            else:
                by_topic[topic.topic_id] = len(groups)
                groups.append((topic, [segment]))
        for group_topic, members in groups:
            members.sort(key=lambda s: order[s.segment_id])
            first = members[0]
            drafts.append(
                Draft(
                    section_id=f"sec_{a_index}_{first.segment_id}",
                    asset_index=a_index,
                    asset_id=asset.asset_id,
                    source_index=len(drafts),
                    label=(
                        group_topic.label
                        if group_topic is not None and group_topic.label
                        else excerpt(first.text, 60)
                    ),
                    segments=tuple(members),
                    summary=_statement(group_topic),
                )
            )
    return tuple(drafts)


def functions_of(segment: SegmentFacts) -> list[tuple[BeatFunction, float | None]]:
    """The story functions a passage serves, best-evidenced first (at most two)."""
    seen: dict[BeatFunction, float | None] = {}
    for role, confidence in sorted(segment.roles, key=lambda r: -(r[1] if r[1] is not None else 0)):
        function = ROLE_FUNCTION.get(role)
        if function is not None and function not in seen:
            seen[function] = confidence
    return list(seen.items())[:_MAX_BEATS_PER_SEGMENT]


def build_beats(draft: Draft) -> tuple[Beat, ...]:
    beats: list[Beat] = []
    for segment in draft.segments:
        found = functions_of(segment)
        for function, confidence in found:
            beats.append(
                _beat(
                    draft, segment, len(beats), function, confidence, Basis.DIRECT, Method.OBSERVED
                )
            )
    if not beats:
        # A section upstream gave no role anywhere still needs one anchor for the story.
        anchor = max(draft.segments, key=lambda s: s.importance if s.importance is not None else -1)
        beats.append(
            _beat(draft, anchor, 0, F.UNCLASSIFIED, None, Basis.INFERRED, Method.HEURISTIC)
        )
    return tuple(beats)


def _beat(
    draft: Draft,
    segment: SegmentFacts,
    index: int,
    function: BeatFunction,
    confidence: float | None,
    basis: Basis,
    method: Method,
) -> Beat:
    return Beat(
        beat_id=f"{draft.section_id}_b{index + 1:02d}",
        section_id=draft.section_id,
        function=function,
        source=source_ref(draft.asset_id, (segment,)),
        basis=basis,
        method=method,
        summary=segment.summary,
        confidence=confidence,
    )


def dominant_function(beats: tuple[Beat, ...]) -> BeatFunction:
    """The function that best names the section: the most frequent, earlier first on a tie."""
    counts: Counter[BeatFunction] = Counter(
        b.function for b in beats if b.function is not F.UNCLASSIFIED
    )
    if not counts:
        return F.UNCLASSIFIED
    best = max(counts.values())
    return next(b.function for b in beats if counts.get(b.function) == best)


def mode_of(beats: tuple[Beat, ...]) -> ContentMode:
    counts: Counter[ContentMode] = Counter(_MODE[b.function] for b in beats if b.function in _MODE)
    if not counts:
        return ContentMode.UNKNOWN
    best = max(counts.values())
    return next(m for m in (_MODE.get(b.function) for b in beats) if m and counts[m] == best)


def purpose_of(draft: Draft, beats: tuple[Beat, ...]) -> Statement:
    """What the section does for the story. Always an inference: the speaker did not say it."""
    function = dominant_function(beats)
    return Statement(
        text=_PURPOSE[function].format(label=draft.label),
        origin=Origin.INFERENCE,
        method=Method.HEURISTIC,
        unit_ids=draft.first.unit_ids[:1],
    )
