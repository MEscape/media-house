"""What the review needs to know about a timeline: which version it is and how its sections look.

The orchestrator never owns a timeline. ``TimelineRef`` names one exact build of the timeline
builder (id, version number and the hash of its content); ``TimelineFacts`` adds a fingerprint per
section so a change can be told apart from a rebuild that changed nothing. The application maps a
built timeline into these (``application/views.py``); this domain imports no other module.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimelineRef:
    """One exact version of a timeline. Two refs with the same ``content_hash`` are the same video
    even when a rebuild gave them different version numbers."""

    timeline_id: str
    version: int
    content_hash: str
    anchor_asset_id: str
    #: The stored build document: an immutable snapshot of this version.
    build_asset_id: str | None = None

    def same_content(self, other: "TimelineRef") -> bool:
        return self.timeline_id == other.timeline_id and self.content_hash == other.content_hash


@dataclass(frozen=True, slots=True)
class SectionFacts:
    section_id: str
    name: str
    start_frame: int
    end_frame: int
    #: sha256 of everything on the timeline inside the section.
    digest: str


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    ref: TimelineRef
    width: int
    height: int
    frame_rate: str
    duration_frames: int
    sections: tuple[SectionFacts, ...]

    def section(self, section_id: str) -> SectionFacts | None:
        return next((s for s in self.sections if s.section_id == section_id), None)


@dataclass(frozen=True, slots=True)
class ChangeAssessment:
    """What changed between two versions, for deciding which decisions still stand."""

    #: The video differs (a rebuild with identical content is not a change).
    material: bool
    changed_sections: tuple[str, ...] = ()
    added_sections: tuple[str, ...] = ()
    removed_sections: tuple[str, ...] = ()
    output_changed: bool = False
    duration_changed: bool = False
    opening_changed: bool = False
    ending_changed: bool = False

    @property
    def touched_sections(self) -> tuple[str, ...]:
        return (*self.changed_sections, *self.removed_sections)

    def reasons(self) -> tuple[str, ...]:
        found = []
        if self.changed_sections:
            found.append("sections changed: " + ", ".join(self.changed_sections))
        if self.added_sections:
            found.append("sections added: " + ", ".join(self.added_sections))
        if self.removed_sections:
            found.append("sections removed: " + ", ".join(self.removed_sections))
        if self.output_changed:
            found.append("output format changed")
        if self.duration_changed:
            found.append("duration changed")
        if self.opening_changed:
            found.append("opening changed")
        if self.ending_changed:
            found.append("ending changed")
        if self.material and not found:
            found.append("content outside the sections changed")
        return tuple(found)


def assess_change(old: TimelineFacts | None, new: TimelineFacts) -> ChangeAssessment:
    """Compare two versions. ``None`` (nothing bound before) is a first binding, not a change."""
    if old is None or old.ref.same_content(new.ref):
        return ChangeAssessment(material=False)
    before = {s.section_id: s for s in old.sections}
    after = {s.section_id: s for s in new.sections}
    changed = tuple(
        sid
        for sid, section in after.items()
        if sid in before and before[sid].digest != section.digest
    )
    added = tuple(sid for sid in after if sid not in before)
    removed = tuple(sid for sid in before if sid not in after)
    first_changed = bool(old.sections and new.sections) and (
        old.sections[0].section_id != new.sections[0].section_id
        or old.sections[0].digest != new.sections[0].digest
    )
    last_changed = bool(old.sections and new.sections) and (
        old.sections[-1].section_id != new.sections[-1].section_id
        or old.sections[-1].digest != new.sections[-1].digest
    )
    return ChangeAssessment(
        material=True,
        changed_sections=changed,
        added_sections=added,
        removed_sections=removed,
        output_changed=(old.width, old.height, old.frame_rate)
        != (new.width, new.height, new.frame_rate),
        duration_changed=old.duration_frames != new.duration_frames,
        opening_changed=first_changed,
        ending_changed=last_changed,
    )
