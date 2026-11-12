"""Reaction pictures: the owner's tagged collection, uploads, and good moments from the footage.

* Collection: library pictures tagged ``reaction`` (an upload here gets the tag, so the
  collection grows by itself and every later video can reuse it).
* Footage: shots ranked from the STORED video analyses (nothing is analysed here); the best ones
  are taken as exact frames through visual asset generation (cached there), for the person to pick.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from media_house.core.domain import Rational
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.thumbnail_design.domain.reactions import ReactionMoment, ShotCue, rank
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis, VideoCatalog
from media_house.modules.visual_asset_generation.application.contracts import (
    FrameError,
    FrameGrabber,
    GrabFrameCommand,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result

REACTION_TAG: Final = "reaction"
DEFAULT_SUGGESTIONS: Final = 6


@dataclass(frozen=True, slots=True)
class ReactionCandidate:
    """A frame of the footage that would make a good reaction picture."""

    picture: MediaAssetDto
    moment: ReactionMoment


class TaggedReactions:
    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def list(self) -> tuple[MediaAssetDto, ...]:
        page = self._library.search(
            MediaQuery(
                media_types=frozenset({MediaType.IMAGE}),
                tags=frozenset({REACTION_TAG}),
                page_size=MediaQuery.MAX_PAGE_SIZE,
            )
        )
        return tuple(page.items)

    def add(self, path: Path) -> Result[MediaAssetDto, MediaError]:
        """Import a picture into the collection (a duplicate is the stored picture, now tagged)."""
        imported = self._library.import_file(path, tags=(REACTION_TAG,))
        if isinstance(imported, Err):
            return imported
        return self.mark(imported.value.asset.id)

    def mark(self, asset_id: str) -> Result[MediaAssetDto, MediaError]:
        """Put a library picture (an upload, a footage frame) into the collection."""
        return self._library.add_tags(asset_id, (REACTION_TAG,))


class SuggestReactions:
    def __init__(self, library: MediaLibrary, videos: VideoCatalog, frames: FrameGrabber) -> None:
        self._library = library
        self._videos = videos
        self._frames = frames

    def cues(self, video_asset_ids: Sequence[str]) -> Result[tuple[ShotCue, ...], MediaError]:
        cues: list[ShotCue] = []
        for asset_id in dict.fromkeys(video_asset_ids):
            stored = self._videos.latest(asset_id)
            if isinstance(stored, Err):
                return stored
            if stored.value is not None:
                analysis, _ = stored.value
                asset = self._library.get(asset_id)
                name = asset.value.display_name if isinstance(asset, Ok) else ""
                cues.extend(_cues(analysis, name))
        return Ok(tuple(cues))

    def execute(
        self, video_asset_ids: Sequence[str], ctx: JobContext, limit: int = DEFAULT_SUGGESTIONS
    ) -> Result[tuple[ReactionCandidate, ...], MediaError | FrameError]:
        """The best moments as pictures; videos without a stored analysis offer none."""
        cues = self.cues(video_asset_ids)
        if isinstance(cues, Err):
            return cues
        moments = rank(cues.value, limit)
        found: list[ReactionCandidate] = []
        for done, moment in enumerate(moments):
            ctx.raise_if_cancelled()
            ctx.progress.report(done, len(moments), "Reaction frames")
            grab = self._frames.execute(
                GrabFrameCommand(
                    video_asset_id=moment.asset_id,
                    source_time=Rational(round(moment.seconds * 1000), 1000),
                ),
                ctx,
            )
            if isinstance(grab, Err):
                return grab
            found.append(ReactionCandidate(grab.value.asset, moment))
        return Ok(tuple(found))


def _cues(analysis: VideoAnalysis, name: str) -> list[ShotCue]:
    cues = []
    for shot in analysis.shots:
        faces = shot.faces
        cues.append(
            ShotCue(
                asset_id=analysis.asset_id,
                asset_name=name,
                shot_id=shot.shot_id,
                start_seconds=shot.range.start.seconds,
                end_seconds=shot.range.end.seconds,
                face_frames=faces.face_frames,
                face_height=faces.mean_face_height,
                face_sharpness=faces.face_sharpness,
                eyes_closed=faces.eyes_closed,
                smile=faces.smile_cue,
                mouth_activity=faces.visual_speaking,
            )
        )
    return cues
