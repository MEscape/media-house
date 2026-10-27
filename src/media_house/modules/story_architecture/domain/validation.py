"""The plan's own consistency checks. A plan that fails them is a bug and is never stored.

Checks the document against itself (identifiers, relationships, order, dependency cycles,
durations) and, when the analyses it was built from are given, against them (every cited asset
and segment exists, every time range is valid).
"""

import math
from collections import Counter, defaultdict
from collections.abc import Mapping

from media_house.modules.story_architecture.domain.model import SourceRef, StoryPlan
from media_house.modules.story_architecture.domain.values import (
    ConstraintKind,
    DurationBasis,
    Strength,
)

_TOLERANCE = 1e-3


def validate_plan(
    plan: StoryPlan, known_segments: Mapping[str, frozenset[str]] | None = None
) -> list[str]:
    """Every problem found, or an empty list. ``known_segments``: asset id -> segment ids."""
    problems: list[str] = []
    problems += _unique_ids(plan)
    section_ids = {s.section_id for s in plan.sections}
    problems += _relationships(plan, section_ids)
    problems += _order(plan, section_ids)
    problems += _cycles(plan)
    problems += _durations(plan)
    problems += _sources(plan, known_segments)
    problems += _scores(plan)
    return problems


def _unique_ids(plan: StoryPlan) -> list[str]:
    groups: dict[str, list[str]] = {
        "section": [s.section_id for s in plan.sections],
        "beat": [b.beat_id for b in plan.beats],
        "key information": [k.info_id for k in plan.key_information],
        "evidence link": [e.link_id for e in plan.supporting_evidence],
        "constraint": [c.constraint_id for c in plan.ordering_constraints],
        "dependency": [d.dependency_id for d in plan.context_dependencies],
        "setup/payoff link": [x.link_id for x in plan.setup_payoff_links],
        "opening": [o.candidate_id for o in plan.candidate_openings],
        "visual": [v.visual_id for v in plan.visual_opportunities],
        "pacing note": [n.note_id for n in plan.pacing_notes],
        "issue": [i.issue_id for i in plan.completeness_issues],
        "asset relation": [r.relationship_id for r in plan.asset_relations],
        "alternative": [a.alternative_id for a in plan.alternative_structures],
    }
    out: list[str] = []
    for name, ids in groups.items():
        out += [
            f"duplicate or empty {name} id {k!r}" for k, n in Counter(ids).items() if n > 1 or not k
        ]
    return out


def _relationships(plan: StoryPlan, section_ids: set[str]) -> list[str]:
    out: list[str] = []
    beat_ids = {b.beat_id for b in plan.beats}
    info_ids = {k.info_id for k in plan.key_information}
    link_ids = {e.link_id for e in plan.supporting_evidence}
    visual_ids = {v.visual_id for v in plan.visual_opportunities}
    issue_ids = {i.issue_id for i in plan.completeness_issues}
    for b in plan.beats:
        if b.section_id not in section_ids:
            out.append(f"beat {b.beat_id} belongs to unknown section {b.section_id}")
    for s in plan.sections:
        out += [
            f"section {s.section_id} lists unknown beat {x}"
            for x in s.beat_ids
            if x not in beat_ids
        ]
        out += [
            f"section {s.section_id} requires unknown section {x}"
            for x in s.requires
            if x not in section_ids
        ]
        out += [
            f"section {s.section_id} lists unknown visual {x}"
            for x in s.visual_ids
            if x not in visual_ids
        ]
        out += [
            f"section {s.section_id} lists unknown issue {x}"
            for x in s.issue_ids
            if x not in issue_ids
        ]
        if not s.beat_ids:
            out.append(f"section {s.section_id} has no beat")
        if s.requires and s.section_id in s.requires:
            out.append(f"section {s.section_id} requires itself")
    for k in plan.key_information:
        if k.section_id not in section_ids:
            out.append(f"key information {k.info_id} belongs to unknown section {k.section_id}")
        out += [
            f"key information {k.info_id} cites unknown evidence {x}"
            for x in k.support_ids
            if x not in link_ids
        ]
        if k.verification != "unverified":
            out.append(f"key information {k.info_id} claims verification")
    for e in plan.supporting_evidence:
        if e.info_id not in info_ids:
            out.append(f"evidence {e.link_id} supports unknown information {e.info_id}")
    for c in plan.ordering_constraints:
        for end in (c.first, c.second):
            if end not in section_ids:
                out.append(f"constraint {c.constraint_id} names unknown section {end}")
        if c.first == c.second:
            out.append(f"constraint {c.constraint_id} joins a section to itself")
    for d in plan.context_dependencies:
        for end in (d.section_id, d.requires_section_id):
            if end not in section_ids:
                out.append(f"dependency {d.dependency_id} names unknown section {end}")
    for x in plan.setup_payoff_links:
        for named in (x.setup_section, x.payoff_section):
            if named is not None and named not in section_ids:
                out.append(f"link {x.link_id} names unknown section {named}")
        if (x.payoff is None) != (x.payoff_section is None):
            out.append(f"link {x.link_id} has a payoff without a section or the reverse")
    for v in plan.visual_opportunities:
        if v.section_id not in section_ids:
            out.append(f"visual {v.visual_id} belongs to unknown section {v.section_id}")
    for n in plan.pacing_notes:
        if n.section_id not in section_ids:
            out.append(f"pacing note {n.note_id} names unknown section {n.section_id}")
    for i in plan.completeness_issues:
        if i.section_id is not None and i.section_id not in section_ids:
            out.append(f"issue {i.issue_id} names unknown section {i.section_id}")
    for o in plan.candidate_openings:
        if o.section_id not in section_ids:
            out.append(f"opening {o.candidate_id} names unknown section {o.section_id}")
    assets = {a.asset_id for a in plan.source_assets}
    for r in plan.asset_relations:
        for end in (r.source_asset, r.target_asset):
            if end not in assets:
                out.append(f"asset relation {r.relationship_id} names unknown asset {end}")
        out += [
            f"asset relation {r.relationship_id} names unknown section {x}"
            for x in r.affected_sections
            if x not in section_ids
        ]
    if plan.recommended_opening and plan.opening(plan.recommended_opening) is None:
        out.append(f"recommended opening {plan.recommended_opening} does not exist")
    for a in plan.alternative_structures:
        out += [
            f"alternative {a.alternative_id} names unknown section {x}"
            for x in (*a.section_order, *a.omits)
            if x not in section_ids
        ]
        if a.opening_candidate and plan.opening(a.opening_candidate) is None:
            out.append(f"alternative {a.alternative_id} uses unknown opening {a.opening_candidate}")
    return out


def _order(plan: StoryPlan, section_ids: set[str]) -> list[str]:
    out: list[str] = []
    pattern = plan.narrative_pattern
    for label, order in (
        ("source", pattern.source_order),
        ("recommended", pattern.recommended_order),
    ):
        if sorted(order) != sorted(section_ids):
            out.append(f"the {label} order is not a permutation of the sections")
    position = {s: i for i, s in enumerate(pattern.recommended_order)}
    if sorted(s.order for s in plan.sections) != list(range(len(plan.sections))):
        out.append("section order numbers are not 0..n-1")
    for s in plan.sections:
        if position.get(s.section_id) != s.order:
            out.append(f"section {s.section_id} order disagrees with the recommended order")
    return out


def _cycles(plan: StoryPlan) -> list[str]:
    """The hard precedence constraints must be satisfiable, so they must not form a cycle.

    Context requirements may legitimately point both ways (two sections each needing a sentence of
    the other), so only the order graph is held to this."""
    graph: dict[str, set[str]] = defaultdict(set)
    for c in plan.ordering_constraints:
        if c.kind is ConstraintKind.MUST_PRECEDE and c.strength is Strength.HARD:
            graph[c.first].add(c.second)
    return ["the hard precedence constraints contain a cycle"] if _has_cycle(graph) else []


def _has_cycle(graph: dict[str, set[str]]) -> bool:
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1
        for nxt in graph.get(node, ()):
            if state.get(nxt) == 1 or (nxt not in state and visit(nxt)):
                return True
        state[node] = 2
        return False

    return any(n not in state and visit(n) for n in list(graph))


def _durations(plan: StoryPlan) -> list[str]:
    out: list[str] = []
    for s in plan.sections:
        d = s.duration
        if d.seconds is not None and (not math.isfinite(d.seconds) or d.seconds < 0):
            out.append(f"section {s.section_id} has an invalid duration")
        if (d.seconds is None) != (d.basis is DurationBasis.UNKNOWN):
            out.append(f"section {s.section_id} duration and its basis disagree")
    known = [s.duration.seconds for s in plan.sections if s.duration.seconds is not None]
    total = plan.estimated_duration.total.seconds
    if known and (total is None or abs(total - sum(known)) > _TOLERANCE):
        out.append("the total duration is not the sum of the section durations")
    if not known and total is not None:
        out.append("the total duration is known while no section duration is")
    core = plan.estimated_duration.core.seconds
    essential = [
        s.duration.seconds or 0.0 for s in plan.sections if s.priority.value == "essential"
    ]
    if core is not None and abs(core - sum(essential)) > _TOLERANCE:
        out.append("the core duration is not the sum of the essential sections")
    return out


def _check_ref(ref: SourceRef, where: str, known: Mapping[str, frozenset[str]] | None) -> list[str]:
    out: list[str] = []
    if known is not None:
        segments = known.get(ref.asset_id)
        if segments is None:
            out.append(f"{where} cites unknown asset {ref.asset_id}")
        else:
            out += [
                f"{where} cites unknown segment {x}" for x in ref.segment_ids if x not in segments
            ]
    if not ref.segment_ids:
        out.append(f"{where} cites no segment")
    for t in (ref.start, ref.end):
        if t is not None and (not math.isfinite(t) or t < 0):
            out.append(f"{where} has an invalid time")
    if (ref.start is None) != (ref.end is None):
        out.append(f"{where} has only one end of its time range")
    elif ref.start is not None and ref.end is not None and ref.end < ref.start:
        out.append(f"{where} ends before it starts")
    return out


def _sources(plan: StoryPlan, known: Mapping[str, frozenset[str]] | None) -> list[str]:
    out: list[str] = []
    assets = {a.asset_id for a in plan.source_assets}
    refs: list[tuple[str, SourceRef]] = []
    for s in plan.sections:
        refs += [(f"section {s.section_id}", r) for r in s.sources]
    refs += [(f"beat {b.beat_id}", b.source) for b in plan.beats]
    refs += [(f"opening {o.candidate_id}", o.source) for o in plan.candidate_openings]
    refs += [(f"key information {k.info_id}", k.source) for k in plan.key_information]
    refs += [(f"evidence {e.link_id}", e.support) for e in plan.supporting_evidence]
    refs += [(f"visual {v.visual_id}", v.source) for v in plan.visual_opportunities]
    for x in plan.setup_payoff_links:
        refs.append((f"link {x.link_id} setup", x.setup))
        if x.payoff is not None:
            refs.append((f"link {x.link_id} payoff", x.payoff))
    for o in plan.candidate_openings:
        if o.context_required is not None:
            refs.append((f"opening {o.candidate_id} context", o.context_required))
    for where, ref in refs:
        if ref.asset_id not in assets:
            out.append(f"{where} cites asset {ref.asset_id} that is not a source of the plan")
        out += _check_ref(ref, where, known)
    return out


def _scores(plan: StoryPlan) -> list[str]:
    out: list[str] = []
    if plan.plan_version < 1:
        out.append("plan_version must be at least 1")
    if plan.supersedes is not None and plan.plan_version < 2:
        out.append("a plan that supersedes another must be version 2 or later")
    ranks = sorted(o.rank for o in plan.candidate_openings)
    if ranks != list(range(1, len(ranks) + 1)):
        out.append("opening ranks are not 1..n")
    return out
