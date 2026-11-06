"""The audio structure of the Resolve timeline, for ``audio_mix_engine``.

The mix engine owns mixing and mastering; it must not reach into Resolve around this adapter. This
is the read side of their contract: which Resolve track carries which role, which clip of the
timeline is which sound (and which Resolve item it is now), where it comes from. The mix
instructions themselves stay in the built timeline (``timeline_builder``); this only says where
they act in Resolve. Nothing is read from Resolve and nothing is changed.
"""

from dataclasses import dataclass

from media_house.modules.media_library.application.contracts import MediaError
from media_house.modules.resolve_adapter.application.store import ResolveStore
from media_house.modules.resolve_adapter.domain.errors import NothingBound
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimelineCatalog,
    TrackKind,
)
from media_house.shared.errors import Err, Ok, Result


@dataclass(frozen=True, slots=True)
class AudioTrackRef:
    track_id: str
    role: str  # dialogue | production | music | ambience | sfx
    resolve_index: int  # Resolve's audio track number, 1 = bottom
    name: str


@dataclass(frozen=True, slots=True)
class AudioClipRef:
    element_id: str
    track_id: str
    role: str
    #: Resolve's id of the item as last synchronised ("" if it was never placed).
    resolve_item_id: str
    asset_id: str | None
    original_asset_id: str | None
    source_in_seconds: str
    source_out_seconds: str
    start_sample: int
    end_sample: int
    sample_rate: int
    mix_ids: tuple[str, ...]
    video_link: str | None


@dataclass(frozen=True, slots=True)
class AudioLayout:
    project_name: str
    timeline_name: str
    tracks: tuple[AudioTrackRef, ...]
    clips: tuple[AudioClipRef, ...]
    #: Instructions for the mix, by id (they live in the built timeline).
    mix_ids: tuple[str, ...]

    def clips_of(self, role: str) -> tuple[AudioClipRef, ...]:
        return tuple(c for c in self.clips if c.role == role)


class ResolveAudioLayouts:
    def __init__(self, catalog: BuiltTimelineCatalog, store: ResolveStore) -> None:
        self._catalog = catalog
        self._store = store

    def layout(
        self, anchor_asset_id: str, *, timeline_id: str | None = None
    ) -> Result[AudioLayout, MediaError | NothingBound]:
        latest = self._catalog.latest(anchor_asset_id, timeline_id=timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingBound())
        timeline = latest.value[0]
        bound = self._store.latest_binding(anchor_asset_id, timeline.timeline_id)
        if isinstance(bound, Err):
            return bound
        if bound.value is None:
            return Err(NothingBound())
        state = bound.value[0]
        item_of = {e.element_id: e.item_id for e in state.elements}
        ordered = sorted(
            (t for t in timeline.tracks if t.kind is TrackKind.AUDIO),
            key=lambda t: (t.index, t.lane, t.track_id),
        )
        tracks = tuple(
            AudioTrackRef(t.track_id, t.role.value, position, t.name)
            for position, t in enumerate(ordered, start=1)
        )
        clips = tuple(
            AudioClipRef(
                element_id=c.clip_id,
                track_id=t.track_id,
                role=c.role.value,
                resolve_item_id=item_of.get(c.clip_id, ""),
                asset_id=c.source.source_id if c.source else None,
                original_asset_id=c.source.original_asset_id if c.source else None,
                source_in_seconds=f"{c.source.source_in.numerator}/{c.source.source_in.denominator}"
                if c.source
                else "0/1",
                source_out_seconds=f"{c.source.source_out.numerator}/{c.source.source_out.denominator}"
                if c.source
                else "0/1",
                start_sample=c.start_sample,
                end_sample=c.end_sample,
                sample_rate=c.sample_rate,
                mix_ids=c.mix_ids,
                video_link=c.video_link,
            )
            for t in ordered
            for c in t.audio
        )
        return Ok(
            AudioLayout(
                project_name=state.project.project_name,
                timeline_name=state.project.timeline_name,
                tracks=tracks,
                clips=clips,
                mix_ids=tuple(m.mix_id for m in timeline.mix),
            )
        )
