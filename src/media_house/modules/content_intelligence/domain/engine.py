"""The deterministic analysis, end to end, in pure Python.

``prepare`` turns a transcript into sentences and word statistics (cacheable, needs no model).
``analyze`` runs segmentation, key information, relationships, scores, roles, opportunities and
summaries over them with a chosen similarity provider. Neither touches I/O, a clock or a model,
so the whole pipeline is testable with plain text. A language model, when configured, refines
the result afterwards (``refine``) and can only add what the checks in ``grounding`` accept.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from media_house.modules.content_intelligence.domain.config import ContentConfig
from media_house.modules.content_intelligence.domain.context import AudioCues, VisualContext
from media_house.modules.content_intelligence.domain.errors import InvalidTranscriptInput
from media_house.modules.content_intelligence.domain.frame import Frame
from media_house.modules.content_intelligence.domain.ids import segment_id
from media_house.modules.content_intelligence.domain.keyinfo import extract
from media_house.modules.content_intelligence.domain.lexical import LexicalSimilarity, TextStats
from media_house.modules.content_intelligence.domain.lexicon import Lexicon, lexicon_for
from media_house.modules.content_intelligence.domain.model import (
    ContentAnalysis,
    InputRecord,
    KeyItem,
    Observation,
    Provenance,
    RhetoricalMarker,
    RoleLabel,
    Score,
    SemanticSegment,
    SourceRecord,
    StageReport,
    Summary,
    Topic,
)
from media_house.modules.content_intelligence.domain.narrative import cue_roles, finalize_roles
from media_house.modules.content_intelligence.domain.opportunities import Found, detect
from media_house.modules.content_intelligence.domain.relations import Relations, build
from media_house.modules.content_intelligence.domain.scoring import score_all
from media_house.modules.content_intelligence.domain.segmentation import segment
from media_house.modules.content_intelligence.domain.similarity import (
    EMBEDDING,
    LEXICAL,
    SimilarityProvider,
)
from media_house.modules.content_intelligence.domain.source import (
    SourceTranscript,
    validate_source,
)
from media_house.modules.content_intelligence.domain.span import Span
from media_house.modules.content_intelligence.domain.structure import Structure, build_structure
from media_house.modules.content_intelligence.domain.summarize import (
    KIND_WEIGHT,
    summarize,
    takeaways,
)
from media_house.modules.content_intelligence.domain.units import Unit, build_units
from media_house.modules.content_intelligence.domain.values import (
    KeyKind,
    RelationKind,
    ResultState,
    ScoreName,
    SegmentLevel,
    StageState,
)

_STATIC_LIMITS = (
    "Scores are advisory heuristics, not calibrated probabilities; "
    "they recommend, they do not decide.",
    "Uniqueness and redundancy compare passages within this recording only.",
    "Visual and audio observations are related to sentences by overlapping time only; "
    "no word-to-frame synchronisation is claimed.",
)


@dataclass(frozen=True, slots=True)
class Prepared:
    """Everything derived from the transcript alone."""

    source: SourceTranscript
    language: str | None
    lexicon: Lexicon
    units: tuple[Unit, ...]
    stats: TextStats
    warnings: tuple[str, ...]

    @property
    def texts(self) -> list[str]:
        return [u.text for u in self.units]

    def lexical_similarity(self) -> LexicalSimilarity:
        return LexicalSimilarity(self.stats)


@dataclass(frozen=True, slots=True)
class AnalysisMeta:
    """What the caller knows that the text does not: identity, time, inputs, engines."""

    analysis_id: str
    asset_id: str | None
    timestamp: datetime
    source: SourceRecord
    inputs: tuple[InputRecord, ...]
    provenance: Provenance
    stages: tuple[StageReport, ...] = ()
    warnings: tuple[str, ...] = ()


def prepare(source: SourceTranscript, config: ContentConfig) -> Prepared:
    warnings = list(validate_source(source))
    language = config.language or source.language
    lexicon = lexicon_for(language)
    if language and not lexicon.supported:
        warnings.append(
            f"no cue lists exist for language '{language}': roles, key items and opportunities "
            "use language-neutral signals only"
        )
    units = build_units(source, config.thresholds, lexicon.family)
    if len(units) > config.max_units:
        raise InvalidTranscriptInput(
            f"{len(units)} sentences exceed the limit of {config.max_units}"
        )
    stats = TextStats.of([u.text for u in units], lexicon)
    return Prepared(source, language, lexicon, units, stats, tuple(warnings))


def derive_state(
    warnings: Sequence[str], stages: Sequence[StageReport], empty: bool
) -> ResultState:
    if empty:
        return ResultState.NOT_APPLICABLE
    if any(s.state in {StageState.FAILED, StageState.NOT_AVAILABLE} for s in stages):
        return ResultState.PARTIAL
    return ResultState.WARNINGS if warnings else ResultState.OK


def analyze(
    prepared: Prepared,
    similarity: SimilarityProvider,
    config: ContentConfig,
    meta: AnalysisMeta,
    visual: VisualContext | None = None,
    audio: AudioCues | None = None,
) -> ContentAnalysis:
    units = prepared.units
    warnings = list(prepared.warnings) + list(meta.warnings)
    limits = list(_STATIC_LIMITS)
    if not units:
        return _empty(prepared, meta, warnings)

    lex, thresholds, settings = prepared.lexicon, config.thresholds, config.settings
    segmentation = segment(units, similarity, lex, thresholds, settings.similarity_window, visual)
    structure = build_structure(units, prepared.stats, similarity, segmentation, thresholds)
    frame = Frame(
        units,
        prepared.stats,
        lex,
        similarity,
        similarity.neighbours(settings.neighbours_k, 0.5 * _overlap(config, similarity)),
        thresholds,
        settings,
        visual,
        audio,
        structure.segment_of_unit,
    )

    info = extract(frame, structure.segments)
    relations = build(frame, structure.segments, info.items, structure.topics)
    unit_position = {u.unit_id: u.index for u in units}
    items = sorted(
        [*info.items, *relations.new_items],
        key=lambda i: (unit_position[i.unit_ids[0]], i.item_id),
    )
    labels = cue_roles(frame, structure, items)
    scores = score_all(
        frame, structure, items, relations, [{lab.role for lab in row} for row in labels]
    )
    roles = finalize_roles(frame, structure, items, labels, scores)
    found = detect(frame, structure, items, [{lab.role for lab in row} for row in roles])

    by_unit: dict[int, list[KeyItem]] = {}
    index_of = {u.unit_id: u.index for u in units}
    for item in items:
        by_unit.setdefault(index_of[item.unit_ids[0]], []).append(item)
    markers = _markers_by_segment(info.markers, structure)
    importance = {
        segment_id(k): next(
            (s.value for s in row if s.name is ScoreName.IMPORTANCE and s.value is not None), 0.5
        )
        for k, row in enumerate(scores)
    }

    segment_summaries = [
        summarize(frame, list(r), thresholds.summary_sentences_segment, by_unit)
        for r in structure.segments
    ]
    used = {index_of[uid] for s in segment_summaries if s for uid in s.unit_ids}
    topics = _summarised_topics(frame, structure, items, by_unit, used)
    pool = sorted({index_of[uid] for t in topics if t.summary for uid in t.summary.unit_ids} | used)
    recording = summarize(
        frame, pool or list(range(len(units))), thresholds.summary_sentences_recording, by_unit
    )
    key_takeaways = takeaways(frame, items, importance, thresholds.key_takeaways)

    segments = _segments(
        frame, structure, items, segment_summaries, roles, scores, found, relations, markers
    )
    observations = _observations(items, relations, segments, found.suppressed, found.visual_checked)
    if similarity.kind == LEXICAL:
        limits.append(
            "Topics and redundancy come from word overlap: "
            "paraphrases in different words are not matched."
        )
    if not lex.supported:
        limits.append(
            "Cue-based features (roles, key items, opportunities) are limited "
            "without cue lists for this language."
        )
    if visual is None:
        limits.append("No visual analysis was used.")
    if audio is None:
        limits.append(
            "No acoustic analysis was used: delivery cues (emphasis, laughter) are absent."
        )

    n_topics = sum(1 for t in structure.topics if t.level is SegmentLevel.TOPIC)
    stages = (
        *meta.stages,
        StageReport(
            stage="segmentation",
            state=StageState.OK,
            detail=f"{len(segments)} segments, {n_topics} topics",
        ),
        StageReport(
            stage="analysis",
            state=StageState.OK,
            detail=f"{len(items)} key items, {len(relations.relationships)} relationships",
        ),
    )
    return ContentAnalysis(
        analysis_id=meta.analysis_id,
        asset_id=meta.asset_id,
        state=derive_state(warnings, stages, empty=False),
        analysis_timestamp=meta.timestamp,
        source=meta.source,
        inputs=meta.inputs,
        provenance=meta.provenance,
        stages=stages,
        units=units,
        segments=tuple(segments),
        topics=tuple(topics),
        key_items=tuple(items),
        relationships=relations.relationships,
        summary=recording,
        key_takeaways=tuple(key_takeaways),
        global_observations=tuple(observations),
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=tuple(limits),
    )


def _overlap(config: ContentConfig, similarity: SimilarityProvider) -> float:
    t = config.thresholds
    return t.overlap_embedding if similarity.kind == EMBEDDING else t.overlap_lexical


def _empty(prepared: Prepared, meta: AnalysisMeta, warnings: list[str]) -> ContentAnalysis:
    warnings.append("the transcript holds no speech to analyse")
    stages = (*meta.stages,)
    return ContentAnalysis(
        analysis_id=meta.analysis_id,
        asset_id=meta.asset_id,
        state=ResultState.NOT_APPLICABLE,
        analysis_timestamp=meta.timestamp,
        source=meta.source,
        inputs=meta.inputs,
        provenance=meta.provenance,
        stages=stages,
        units=(),
        segments=(),
        topics=(),
        key_items=(),
        relationships=(),
        summary=None,
        key_takeaways=(),
        global_observations=(),
        warnings=tuple(dict.fromkeys(warnings)),
        limitations=_STATIC_LIMITS,
    )


def _markers_by_segment(
    markers: list[tuple[int, RhetoricalMarker]], structure: Structure
) -> dict[int, list[RhetoricalMarker]]:
    out: dict[int, list[RhetoricalMarker]] = {}
    for unit_index, marker in markers:
        out.setdefault(structure.segment_of_unit[unit_index], []).append(marker)
    return out


def _summarised_topics(
    frame: Frame,
    structure: Structure,
    items: list[KeyItem],
    by_unit: dict[int, list[KeyItem]],
    used: set[int],
) -> list[Topic]:
    out: list[Topic] = []
    seg_index = {segment_id(k): k for k in range(len(structure.segments))}
    for topic in structure.topics:
        members = [seg_index[s] for s in topic.segment_ids]
        indices = [i for k in members for i in structure.segments[k]]
        top_items = sorted(
            (it for it in items if it.segment_id in topic.segment_ids),
            key=lambda it: (-KIND_WEIGHT.get(it.kind, 0.0) * (it.confidence or 0.5), it.item_id),
        )[:5]
        summary: Summary | None = None
        if len(members) >= 2:
            summary = summarize(
                frame, indices, frame.thresholds.summary_sentences_topic, by_unit, used
            )
        out.append(
            replace(topic, summary=summary, key_item_ids=tuple(it.item_id for it in top_items))
        )
    return out


def _segments(
    frame: Frame,
    structure: Structure,
    items: list[KeyItem],
    summaries: list[Summary | None],
    roles: list[tuple[RoleLabel, ...]],
    scores: list[list[Score]],
    found: Found,
    relations: Relations,
    markers: dict[int, list[RhetoricalMarker]],
) -> list[SemanticSegment]:
    out: list[SemanticSegment] = []
    last = len(structure.segments) - 1
    items_of: dict[str, list[str]] = {}
    for it in items:
        if it.segment_id:
            items_of.setdefault(it.segment_id, []).append(it.item_id)
    for k, rng in enumerate(structure.segments):
        sid = segment_id(k)
        boundary = structure.boundaries[k]
        following = structure.boundaries[k + 1] if k < last else None
        strengths = [b.strength for b in (boundary, following) if b is not None]
        span = Span.cover(frame.units[rng.start].span, frame.units[rng.stop - 1].span)
        warns: list[str] = []
        if boundary is not None and boundary.weak:
            warns.append("the start of this passage is weakly supported by the evidence")
        if span.known and not span.exact:
            warns.append("times are estimated, not measured")
        out.append(
            SemanticSegment(
                segment_id=sid,
                unit_ids=tuple(frame.units[i].unit_id for i in rng),
                span=span,
                topic_ids=structure.topic_ids[k],
                boundary=boundary,
                summary=summaries[k],
                key_item_ids=tuple(items_of.get(sid, ())),
                roles=roles[k],
                rhetoric=tuple(markers.get(k, ())),
                scores=tuple(scores[k]),
                broll=tuple(found.broll[k]),
                graphics=tuple(found.graphics[k]),
                dependency_ids=tuple(relations.dependencies.get(k, ())),
                redundancy_ids=tuple(relations.redundancies.get(k, ())),
                confidence=min(strengths) if strengths else None,
                warnings=tuple(warns),
            )
        )
    return out


def _observations(
    items: list[KeyItem],
    relations: Relations,
    segments: list[SemanticSegment],
    suppressed: int,
    visual_checked: bool,
) -> list[Observation]:
    out: list[Observation] = []
    open_q = [i for i in items if i.kind is KeyKind.OPEN_QUESTION]
    if open_q:
        out.append(
            Observation(
                kind="open_questions",
                text=f"{len(open_q)} question(s) received no answer in the transcript",
                evidence=tuple(i.evidence[0] for i in open_q[:5]),
            )
        )
    repeats = [
        r
        for r in relations.relationships
        if r.kind in {RelationKind.REPEATS, RelationKind.OVERLAPS}
    ]
    if repeats:
        nothing = sum(1 for r in repeats if r.adds is not None and r.adds.value == "nothing_new")
        out.append(
            Observation(
                kind="repetition",
                text=(
                    f"{len(repeats)} passage(s) restate earlier ones; {nothing} add nothing "
                    "new by these checks (not a removal decision)"
                ),
            )
        )
    weak = sum(1 for s in segments if s.boundary is not None and s.boundary.weak)
    if weak:
        out.append(
            Observation(
                kind="weak_boundaries", text=f"{weak} segment boundary(ies) rest on weak evidence"
            )
        )
    if visual_checked and suppressed:
        out.append(
            Observation(
                kind="graphics_already_on_screen",
                text=(
                    f"{suppressed} graphic suggestion(s) were dropped because the text is "
                    "already on screen"
                ),
            )
        )
    return out


__all__ = ["AnalysisMeta", "Prepared", "analyze", "derive_state", "prepare"]
