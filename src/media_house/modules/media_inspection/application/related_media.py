"""Use case: which other assets relate to this one (duplicates, proxies, derivatives, companions).

Evaluated against the library as it is NOW and never stored: relationships change whenever
media is added or removed. Uncertain relationships are returned as candidates with a confidence.
"""

from dataclasses import dataclass

from media_house.modules.media_inspection.domain.relations import (
    MediaSummary,
    RelationCandidate,
    find_relations,
)
from media_house.modules.media_library.application.contracts import (
    AssetStatus,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.shared.errors import Err, Ok, Result

#: Candidates are looked for among at most this many audio and this many video assets.
SEARCH_LIMIT = MediaQuery.MAX_PAGE_SIZE
_MEDIA = frozenset({MediaType.AUDIO, MediaType.VIDEO})


@dataclass(frozen=True, slots=True)
class RelatedMedia:
    asset_id: str
    candidates: tuple[RelationCandidate, ...]
    #: ``True`` when the library holds more audio/video than was compared.
    truncated: bool = False


def summarize(asset: MediaAssetDto) -> MediaSummary:
    """The attributes of a library asset that relationships are judged by."""
    technical = asset.technical
    container = technical.get("container")
    video_codec = technical.get("video_codec")
    is_video = asset.media_type is MediaType.VIDEO
    return MediaSummary(
        asset_id=asset.id,
        checksum=asset.checksum,
        has_video=is_video,
        has_audio=asset.media_type is MediaType.AUDIO
        or (is_video and bool(technical.get("audio_stream_count"))),
        duration=asset.duration_seconds,
        width=asset.width,
        height=asset.height,
        container=tuple(str(c) for c in container) if isinstance(container, list) else (),
        video_codec=video_codec if isinstance(video_codec, str) else None,
        source_asset_id=asset.derivation.source_asset_id if asset.derivation else None,
    )


class FindRelatedMedia:
    """Compares an asset with the rest of the library, using only what the library records."""

    def __init__(self, library: MediaLibrary) -> None:
        self._library = library

    def execute(self, asset_id: str) -> Result[RelatedMedia, MediaError]:
        subject = self._library.get(asset_id)
        if isinstance(subject, Err):
            return subject
        if subject.value.media_type not in _MEDIA:
            return Ok(RelatedMedia(asset_id, ()))

        pool: dict[str, MediaAssetDto] = {}
        truncated = False
        for media_type in (MediaType.VIDEO, MediaType.AUDIO):
            page = self._library.search(
                MediaQuery(media_types=frozenset({media_type}), page_size=SEARCH_LIMIT)
            )
            truncated = truncated or page.total > len(page.items)
            pool.update({a.id: a for a in page.items})
        # recorded relatives are facts even when they fall outside the searched page
        derived = self._library.list_derived(asset_id)
        pool.update({a.id: a for a in derived if a.media_type in _MEDIA})
        if subject.value.derivation is not None:
            source = self._library.get(subject.value.derivation.source_asset_id)
            if isinstance(source, Ok) and source.value.media_type in _MEDIA:
                pool[source.value.id] = source.value

        candidates = find_relations(
            summarize(subject.value),
            [summarize(a) for a in pool.values() if a.status is AssetStatus.ACTIVE],
        )
        return Ok(RelatedMedia(asset_id, candidates, truncated))
