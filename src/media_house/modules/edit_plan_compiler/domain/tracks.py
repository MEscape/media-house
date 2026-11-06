"""Track planning: a fixed, channel-wide track layout and first-fit lanes for overlaps.

Every timeline has the same standard tracks, so a project template carries from video to video
(V1 main picture, V2 cover footage, V3 graphics, V4 captions, V5 effects / transition overlays;
A1 dialogue, A2 production sound, A3 music, A4 ambience, A5 effects). Where two items of one role
overlap in time, the later one takes the next LANE of that role: a lane is inserted right after
the role's base track, so overlaps never share a track and the numbers only shift when needed.
The main picture never gets a second lane: an overlap there is a finding, not a layout.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.edit_plan_compiler.domain.model import Track
from media_house.modules.edit_plan_compiler.domain.values import (
    AUDIO_ROLE_ORDER,
    VIDEO_ROLE_ORDER,
    Composite,
    TrackKind,
    TrackRole,
)

_LABELS = {
    TrackRole.PRIMARY: "Primary footage",
    TrackRole.COVER: "Cover footage and B-roll",
    TrackRole.GRAPHICS: "Graphics",
    TrackRole.CAPTIONS: "Captions",
    TrackRole.EFFECTS: "Transition overlays and effects",
    TrackRole.DIALOGUE: "Dialogue",
    TrackRole.PRODUCTION: "Production sound",
    TrackRole.MUSIC: "Music",
    TrackRole.AMBIENCE: "Ambience",
    TrackRole.SFX: "Sound effects",
}
_COMPOSITE = {
    TrackRole.PRIMARY: Composite.NORMAL,
    TrackRole.COVER: Composite.NORMAL,
    TrackRole.GRAPHICS: Composite.ALPHA_OVER,
    TrackRole.CAPTIONS: Composite.ALPHA_OVER,
    TrackRole.EFFECTS: Composite.ALPHA_OVER,
}
#: Roles that never open a second lane (an overlap there is reported by validation).
_SINGLE_LANE = frozenset({TrackRole.PRIMARY})


def kind_of(role: TrackRole) -> TrackKind:
    return TrackKind.VIDEO if role in VIDEO_ROLE_ORDER else TrackKind.AUDIO


@dataclass(slots=True)
class TrackPlanner:
    """Collects items by role, assigns lanes, then names the tracks."""

    _lanes: dict[TrackRole, list[Fraction]] = field(default_factory=dict)

    def lane_for(self, role: TrackRole, start: Fraction, end: Fraction) -> int:
        """The first lane of ``role`` free at ``[start, end)``. Call in (start, end, id) order."""
        ends = self._lanes.setdefault(role, [])
        if role not in _SINGLE_LANE:
            for lane, last in enumerate(ends):
                if last <= start:
                    ends[lane] = max(last, end)
                    return lane + 1
        if role in _SINGLE_LANE and ends:
            ends[0] = max(ends[0], end)
            return 1
        ends.append(end)
        return len(ends)

    def tracks(self) -> tuple[Track, ...]:
        found: list[Track] = []
        for kind, order in (
            (TrackKind.VIDEO, VIDEO_ROLE_ORDER),
            (TrackKind.AUDIO, AUDIO_ROLE_ORDER),
        ):
            index = 0
            for role in order:
                for lane in range(1, max(1, len(self._lanes.get(role, ()))) + 1):
                    index += 1
                    found.append(
                        Track(
                            track_id=_id(kind, index),
                            kind=kind,
                            role=role,
                            index=index,
                            lane=lane,
                            composite=_COMPOSITE.get(role, Composite.NORMAL),
                            label=_LABELS[role] + (f" {lane}" if lane > 1 else ""),
                        )
                    )
        return tuple(found)


def _id(kind: TrackKind, index: int) -> str:
    return f"{'V' if kind is TrackKind.VIDEO else 'A'}{index}"


def track_id(tracks: tuple[Track, ...], role: TrackRole, lane: int) -> str:
    for track in tracks:
        if track.role is role and track.lane == lane:
            return track.track_id
    raise KeyError((role, lane))
