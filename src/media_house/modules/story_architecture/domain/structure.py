"""How the sections depend on each other, and the order that respects those dependencies.

Dependencies are read from the relationships content intelligence found (and from the context a
passage says it needs), never guessed. The recommended order starts as the recorded order and
changes only when the format allows it and a hard dependency points forward in the recording; a
section pulled forward to give context keeps its internal order. Circular dependencies cannot
be satisfied, so the edge that closes a cycle is dropped and reported rather than emitted.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from media_house.modules.story_architecture.domain.facts import (
    AssetFacts,
    RelationFacts,
    SegmentFacts,
)
from media_house.modules.story_architecture.domain.model import (
    ContextDependency,
    OrderChange,
    OrderingConstraint,
    SetupPayoffLink,
    SourceRef,
)
from media_house.modules.story_architecture.domain.profile import ReorderPolicy
from media_house.modules.story_architecture.domain.sections import Draft, source_ref
from media_house.modules.story_architecture.domain.values import (
    Basis,
    ConstraintKind,
    LinkKind,
    LinkState,
    Strength,
)

#: relation -> (constraint kind, strength). ``source`` stands in the relation to ``target``; for
#: every entry here ``target`` has to come first ("source depends on target").
_PRECEDENCE: dict[str, Strength] = {
    "depends_on": Strength.HARD,
    "answers": Strength.HARD,
    "qualifies": Strength.HARD,
    "continues": Strength.HARD,
    "references": Strength.SOFT,
    "summarizes": Strength.SOFT,
}
_ADJACENT: dict[str, Strength] = {
    "qualifies": Strength.HARD,
    "continues": Strength.SOFT,
    "answers": Strength.SOFT,
    "transitions_to": Strength.SOFT,
}
_REASON: dict[str, str] = {
    "depends_on": "depends on",
    "answers": "answers a question in",
    "qualifies": "qualifies a statement in",
    "continues": "continues",
    "references": "refers back to",
    "summarizes": "summarises",
    "transitions_to": "leads into",
}


type AddConstraint = Callable[[ConstraintKind, str, str, Strength, str], None]


@dataclass(slots=True)
class Index:
    """Where each passage and sentence lives, so a relation can be placed between sections."""

    drafts: tuple[Draft, ...]
    assets: tuple[AssetFacts, ...]
    _segment: dict[tuple[str, str], str] = field(default_factory=dict)
    _unit: dict[tuple[str, str], str] = field(default_factory=dict)
    _by_id: dict[str, Draft] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for draft in self.drafts:
            self._by_id[draft.section_id] = draft
            for segment in draft.segments:
                self._segment[(draft.asset_id, segment.segment_id)] = draft.section_id
                for unit in segment.unit_ids:
                    self._unit[(draft.asset_id, unit)] = segment.segment_id

    def section_of(self, asset_id: str, segment_id: str) -> str | None:
        return self._segment.get((asset_id, segment_id))

    def segment_of_unit(self, asset_id: str, unit_id: str) -> str | None:
        return self._unit.get((asset_id, unit_id))

    def draft(self, section_id: str) -> Draft:
        return self._by_id[section_id]

    def ref_of_units(self, asset_id: str, unit_ids: tuple[str, ...]) -> SourceRef | None:
        """The passage made of the segments that hold ``unit_ids``, or ``None`` if none is known."""
        segment_ids = tuple(
            dict.fromkeys(s for u in unit_ids if (s := self.segment_of_unit(asset_id, u)))
        )
        if not segment_ids:
            return None
        section = self.section_of(asset_id, segment_ids[0])
        if section is None:
            return None
        wanted = set(segment_ids)
        members = tuple(s for s in self.draft(section).segments if s.segment_id in wanted)
        return source_ref(asset_id, members) if members else None

    def ref_of_segment(self, asset_id: str, segment_id: str) -> SourceRef | None:
        section = self.section_of(asset_id, segment_id)
        if section is None:
            return None
        members = tuple(s for s in self.draft(section).segments if s.segment_id == segment_id)
        return source_ref(asset_id, members) if members else None


@dataclass(frozen=True, slots=True)
class Structure:
    constraints: tuple[OrderingConstraint, ...]
    dependencies: tuple[ContextDependency, ...]
    links: tuple[SetupPayoffLink, ...]
    #: Edges dropped because they would have closed a cycle: ``(first, second, relation)``.
    dropped: tuple[tuple[str, str, str], ...]


def _between(index: Index, asset: AssetFacts, rel: RelationFacts) -> tuple[str, str] | None:
    source = index.section_of(asset.asset_id, rel.source_segment)
    target = index.section_of(asset.asset_id, rel.target_segment)
    if source is None or target is None or source == target:
        return None
    return source, target


def derive(index: Index) -> Structure:
    """Constraints, context dependencies and setup/payoff links from the upstream relations."""
    found: dict[tuple[ConstraintKind, str, str], tuple[Strength, str]] = {}

    def add(
        kind: ConstraintKind, first: str, second: str, strength: Strength, relation: str
    ) -> None:
        key = (kind, first, second)
        current = found.get(key)
        if current is None or (strength is Strength.HARD and current[0] is Strength.SOFT):
            found[key] = (strength, relation)

    deps: dict[tuple[str, str], tuple[str, SourceRef | None, Basis]] = {}
    for asset in index.assets:
        for rel in asset.relations:
            pair = _between(index, asset, rel)
            if pair is None:
                continue
            source, target = pair  # ``target`` has to come first for the "depends" family
            if rel.kind in _PRECEDENCE:
                add(ConstraintKind.MUST_PRECEDE, target, source, _PRECEDENCE[rel.kind], rel.kind)
                needed = index.ref_of_segment(asset.asset_id, rel.target_segment)
                deps.setdefault((source, target), (rel.kind, needed, Basis.DIRECT))
            if rel.kind in _ADJACENT:
                first, second = (
                    (source, target) if rel.kind == "transitions_to" else (target, source)
                )
                add(ConstraintKind.KEEP_ADJACENT, first, second, _ADJACENT[rel.kind], rel.kind)
        for segment in (
            s for d in index.drafts if d.asset_id == asset.asset_id for s in d.segments
        ):
            section = index.section_of(asset.asset_id, segment.segment_id)
            for unit in segment.requires_context:
                seg_id = index.segment_of_unit(asset.asset_id, unit)
                other = index.section_of(asset.asset_id, seg_id) if seg_id else None
                if section and other and other != section:
                    deps.setdefault(
                        (section, other),
                        (
                            "needs_context",
                            index.ref_of_units(asset.asset_id, (unit,)),
                            Basis.DIRECT,
                        ),
                    )
        for item in asset.items:
            _qualifier_constraints(index, asset, item.segment_id, item.qualified_by, add)

    constraints = [
        OrderingConstraint(
            constraint_id="",
            kind=kind,
            first=first,
            second=second,
            strength=strength,
            reason=_constraint_reason(index, kind, first, second, relation),
            relation=relation,
            basis=Basis.DIRECT,
        )
        for (kind, first, second), (strength, relation) in found.items()
    ]
    constraints.sort(
        key=lambda c: (
            c.strength is not Strength.HARD,
            c.kind.value,
            index.draft(c.first).source_index,
            index.draft(c.second).source_index,
        )
    )
    kept, dropped = _break_cycles(constraints)
    numbered = tuple(
        OrderingConstraint(
            constraint_id=f"oc_{i + 1:03d}",
            kind=c.kind,
            first=c.first,
            second=c.second,
            strength=c.strength,
            reason=c.reason,
            relation=c.relation,
            basis=c.basis,
        )
        for i, c in enumerate(kept)
    )
    dependencies = tuple(
        ContextDependency(
            dependency_id=f"cd_{i + 1:03d}",
            section_id=section,
            requires_section_id=other,
            reason=_dependency_reason(index, section, other, relation),
            needed=needed,
            basis=basis,
        )
        for i, ((section, other), (relation, needed, basis)) in enumerate(
            sorted(deps.items(), key=lambda kv: _sort_pair(index, kv[0]))
        )
    )
    return Structure(numbered, dependencies, _links(index), dropped)


def _sort_pair(index: Index, pair: tuple[str, str]) -> tuple[int, int]:
    return index.draft(pair[0]).source_index, index.draft(pair[1]).source_index


def _qualifier_constraints(
    index: Index,
    asset: AssetFacts,
    segment_id: str | None,
    qualified_by: tuple[str, ...],
    add: AddConstraint,
) -> None:
    """A claim and the sentences that limit it have to stay together when they sit apart."""
    claim = index.section_of(asset.asset_id, segment_id) if segment_id else None
    for unit in qualified_by:
        seg = index.segment_of_unit(asset.asset_id, unit)
        qualifier = index.section_of(asset.asset_id, seg) if seg else None
        if claim and qualifier and claim != qualifier:
            add(ConstraintKind.KEEP_ADJACENT, claim, qualifier, Strength.HARD, "qualifies")
            add(ConstraintKind.MUST_PRECEDE, claim, qualifier, Strength.HARD, "qualifies")


def _constraint_reason(
    index: Index, kind: ConstraintKind, first: str, second: str, rel: str
) -> str:
    a, b = index.draft(first).label, index.draft(second).label
    if kind is ConstraintKind.KEEP_ADJACENT:
        return f"'{b}' stays next to '{a}' ({_REASON.get(rel, rel)})"
    return f"'{a}' has to come before '{b}', which {_REASON.get(rel, rel)} it"


def _dependency_reason(index: Index, section: str, other: str, relation: str) -> str:
    label, needed = index.draft(section).label, index.draft(other).label
    if relation == "needs_context":
        return f"'{label}' is not understandable without sentences from '{needed}'"
    return f"'{label}' {_REASON.get(relation, relation)} '{needed}'"


def _break_cycles(
    constraints: list[OrderingConstraint],
) -> tuple[list[OrderingConstraint], tuple[tuple[str, str, str], ...]]:
    """Keep every constraint that leaves the hard precedence graph acyclic (hard ones first)."""
    successors: dict[str, set[str]] = defaultdict(set)
    kept: list[OrderingConstraint] = []
    dropped: list[tuple[str, str, str]] = []

    def reaches(start: str, goal: str) -> bool:
        stack, seen = [start], set[str]()
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node not in seen:
                seen.add(node)
                stack.extend(successors[node])
        return False

    for c in constraints:
        hard_precedence = c.kind is ConstraintKind.MUST_PRECEDE and c.strength is Strength.HARD
        if hard_precedence:
            if reaches(c.second, c.first):
                dropped.append((c.first, c.second, c.relation))
                continue
            successors[c.first].add(c.second)
        kept.append(c)
    return kept, tuple(dropped)


def _links(index: Index) -> tuple[SetupPayoffLink, ...]:
    links: list[SetupPayoffLink] = []
    for asset in index.assets:
        answered: dict[str, str] = {}
        for rel in asset.relations:
            if rel.kind == "answers":
                answered.setdefault(rel.target_segment, rel.source_segment)
        rhetorical = {
            s.segment_id
            for s in _segments(index, asset.asset_id)
            if "rhetorical_question" in s.rhetoric
        }
        for segment in _segments(index, asset.asset_id):
            if not segment.has_role("question") or segment.segment_id in rhetorical:
                continue
            setup_ref = index.ref_of_segment(asset.asset_id, segment.segment_id)
            setup_section = index.section_of(asset.asset_id, segment.segment_id)
            if setup_ref is None or setup_section is None:
                continue
            answer_id = answered.get(segment.segment_id)
            payoff_ref = index.ref_of_segment(asset.asset_id, answer_id) if answer_id else None
            payoff_section = index.section_of(asset.asset_id, answer_id) if answer_id else None
            links.append(
                SetupPayoffLink(
                    link_id="",
                    kind=LinkKind.QUESTION_ANSWER,
                    state=LinkState.RESOLVED if payoff_ref else LinkState.OPEN,
                    setup=setup_ref,
                    setup_section=setup_section,
                    payoff=payoff_ref,
                    payoff_section=payoff_section,
                    distance=None,
                    basis=Basis.DIRECT,
                )
            )
    return tuple(links)


def _segments(index: Index, asset_id: str) -> list[SegmentFacts]:
    return [s for d in index.drafts if d.asset_id == asset_id for s in d.segments]


def order_sections(
    index: Index, structure: Structure, policy: ReorderPolicy
) -> tuple[tuple[str, ...], tuple[OrderChange, ...]]:
    """The recommended order, and the sections that were moved to get there."""
    source = tuple(d.section_id for d in index.drafts)
    if policy is ReorderPolicy.PRESERVE:
        return source, ()
    hard: dict[str, list[OrderingConstraint]] = defaultdict(list)
    for c in structure.constraints:
        if c.kind is ConstraintKind.MUST_PRECEDE and c.strength is Strength.HARD:
            hard[c.second].append(c)
    placed: list[str] = []
    moved: dict[str, list[OrderingConstraint]] = {}

    def place(section: str, pulled_by: OrderingConstraint | None) -> None:
        if section in placed:
            return
        for c in sorted(hard[section], key=lambda c: index.draft(c.first).source_index):
            if c.first not in placed:
                place(c.first, c)
        if pulled_by is not None:
            moved.setdefault(section, []).append(pulled_by)
        placed.append(section)

    for section in source:
        place(section, None)
    changes = tuple(
        OrderChange(
            section_id=section,
            from_index=index.draft(section).source_index,
            to_index=placed.index(section),
            reason=(f"moved ahead of '{index.draft(by[0].second).label}' to give it context"),
            constraint_ids=tuple(c.constraint_id for c in by),
        )
        for section, by in moved.items()
        if placed.index(section) != index.draft(section).source_index
    )
    return tuple(placed), changes


def with_distance(
    links: tuple[SetupPayoffLink, ...], order: tuple[str, ...]
) -> tuple[SetupPayoffLink, ...]:
    """Number the links and measure how many sections lie between a setup and its payoff."""
    position = {s: i for i, s in enumerate(order)}
    out: list[SetupPayoffLink] = []
    for i, link in enumerate(links):
        distance = (
            abs(position[link.payoff_section] - position[link.setup_section]) - 1
            if link.payoff_section is not None
            else None
        )
        out.append(
            SetupPayoffLink(
                link_id=link.link_id or f"sp_{i + 1:03d}",
                kind=link.kind,
                state=link.state,
                setup=link.setup,
                setup_section=link.setup_section,
                payoff=link.payoff,
                payoff_section=link.payoff_section,
                distance=None if distance is None else max(distance, 0),
                basis=link.basis,
            )
        )
    return tuple(out)
