"""What the built timeline does, read for the timeline QC: nothing is rebuilt or re-measured.

The label spans are the clips in the ``sponsor_label`` zone, transitions are the built
transitions' kinds, overlays are the picture clips that sit above the picture (graphics and
custom clips; captions and the label itself do not count as clutter), and the used assets are the
originals the enabled clips cut from.
"""

from media_house.modules.production_project.domain.compliance import TimelineFacts
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    ClipKind,
    VideoClip,
)
from media_house.shared.errors import Err, NotFoundError, Ok, Result

_LABEL_ZONE = "sponsor_label"
_OVERLAY_KINDS = frozenset({ClipKind.GRAPHIC, ClipKind.CUSTOM})
_PICTURE_KINDS = frozenset({ClipKind.PRIMARY, ClipKind.VFX})


def facts_from_timeline(timeline: BuiltTimeline) -> TimelineFacts:
    rate = timeline.settings.frame_rate
    per_frame = rate.denominator / rate.numerator
    duration = timeline.settings.duration_frames * per_frame
    clips = [c for c in timeline.video_clips() if c.enabled]
    label = sorted(
        (c.start_frame * per_frame, c.end_frame * per_frame) for c in clips if _is_label(c)
    )
    used = {c.source.original_asset_id for c in clips if c.source}
    used |= {c.source.original_asset_id for c in timeline.audio_clips() if c.enabled and c.source}
    cues = timeline.subtitles.cues if timeline.subtitles else ()
    return TimelineFacts(
        duration_seconds=duration,
        label_spans=tuple(label),
        transitions=tuple(t.kind for t in timeline.transitions),
        max_simultaneous_overlays=_max_overlap([c for c in clips if _is_overlay(c)]),
        used_assets=tuple(sorted(used)),
        on_screen_texts=tuple(cue.text for cue in cues),
        cuts=max(0, sum(1 for c in clips if c.kind in _PICTURE_KINDS) - 1),
    )


def _is_label(clip: VideoClip) -> bool:
    return clip.zone == _LABEL_ZONE


def _is_overlay(clip: VideoClip) -> bool:
    return clip.kind in _OVERLAY_KINDS and not _is_label(clip)


def _max_overlap(clips: list[VideoClip]) -> int:
    """The most clips on screen at once (a clip ending where another starts does not overlap)."""
    edges = sorted(
        [(c.start_frame, 1) for c in clips] + [(c.end_frame, -1) for c in clips],
        key=lambda edge: (edge[0], edge[1]),
    )
    best = current = 0
    for _frame, change in edges:
        current += change
        best = max(best, current)
    return best


class BuiltTimelineSource:
    """Structurally implements ``TimelineSource``."""

    def __init__(self, catalog: BuiltTimelineCatalog) -> None:
        self._catalog = catalog

    def facts(self, anchor_asset_id: str) -> Result[TimelineFacts, NotFoundError]:
        found = self._catalog.latest(anchor_asset_id)
        if isinstance(found, Err) or found.value is None:
            return Err(
                NotFoundError(
                    f"No built timeline for {anchor_asset_id}",
                    user_message="Zu diesem Schnitt gibt es noch keine gebaute Timeline.",
                )
            )
        timeline, _asset = found.value
        return Ok(facts_from_timeline(timeline))


__all__ = ["BuiltTimelineSource", "facts_from_timeline"]
