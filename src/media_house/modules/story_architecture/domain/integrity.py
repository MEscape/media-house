"""Claims, their support, and what could go wrong with them (factual integrity).

This module can say what the speaker STATED and whether the recording backs it up with another
passage. It never says a statement is true or false: ``verification`` is always ``unverified``,
and an "unsupported" claim means only that no supporting passage was found in these recordings.
"""

from media_house.modules.story_architecture.domain.facts import ItemFacts, RelationFacts
from media_house.modules.story_architecture.domain.model import (
    CompletenessIssue,
    EvidenceLink,
    KeyInformation,
    OrderingConstraint,
    SetupPayoffLink,
    SourceRef,
)
from media_house.modules.story_architecture.domain.profile import Thresholds
from media_house.modules.story_architecture.domain.sections import Draft, excerpt
from media_house.modules.story_architecture.domain.structure import Index, Structure
from media_house.modules.story_architecture.domain.values import (
    Basis,
    BeatFunction,
    ClaimKind,
    ConstraintKind,
    IssueKind,
    LinkState,
    Origin,
    Priority,
    Severity,
    Strength,
    SupportRelation,
)

_KIND: dict[str, ClaimKind] = {
    "fact": ClaimKind.STATED_FACT,
    "claim": ClaimKind.CLAIM,
    "argument": ClaimKind.CLAIM,
    "conclusion": ClaimKind.CLAIM,
    "recommendation": ClaimKind.OPINION,
    "decision": ClaimKind.OPINION,
    "caveat": ClaimKind.QUALIFICATION,
}
_SUPPORT: dict[str, SupportRelation] = {
    "supports": SupportRelation.SUPPORTS,
    "explains": SupportRelation.EXPLAINS,
    "illustrates": SupportRelation.ILLUSTRATES,
}
_EVIDENCE_FUNCTIONS = frozenset(
    {BeatFunction.EVIDENCE, BeatFunction.DEMONSTRATION, BeatFunction.EXAMPLE}
)
#: A claim of these kinds is expected to be backed by another passage; an opinion is not.
_NEEDS_SUPPORT = frozenset({ClaimKind.STATED_FACT, ClaimKind.CLAIM})


class Integrity:
    """Key information, evidence links and the issues found while building them."""

    def __init__(self, index: Index, thresholds: Thresholds) -> None:
        self._index = index
        self._t = thresholds
        self.information: list[KeyInformation] = []
        self.evidence: list[EvidenceLink] = []
        self.issues: list[CompletenessIssue] = []

    # --- issues ---------------------------------------------------------------------------
    def issue(
        self,
        kind: IssueKind,
        severity: Severity,
        detail: str,
        *,
        section: str | None = None,
        source: SourceRef | None = None,
        related: tuple[str, ...] = (),
    ) -> None:
        self.issues.append(
            CompletenessIssue(
                issue_id=f"is_{len(self.issues) + 1:03d}",
                kind=kind,
                severity=severity,
                section_id=section,
                detail=detail,
                source=source,
                related=related,
            )
        )

    # --- claims and support ---------------------------------------------------------------
    def read_claims(self) -> None:
        for asset_index, asset in enumerate(self._index.assets):
            supports_of: dict[str, list[RelationFacts]] = {}
            for rel in asset.relations:
                if rel.kind in _SUPPORT:
                    supports_of.setdefault(rel.target_segment, []).append(rel)
            for item in asset.items:
                self._claim(asset_index, asset.asset_id, item, supports_of)
            for draft in self._index.drafts:
                if draft.asset_id != asset.asset_id:
                    continue
                for segment in draft.segments:
                    if segment.has_role("demonstration"):
                        ref = self._index.ref_of_segment(asset.asset_id, segment.segment_id)
                        if ref is not None:
                            self.information.append(
                                KeyInformation(
                                    info_id=f"ki_{asset_index}_d{segment.segment_id}",
                                    section_id=draft.section_id,
                                    kind=ClaimKind.DEMONSTRATION,
                                    text=excerpt(segment.text),
                                    origin=Origin.DIRECT,
                                    source=ref,
                                    verification="unverified",
                                    priority=self._priority(segment.importance),
                                )
                            )

    def _priority(self, importance: float | None) -> Priority:
        if importance is None:
            return Priority.SUPPORTING
        if importance >= self._t.essential_importance:
            return Priority.ESSENTIAL
        if importance < self._t.optional_importance:
            return Priority.OPTIONAL
        return Priority.SUPPORTING

    def _claim(
        self,
        asset_index: int,
        asset_id: str,
        item: ItemFacts,
        supports_of: dict[str, list[RelationFacts]],
    ) -> None:
        kind = _KIND.get(item.kind)
        if kind is None or item.segment_id is None:
            return
        section_id = self._index.section_of(asset_id, item.segment_id)
        ref = self._index.ref_of_units(asset_id, item.unit_ids) or self._index.ref_of_segment(
            asset_id, item.segment_id
        )
        if section_id is None or ref is None:
            return
        segment = self._index.draft(section_id).segments
        importance = next((s.importance for s in segment if s.segment_id == item.segment_id), None)
        info_id = f"ki_{asset_index}_{item.item_id}"
        origin = {o.value: o for o in Origin}.get(item.origin, Origin.INFERENCE)
        links: list[str] = []
        for rel in supports_of.get(item.segment_id, ()):
            support = self._index.ref_of_segment(asset_id, rel.source_segment)
            if support is None:
                continue
            links.append(self._link(info_id, _SUPPORT[rel.kind], support, Basis.DIRECT, section_id))
        if not links:
            links.extend(self._nearby_support(info_id, section_id, item.segment_id))
        self.information.append(
            KeyInformation(
                info_id=info_id,
                section_id=section_id,
                kind=kind,
                text=item.text,
                origin=origin,
                source=ref,
                verification="unverified",
                priority=self._priority(importance),
                qualified_by=item.qualified_by,
                support_ids=tuple(links),
            )
        )

    def _nearby_support(self, info_id: str, section_id: str, own_segment: str) -> list[str]:
        """Evidence, example or demonstration in the same section (an inferred link)."""
        out: list[str] = []
        draft = self._index.draft(section_id)
        for segment in draft.segments:
            if segment.segment_id == own_segment:
                continue
            roles = {"supporting_evidence", "example", "demonstration", "anecdote"}
            if segment.has_role(*roles):
                ref = self._index.ref_of_segment(draft.asset_id, segment.segment_id)
                if ref is not None:
                    relation = (
                        SupportRelation.ILLUSTRATES
                        if segment.has_role("example", "demonstration", "anecdote")
                        else SupportRelation.SUPPORTS
                    )
                    out.append(self._link(info_id, relation, ref, Basis.INFERRED, section_id))
        return out

    def _link(
        self,
        info_id: str,
        relation: SupportRelation,
        support: SourceRef,
        basis: Basis,
        section_id: str,
    ) -> str:
        link_id = f"ev_{len(self.evidence) + 1:03d}"
        self.evidence.append(
            EvidenceLink(
                link_id=link_id,
                info_id=info_id,
                relation=relation,
                support=support,
                basis=basis,
                section_id=section_id,
            )
        )
        return link_id

    # --- findings -------------------------------------------------------------------------
    def credibility(self) -> dict[tuple[str, str], tuple[float, str]]:
        """Passages whose claim lacks support or is contradicted, for judging an opening."""
        weak: dict[tuple[str, str], tuple[float, str]] = {}
        for info in self.information:
            if (
                info.kind in _NEEDS_SUPPORT
                and not info.support_ids
                and info.priority is not Priority.OPTIONAL
            ):
                for seg in info.source.segment_ids:
                    weak[(info.source.asset_id, seg)] = (
                        0.5,
                        "its claim has no supporting passage in the recording",
                    )
        for issue in self.issues:
            if issue.kind is IssueKind.CONTRADICTION and issue.source is not None:
                for seg in issue.source.segment_ids:
                    weak[(issue.source.asset_id, seg)] = (0.2, "it contradicts another passage")
        return weak

    def find_unsupported(self) -> None:
        reported: set[tuple[str, tuple[str, ...]]] = set()
        for info in self.information:
            if info.kind not in _NEEDS_SUPPORT or info.support_ids:
                continue
            if info.priority is Priority.OPTIONAL:
                continue
            # Two items of one passage (a claim and a fact) are one finding, not two.
            where = (info.source.asset_id, info.source.segment_ids)
            if where in reported:
                continue
            reported.add(where)
            essential = info.priority is Priority.ESSENTIAL
            self.issue(
                IssueKind.UNSUPPORTED_CLAIM,
                Severity.WARNING if essential else Severity.INFO,
                f"No supporting passage was found in the recording for: {excerpt(info.text, 100)}",
                section=info.section_id,
                source=info.source,
                related=(info.info_id,),
            )

    def find_relations(self) -> None:
        for asset in self._index.assets:
            for rel in asset.relations:
                a = self._index.section_of(asset.asset_id, rel.source_segment)
                b = self._index.section_of(asset.asset_id, rel.target_segment)
                if a is None or b is None:
                    continue
                first = self._index.ref_of_segment(asset.asset_id, rel.source_segment)
                if rel.kind == "contradicts":
                    self.issue(
                        IssueKind.CONTRADICTION,
                        Severity.HIGH,
                        f"'{self._label(a)}' contradicts '{self._label(b)}'; both are kept and "
                        "neither is judged correct",
                        section=a,
                        source=first,
                        related=(b,),
                    )
                elif rel.kind in ("repeats", "overlaps") and a != b:
                    added = f" and adds {rel.adds.replace('_', ' ')}" if rel.adds else ""
                    self.issue(
                        IssueKind.REDUNDANT_MATERIAL,
                        Severity.INFO,
                        f"'{self._label(a)}' {rel.kind} '{self._label(b)}'{added}",
                        section=a,
                        source=first,
                        related=(b,),
                    )

    def find_open_links(self, links: tuple[SetupPayoffLink, ...]) -> None:
        for link in links:
            if link.state is LinkState.OPEN:
                self.issue(
                    IssueKind.UNANSWERED_QUESTION,
                    Severity.WARNING,
                    "A question is raised and the recording does not answer it",
                    section=link.setup_section,
                    source=link.setup,
                )

    def check_order(
        self,
        order: tuple[str, ...],
        structure: Structure,
        forward: list[OrderingConstraint],
    ) -> None:
        """Report what the final order breaks: separated pairs, forward dependencies, cycles."""
        position = {s: i for i, s in enumerate(order)}
        for c in structure.constraints:
            if c.kind is ConstraintKind.KEEP_ADJACENT:
                gap = position[c.second] - position[c.first]
                if gap != 1 and c.strength is Strength.HARD:
                    self.issue(
                        IssueKind.DISTORTION_RISK,
                        Severity.HIGH,
                        f"{c.reason}, but the recommended order separates them",
                        section=c.second,
                        related=(c.first,),
                    )
        for c in forward:
            self.issue(
                IssueKind.CONTEXT_REMOVAL_RISK,
                Severity.WARNING,
                f"{c.reason}, but this format keeps the recorded order",
                section=c.second,
                related=(c.first,),
            )
        for first, second, relation in structure.dropped:
            self.issue(
                IssueKind.DISTORTION_RISK,
                Severity.WARNING,
                f"'{self._label(first)}' and '{self._label(second)}' depend on each other "
                f"({relation}); the recorded order of the two is kept",
                section=second,
                related=(first,),
            )

    def _label(self, section_id: str) -> str:
        draft: Draft = self._index.draft(section_id)
        return draft.label
