"""Where the story would benefit from a picture, and what is known to exist for it.

The opportunities come from content intelligence (what the words invite) and from the passages that
announce a demonstration. The shots alignment found for a passage are attached as evidence. No shot
is chosen, no graphic made: this says what a visual is FOR, so the editing stage can look.
"""

from media_house.modules.story_architecture.domain.facts import AssetFacts, ShotMatchFacts
from media_house.modules.story_architecture.domain.model import (
    Beat,
    VisualOpportunity,
    VisualRef,
)
from media_house.modules.story_architecture.domain.profile import Thresholds
from media_house.modules.story_architecture.domain.sections import excerpt
from media_house.modules.story_architecture.domain.structure import Index
from media_house.modules.story_architecture.domain.values import (
    BeatFunction,
    Necessity,
    VisualPurpose,
)

_MAX_SHOTS = 3
_BROLL: dict[str, VisualPurpose] = {
    "process_footage": VisualPurpose.DEMONSTRATION,
    "comparison_visual": VisualPurpose.COMPARISON,
    "archive_or_historical": VisualPurpose.EVIDENCE,
}
_GRAPHIC: dict[str, VisualPurpose] = {
    "comparison": VisualPurpose.COMPARISON,
    "process": VisualPurpose.EXPLAINER_GRAPHIC,
    "list": VisualPurpose.EXPLAINER_GRAPHIC,
    "key_number": VisualPurpose.EXPLAINER_GRAPHIC,
    "definition": VisualPurpose.EXPLAINER_GRAPHIC,
    "term_clarification": VisualPurpose.EXPLAINER_GRAPHIC,
    "quote": VisualPurpose.TEXT_ON_SCREEN,
    "name_and_role": VisualPurpose.TEXT_ON_SCREEN,
    "call_to_action": VisualPurpose.TEXT_ON_SCREEN,
}


def _purpose(kind: str, category: str) -> VisualPurpose:
    table = _BROLL if kind == "broll" else _GRAPHIC
    return table.get(category, VisualPurpose.ILLUSTRATION)


def _shots(asset: AssetFacts, segment_id: str) -> tuple[VisualRef, ...]:
    matches: list[ShotMatchFacts] = sorted(
        (m for m in asset.matches if m.passage_id == segment_id), key=lambda m: m.rank
    )
    return tuple(
        VisualRef(
            asset_id=asset.asset_id,
            shot_id=m.shot_id,
            start=m.start,
            end=m.end,
            grade=m.grade,
            rank=m.rank,
        )
        for m in matches[:_MAX_SHOTS]
    )


def build(
    index: Index,
    beats: tuple[Beat, ...],
    thresholds: Thresholds,
) -> tuple[VisualOpportunity, ...]:
    """Visual opportunities in recorded order, each tied to the section that carries it."""
    functions: dict[tuple[str, str], set[BeatFunction]] = {}
    for beat in beats:
        for seg in beat.source.segment_ids:
            functions.setdefault((beat.source.asset_id, seg), set()).add(beat.function)
    out: list[VisualOpportunity] = []
    covered: set[tuple[str, str]] = set()
    for asset in index.assets:
        for fact in asset.visuals:
            section = index.section_of(asset.asset_id, fact.segment_id)
            ref = index.ref_of_segment(asset.asset_id, fact.segment_id)
            if section is None or ref is None:
                continue
            roles = functions.get((asset.asset_id, fact.segment_id), set())
            needs = BeatFunction.DEMONSTRATION in roles or (
                BeatFunction.EVIDENCE in roles
                and fact.strength >= thresholds.visual_necessity_strength
            )
            covered.add((asset.asset_id, fact.segment_id))
            out.append(
                VisualOpportunity(
                    visual_id=f"vis_{len(out) + 1:03d}",
                    section_id=section,
                    purpose=_purpose(fact.kind, fact.category),
                    necessity=Necessity.ESSENTIAL if needs else Necessity.OPTIONAL,
                    concept=fact.concept,
                    source=ref,
                    rationale=fact.rationale,
                    strength=fact.strength,
                    already_covered=fact.already_covered,
                    available=_shots(asset, fact.segment_id),
                    origin_opportunity=fact.opportunity_id,
                )
            )
        # A passage that demonstrates something needs the thing shown, even when no analysis
        # suggested a visual for it.
        for draft in (d for d in index.drafts if d.asset_id == asset.asset_id):
            for segment in draft.segments:
                key = (asset.asset_id, segment.segment_id)
                if key in covered or not segment.has_role("demonstration"):
                    continue
                ref = index.ref_of_segment(asset.asset_id, segment.segment_id)
                if ref is None:
                    continue
                out.append(
                    VisualOpportunity(
                        visual_id=f"vis_{len(out) + 1:03d}",
                        section_id=draft.section_id,
                        purpose=VisualPurpose.DEMONSTRATION,
                        necessity=Necessity.ESSENTIAL,
                        concept=excerpt(segment.text, 80),
                        source=ref,
                        rationale="The passage demonstrates something, so the viewer has to see it",
                        strength=_role_confidence(segment.roles),
                        already_covered=False,
                        available=_shots(asset, segment.segment_id),
                        origin_opportunity="role:demonstration",
                    )
                )
    return tuple(out)


def _role_confidence(roles: tuple[tuple[str, float | None], ...]) -> float:
    return next((c for r, c in roles if r == "demonstration" and c is not None), 0.5)
