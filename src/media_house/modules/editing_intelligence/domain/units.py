"""The sentences an edit decides about, in the order the story puts them.

The story plan says which sections exist, in which order, and which passages (by sentence id)
each one holds. This module turns that into one ordered list of ``EditUnit``: a sentence with its
section, its story function, its spoken extent and its place in the recording. A recording with no
content analysis still gets units: each story beat becomes one coarse unit (less precise, and every
decision about it is a suggestion, never executable).
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    ContentFacts,
    RefFact,
    SegmentFact,
    StoryFacts,
    UnitFact,
)
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.speech import (
    AudioIndex,
    Extent,
    is_pure_filler,
    unit_extent,
)
from media_house.modules.editing_intelligence.domain.values import EvidenceKind


@dataclass(frozen=True, slots=True)
class EditUnit:
    asset_id: str
    asset_index: int
    unit: UnitFact
    segment: SegmentFact | None
    section_id: str
    priority: str
    function: str | None
    beat_id: str | None
    extent: Extent | None
    #: Position in the final (story) order.
    order: int
    #: Position among the recording's sentences; neighbours are source-adjacent.
    source_pos: int
    #: True for a coarse unit made from a story beat (no sentence-level analysis).
    coarse: bool = False

    @property
    def key(self) -> str:
        return f"{self.asset_index}:{self.unit.unit_id}"

    @property
    def decision_id(self) -> str:
        return f"dec:{self.key}"

    @property
    def pure_filler(self) -> bool:
        return is_pure_filler(self.unit.text)


class _Lookup:
    """One recording's sentences and segments by id (the story names them by id)."""

    def __init__(self, content: ContentFacts) -> None:
        self.units = {u.unit_id: u for u in content.units}
        self.position = {u.unit_id: i for i, u in enumerate(content.units)}
        self.segments = {s.segment_id: s for s in content.segments}


def collect_units(
    story: StoryFacts,
    evidence: Mapping[str, AssetEvidence],
    indexes: Mapping[str, AudioIndex],
) -> list[EditUnit]:
    asset_order = {asset_id: i for i, asset_id in enumerate(story.asset_ids)}
    beat_of_unit: dict[tuple[str, str], tuple[str, str]] = {}
    beat_of_segment: dict[tuple[str, str], tuple[str, str]] = {}
    for beat in story.beats:
        for uid in beat.source.unit_ids:
            beat_of_unit.setdefault((beat.source.asset_id, uid), (beat.beat_id, beat.function))
        for sid in beat.source.segment_ids:
            beat_of_segment.setdefault((beat.source.asset_id, sid), (beat.beat_id, beat.function))
    units: list[EditUnit] = []
    seen: set[tuple[str, str]] = set()
    lookups: dict[str, _Lookup] = {}
    for section in story.ordered_sections():
        picked: list[tuple[int, int, EditUnit]] = []
        for ref in section.sources:
            content = evidence[ref.asset_id].content if ref.asset_id in evidence else None
            if content is None:
                picked.extend(
                    _coarse(story, section.section_id, section.priority, ref, asset_order)
                )
                continue
            positions = lookups.get(ref.asset_id)
            if positions is None:
                positions = lookups[ref.asset_id] = _Lookup(content)
            for segment_id in ref.segment_ids:
                segment = positions.segments.get(segment_id)
                if segment is None:
                    continue
                for uid in segment.unit_ids:
                    unit = positions.units.get(uid)
                    if unit is None or (ref.asset_id, uid) in seen:
                        continue
                    seen.add((ref.asset_id, uid))
                    beat_id, function = beat_of_unit.get(
                        (ref.asset_id, uid),
                        beat_of_segment.get((ref.asset_id, segment_id), (None, None)),
                    )
                    picked.append(
                        (
                            asset_order.get(ref.asset_id, 0),
                            positions.position[uid],
                            EditUnit(
                                asset_id=ref.asset_id,
                                asset_index=asset_order.get(ref.asset_id, 0),
                                unit=unit,
                                segment=segment,
                                section_id=section.section_id,
                                priority=section.priority,
                                function=function,
                                beat_id=beat_id,
                                extent=unit_extent(indexes.get(ref.asset_id), unit),
                                order=0,
                                source_pos=positions.position[uid],
                            ),
                        )
                    )
        picked.sort(key=lambda item: (item[0], item[1]))
        units.extend(item[2] for item in picked)
    return [_with_order(u, i) for i, u in enumerate(units)]


def _with_order(unit: EditUnit, order: int) -> EditUnit:
    return EditUnit(
        asset_id=unit.asset_id,
        asset_index=unit.asset_index,
        unit=unit.unit,
        segment=unit.segment,
        section_id=unit.section_id,
        priority=unit.priority,
        function=unit.function,
        beat_id=unit.beat_id,
        extent=unit.extent,
        order=order,
        source_pos=unit.source_pos,
        coarse=unit.coarse,
    )


def _coarse(
    story: StoryFacts,
    section_id: str,
    priority: str,
    ref: RefFact,
    asset_order: Mapping[str, int],
) -> list[tuple[int, int, EditUnit]]:
    """No content analysis: the section's beats of this recording, one unit each."""
    out: list[tuple[int, int, EditUnit]] = []
    for position, beat in enumerate(story.beats_of(section_id)):
        if beat.source.asset_id != ref.asset_id:
            continue
        unit = UnitFact(
            unit_id=beat.beat_id,
            segment_id=beat.source.segment_ids[0] if beat.source.segment_ids else beat.beat_id,
            start=beat.source.start,
            end=beat.source.end,
            time_basis="source" if beat.source.exact_time else "interpolated",
            text="",
            words=0,
            starts_clean=False,
            ends_clean=False,
        )
        out.append(
            (
                asset_order.get(ref.asset_id, 0),
                position,
                EditUnit(
                    asset_id=ref.asset_id,
                    asset_index=asset_order.get(ref.asset_id, 0),
                    unit=unit,
                    segment=None,
                    section_id=section_id,
                    priority=priority,
                    function=beat.function,
                    beat_id=beat.beat_id,
                    extent=unit_extent(None, unit),
                    order=0,
                    source_pos=position,
                    coarse=True,
                ),
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class Protection:
    """One hard constraint on a passage and the story fact it comes from."""

    reason: str
    kind: EvidenceKind
    ref: str


def protections(
    story: StoryFacts, units: list[EditUnit], profile: EditingProfile
) -> dict[str, list[Protection]]:
    """Hard constraints per unit key: why the passage may not be proposed for removal.

    These come from the story plan (key information, qualifications, needed context, question and
    answer, premise and payoff) and from the profile's protected story functions. A passage that
    is nothing but filler is never protected by a function: it carries no content to protect.
    """
    index = {(u.asset_id, u.unit.unit_id): u for u in units}
    found: defaultdict[str, list[Protection]] = defaultdict(list)

    def protect(
        asset_id: str, unit_ids: tuple[str, ...], reason: str, kind: EvidenceKind, ref: str
    ) -> None:
        for uid in unit_ids:
            target = index.get((asset_id, uid))
            if target is None or (target.pure_filler and kind is EvidenceKind.STORY_BEAT):
                continue
            item = Protection(reason, kind, ref)
            if item not in found[target.key]:
                found[target.key].append(item)

    for info in story.key_info:
        if info.priority == "essential":
            what = f"key information {info.info_id}"
            kind = EvidenceKind.STORY_KEY_INFORMATION
            protect(info.source.asset_id, info.source.unit_ids, what, kind, info.info_id)
            protect(
                info.source.asset_id,
                info.qualified_by,
                f"qualifies {what}",
                kind,
                info.info_id,
            )
    for need in story.contexts:
        if need.needed is not None:
            protect(
                need.needed.asset_id,
                need.needed.unit_ids,
                f"context that section {need.section_id} needs",
                EvidenceKind.STORY_CONSTRAINT,
                need.section_id,
            )
    for link in story.links:
        what = f"{link.kind} link {link.link_id}"
        protect(
            link.setup.asset_id,
            link.setup.unit_ids,
            what,
            EvidenceKind.STORY_CONSTRAINT,
            link.link_id,
        )
        if link.payoff is not None:
            protect(
                link.payoff.asset_id,
                link.payoff.unit_ids,
                what,
                EvidenceKind.STORY_CONSTRAINT,
                link.link_id,
            )
    for beat in story.beats:
        if beat.function in profile.protected_functions:
            protect(
                beat.source.asset_id,
                beat.source.unit_ids,
                f"profile {profile.name} keeps {beat.function} passages",
                EvidenceKind.STORY_BEAT,
                beat.beat_id,
            )
            protect(
                beat.source.asset_id,
                beat.qualified_by,
                f"qualifies {beat.function} {beat.beat_id}",
                EvidenceKind.STORY_BEAT,
                beat.beat_id,
            )
    return dict(found)
