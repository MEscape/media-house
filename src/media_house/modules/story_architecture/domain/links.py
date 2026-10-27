"""What the plan does with the relations between its recordings.

The relations come from ``asset_relationships`` (this module never detects any). They matter only
when a story spans several recordings, and they are used in proportion to how far they can be
relied on:

* RECORDED lineage (an exact copy, a derivative) is a fact. The copy's passages repeat the
  original's, so its sections become redundant candidates. That is advisory like every priority.
* A claim a person CONFIRMED is reported prominently (alternate takes: use one; parallel coverage),
  but nothing is dropped: which take to use is an editorial choice.
* Anything else (evidence-supported or unverified) is noted as a possibility and changes neither
  order nor priority. A hypothesis never becomes a constraint.
* A rejected or out-of-date claim is listed, labelled not usable, and never acted on.

Certainty is carried through unchanged: a claim that appears in a plan has not become a fact.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from media_house.modules.story_architecture.domain.facts import AssetFacts, AssetLinkFacts
from media_house.modules.story_architecture.domain.model import AssetRelation
from media_house.modules.story_architecture.domain.sections import Draft
from media_house.modules.story_architecture.domain.values import (
    Certainty,
    IssueKind,
    Severity,
    Treatment,
)

_COPY = "exact_duplicate_of"
_DERIVED = frozenset({"derived_from", "proxy_of"})
_TAKES = "alternate_take_of"
_OVERLAP = "temporally_overlaps_with"
_PHRASE = {
    _COPY: "exact copies",
    "derived_from": "the same recording",
    "proxy_of": "the same recording",
    _TAKES: "alternate takes of one scene",
    _OVERLAP: "recordings of the same time span",
}


@dataclass(frozen=True, slots=True)
class IssueSpec:
    kind: IssueKind
    severity: Severity
    detail: str
    related: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinkEffects:
    relations: tuple[AssetRelation, ...] = ()
    #: asset id -> why all of its sections are redundant candidates.
    redundant: Mapping[str, str] | None = None
    issues: tuple[IssueSpec, ...] = ()


def certainty_of(link: AssetLinkFacts) -> Certainty:
    if link.status in ("rejected", "stale"):
        return Certainty.NOT_USABLE
    if link.status is None and link.grade == "recorded":
        return Certainty.RECORDED
    if link.status == "confirmed":
        return Certainty.CONFIRMED
    if link.grade in ("corroborated", "indicated"):
        return Certainty.EVIDENCE_SUPPORTED
    return Certainty.UNVERIFIED


def apply_links(
    links: tuple[AssetLinkFacts, ...], assets: tuple[AssetFacts, ...], drafts: tuple[Draft, ...]
) -> LinkEffects:
    """The effect of each relation whose two ends are both in the story."""
    names = {a.asset_id: a.name for a in assets}
    sections_of: dict[str, tuple[str, ...]] = {}
    for d in drafts:
        sections_of[d.asset_id] = (*sections_of.get(d.asset_id, ()), d.section_id)
    relations: list[AssetRelation] = []
    redundant: dict[str, str] = {}
    issues: list[IssueSpec] = []
    for link in sorted(links, key=lambda x: x.relationship_id):
        if link.source_asset not in names or link.target_asset not in names:
            continue
        certainty = certainty_of(link)
        src, tgt = names[link.source_asset], names[link.target_asset]
        treatment = Treatment.NOT_USED
        effect = "rejected or out of date: not used"
        affected: tuple[str, ...] = ()
        pair = (link.relationship_id,)
        if certainty is Certainty.RECORDED and link.relation == _COPY:
            treatment, affected = Treatment.REDUNDANT_COPY, sections_of[link.source_asset]
            effect = f"'{src}' is an exact copy of '{tgt}': its passages repeat the original's"
            redundant.setdefault(link.source_asset, f"exact copy of '{tgt}' (library record)")
            issues.append(IssueSpec(IssueKind.REDUNDANT_MATERIAL, Severity.INFO, effect, pair))
        elif certainty is Certainty.RECORDED and link.relation in _DERIVED:
            treatment, affected = Treatment.SAME_RECORDING, sections_of[link.source_asset]
            effect = f"'{src}' was made from '{tgt}': both carry the same recorded content"
            redundant.setdefault(link.source_asset, f"made from '{tgt}' (library record)")
            issues.append(IssueSpec(IssueKind.REDUNDANT_MATERIAL, Severity.INFO, effect, pair))
        elif certainty is Certainty.CONFIRMED and link.relation == _TAKES:
            treatment = Treatment.ALTERNATE_TAKES
            effect = f"confirmed alternate takes: '{src}' and '{tgt}' cover one scene; use one"
            issues.append(IssueSpec(IssueKind.RELATED_RECORDINGS, Severity.WARNING, effect, pair))
        elif certainty is Certainty.CONFIRMED and link.relation == _OVERLAP:
            treatment = Treatment.PARALLEL_COVERAGE
            effect = (
                f"confirmed to cover the same time span: footage of '{src}' can illustrate "
                f"'{tgt}' and the reverse"
            )
            issues.append(IssueSpec(IssueKind.RELATED_RECORDINGS, Severity.INFO, effect, pair))
        elif certainty is not Certainty.NOT_USABLE:
            treatment = Treatment.NOTED
            effect = (
                f"'{src}' and '{tgt}' may be {_PHRASE.get(link.relation, link.relation)} "
                f"({certainty.value}, grade {link.grade}); not confirmed, so the plan did not act "
                "on it"
            )
            if link.contradicted:
                effect += "; its evidence disagrees with itself"
            issues.append(IssueSpec(IssueKind.RELATED_RECORDINGS, Severity.INFO, effect, pair))
        if link.outdated and certainty is Certainty.CONFIRMED:
            effect += "; the evidence has changed since it was confirmed"
        relations.append(
            AssetRelation(
                relationship_id=link.relationship_id,
                relation=link.relation,
                source_asset=link.source_asset,
                target_asset=link.target_asset,
                certainty=certainty,
                grade=link.grade,
                contradicted=link.contradicted,
                outdated=link.outdated,
                evidence=link.evidence,
                treatment=treatment,
                affected_sections=affected,
                effect=effect,
            )
        )
    return LinkEffects(tuple(relations), redundant, tuple(issues))
