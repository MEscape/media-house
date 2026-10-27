"""What the planner reads: plain facts copied from the upstream analyses.

The application layer maps the content analysis and the alignment document (through their public
contracts) into these types, so the domain never imports another module. Everything here is an
observation made upstream; the planner adds structure and judgement on top, never new facts.
Times are seconds on the original media timeline, or ``None`` when upstream had none.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentFacts:
    segment_id: str
    unit_ids: tuple[str, ...]
    topic_ids: tuple[str, ...]
    start: float | None
    end: float | None
    #: ``False`` when a time was interpolated or estimated upstream.
    exact_time: bool
    text: str
    words: int
    #: ``(role, confidence)`` as named by content intelligence (``hook``, ``main_point``, ...).
    roles: tuple[tuple[str, float | None], ...] = ()
    rhetoric: tuple[str, ...] = ()
    importance: float | None = None
    density: float | None = None
    standalone: float | None = None
    hook: float | None = None
    removal: float | None = None
    #: Sentences a reader needs before this passage stands alone.
    requires_context: tuple[str, ...] = ()
    summary: str | None = None
    summary_origin: str | None = None
    key_item_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def has_role(self, *names: str) -> bool:
        return any(r in names for r, _ in self.roles)


@dataclass(frozen=True, slots=True, kw_only=True)
class TopicFacts:
    topic_id: str
    #: ``section`` (coarse) or ``topic`` (finer), as content intelligence levels them.
    level: str
    label: str
    segment_ids: tuple[str, ...]
    parent_id: str | None = None
    summary: str | None = None
    summary_origin: str | None = None
    summary_unit_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemFacts:
    item_id: str
    #: ``claim``, ``fact``, ``caveat``, ``open_question`` ... (content intelligence's kinds).
    kind: str
    text: str
    origin: str
    segment_id: str | None
    unit_ids: tuple[str, ...]
    qualified_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationFacts:
    """``source`` stands in ``kind`` to ``target`` ("source answers target"), by segment."""

    rel_id: str
    kind: str
    source_segment: str
    target_segment: str
    #: For repeats: what the later passage adds (``new_evidence`` ...), else ``None``.
    adds: str | None = None
    #: Set when the relation names an item, so claims can be linked to their support.
    source_item: str | None = None
    target_item: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class VisualFacts:
    opportunity_id: str
    #: ``broll`` or ``graphic``.
    kind: str
    category: str
    concept: str
    segment_id: str
    strength: float
    already_covered: bool
    rationale: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ShotMatchFacts:
    """A candidate shot for a passage, as alignment ranked it. Evidence, not a choice."""

    passage_id: str
    shot_id: str
    #: The shot's span on the video's own seconds.
    start: float
    end: float
    grade: str
    rank: int
    visible_speaker: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetLinkFacts:
    """How two recordings of the story relate, as asset_relationships reported it.

    ``source`` and ``target`` follow that module: lineage runs duplicate/derivative -> original,
    claims list the lower asset id first.
    """

    relationship_id: str
    #: ``exact_duplicate_of``, ``derived_from``, ``proxy_of``, ``temporally_overlaps_with``,
    #: ``alternate_take_of``.
    relation: str
    source_asset: str
    target_asset: str
    #: ``None`` for recorded lineage (a fact has no status); else proposed/confirmed/rejected/stale.
    status: str | None
    #: ``recorded``, ``corroborated``, ``indicated`` or ``suggestive``.
    grade: str
    contradicted: bool
    #: A decided claim whose evidence or basis changed since the decision.
    outdated: bool
    #: Short, readable evidence: ``kind (provider version)``.
    evidence: tuple[str, ...]
    #: Identity of the answer (id, status, grade, when it last changed): part of the cache key.
    revision: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetFacts:
    asset_id: str
    name: str
    language: str | None
    duration: float | None
    #: ``ok``, ``ok_with_warnings``, ``partial``, ``not_applicable`` (content intelligence).
    state: str
    #: Fingerprint of the exact analysis this was read from (part of the plan's cache key).
    analysis_identity: str
    segments: tuple[SegmentFacts, ...]
    topics: tuple[TopicFacts, ...] = ()
    items: tuple[ItemFacts, ...] = ()
    relations: tuple[RelationFacts, ...] = ()
    visuals: tuple[VisualFacts, ...] = ()
    matches: tuple[ShotMatchFacts, ...] = ()
    #: Whole-recording summary and takeaways as upstream wrote them: ``(text, origin, unit_ids)``.
    summary: tuple[str, str, tuple[str, ...]] | None = None
    takeaways: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    warnings: tuple[str, ...] = ()
    alignment_identity: str | None = None
    #: Free-form notes on how the inputs were obtained.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def segment(self, segment_id: str) -> SegmentFacts | None:
        return next((s for s in self.segments if s.segment_id == segment_id), None)
